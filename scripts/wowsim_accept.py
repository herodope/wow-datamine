#!/usr/bin/env python3
"""Acceptance tests for the wowsim tools (docs/wowsim.md, "Acceptance").

    python scripts/wowsim_accept.py                 # everything except the clean build
    python scripts/wowsim_accept.py --clean-build   # also build from a clean copy
    python scripts/wowsim_accept.py --skip-optimize # the fast checks only

Checks:
  build     build_sim.py --check is clean; bisrun sims the prot preset's own gear
  resim     each findings_nov18_2026 .gear.siml at 10k iterations, Undead, within
            1% of its recorded value
  compare   compare_items on the DPS T0.5 set, trinket 2: Heart of the Scale vs
            Heart of Wyrmthalak within +-1 DPS
  optimize  optimize(tps, t05 on, prot_v3, quick), started through the wowsim MCP
            server, finishes in under 5 min within 1% of 1619 TPS
  isolation wow-datamine query latency idle vs during that optimize; the wowsim
            server is killed right after starting the job, and the job and
            wow-datamine both keep working
  clean     (--clean-build) copy the repo's tracked and new files to a temp dir and
            run build_sim.py --setup-toolchains there; the Go/protoc zips are
            pre-seeded from vendor/toolchains/_dl (hash-checked as on download)

Exit status is the number of failed checks.
"""

import argparse
import asyncio
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from wowsim_tools import analysis, api, harness, jobs  # noqa: E402
from wowsim_tools.gear import Gear  # noqa: E402
from wowsim_tools.objective import parse  # noqa: E402

KIT = config.REPO_ROOT / "wowsim-kit" / "findings_nov18_2026"
RESIM = [  # file, metric, target
    ("v2_protpally_DTPS.gear.siml", "Dtps", 199.5),
    ("v3_protpally_TPS_T05.gear.siml", "Tps", 1619),
    ("v3_protpally_TPS_noT05.gear.siml", "Tps", 1581),
    ("v3_protpally_DPS_T05.gear.siml", "Dps", 669),
    ("v3_protpally_DPS_noT05.gear.siml", "Dps", 646),
]
results = []


def check(name, ok, detail):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)


def t_build():
    r = subprocess.run([sys.executable, str(config.SCRIPTS_DIR / "build_sim.py"), "--check"],
                       capture_output=True, text=True)
    bad = [ln for ln in r.stdout.splitlines() if "MISSING" in ln or "STALE" in ln]
    check("build: build_sim --check", r.returncode == 0 and not bad, "; ".join(bad) or "clean")
    o = harness.one({"name": "preset", "iterations": 10000, "seed": 1},
                    harness.preset_path("prot_paladin"))
    check("build: bisrun sims the preset gear", not o["Err"] and o["Tps"] > 0,
          o["Err"] or f"TPS {o['Tps']:.1f} DPS {o['Dps']:.1f} DTPS {o['Dtps']:.1f} TMI {o['Tmi']:.1f}")


def t_resim():
    ctx = api.context("prot_paladin", "prot_v3", "RaceUndead")
    for f, m, target in RESIM:
        g = Gear.from_siml((KIT / "gear_siml" / f).read_text(encoding="utf-8"), ctx.base)
        o = analysis.sim_gear(ctx, g, iterations=10000)
        dev = (o[m] - target) / target
        check(f"resim: {f}", abs(dev) <= 0.01, f"{m} {o[m]:.1f} vs {target} ({dev:+.2%})")


def t_compare():
    ctx = api.context("prot_paladin", "prot_v3", "RaceUndead")
    from wowsim_tools import findings
    g, *_ = findings.resolve_set("2026-11-18_prot_bis/dps_t05")
    r = analysis.compare(ctx, g, 13, [(22321, 0)], parse("dps"), iterations=4000)
    row = r["rows"][0]
    check("compare: HotS vs HoW (trinket 2, DPS T0.5)", abs(row["delta"]) <= 1.0,
          f"Heart of Wyrmthalak {row['delta']:+.2f} ±{row['noise']:.2f} DPS vs Heart of the Scale")


async def _mcp_session(script, calls):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    p = StdioServerParameters(command=sys.executable, args=[str(script)])
    out = []
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for name, args in calls:
                t0 = time.perf_counter()
                res = await s.call_tool(name, args)
                out.append((time.perf_counter() - t0, res))
    return out


def datamine_latency(n=15):
    calls = [("query", {"sql": "SELECT COUNT(*) FROM ItemSparse"})] * n
    res = asyncio.run(_mcp_session(config.SCRIPTS_DIR / "mcp_server.py", calls))
    errs = sum(1 for _, r in res if r.is_error)
    return statistics.median(t for t, _ in res) * 1000, errs


def start_job_via_server_then_kill():
    """Start optimize through the wowsim server; the session ends (server process
    exits) right after the call returns."""
    res = asyncio.run(_mcp_session(config.SCRIPTS_DIR / "wowsim_mcp_server.py", [
        ("optimize", {"objective": "tps", "t05": True, "rules_profile": "prot_v3",
                      "budget": "quick"})]))
    text = res[0][1].content[0].text
    return text.split()[3].rstrip(":"), text


def t_optimize_isolation():
    idle, idle_err = datamine_latency()
    jid, text = start_job_via_server_then_kill()
    print(f"  {text}", flush=True)
    time.sleep(3)
    st = jobs.status(jid)
    check("isolation: job survives the wowsim server exiting", st["status"] in ("running", "done"),
          f"status {st['status']} after the server process ended "
          f"(runner started by {st.get('spawned_by')})")
    if st["status"] not in ("running", "done"):
        check("optimize: quick tps t05", False, f"job {jid} {st['status']}")
        return
    while (jobs.status(jid)["status"] == "running"
           and jobs.status(jid).get("phase") in (None, "setup", "weights", "seeds")):
        time.sleep(2)
    busy, busy_err = datamine_latency()
    check("isolation: wow-datamine answers during optimize", busy_err == 0 and idle_err == 0,
          f"median query {idle:.0f} ms idle, {busy:.0f} ms during the job "
          f"(phase {jobs.status(jid).get('phase')})")
    while jobs.status(jid)["status"] in ("starting", "running"):
        time.sleep(5)
    st = jobs.status(jid)
    if st["status"] != "done":
        check("optimize: quick tps t05", False, f"job {jid} {st['status']}: {st.get('error')}")
        return
    r = jobs.result(jid)
    tps, sd, n = r["eval"]["Tps"], r["eval"]["TpsSd"], r["final_iters"]
    dev = (tps - 1619) / 1619
    check("optimize: under 5 min", st["elapsed_s"] < 300, f"{st['elapsed_s']:.0f} s (job {jid})")
    check("optimize: within 1% of 1619 TPS", abs(dev) <= 0.01,
          f"TPS {tps:.1f} ±{sd / n ** 0.5:.1f} ({dev:+.2%}), sets {', '.join(r['sets']) or 'none'}")


def t_clean_build():
    src = config.REPO_ROOT
    tmp = Path(tempfile.mkdtemp(prefix="wowsim-clean-"))
    try:
        files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                               cwd=src, capture_output=True, text=True, check=True).stdout.split("\n")
        for f in filter(None, files):
            if f.startswith("wowsim-kit"):
                continue
            (tmp / f).parent.mkdir(parents=True, exist_ok=True)
            if (src / f).is_file():
                shutil.copy2(src / f, tmp / f)
        dl = config.TOOLCHAIN_DIR / "_dl"
        if dl.is_dir():
            shutil.copytree(dl, tmp / "vendor" / "toolchains" / "_dl")
        t0 = time.time()
        r = subprocess.run([sys.executable, str(tmp / "scripts" / "build_sim.py"), "--setup-toolchains"],
                           cwd=tmp, capture_output=True, text=True)
        ok = r.returncode == 0 and (tmp / "bin" / f"bisrun{harness.EXE}").exists()
        tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or ["no output"]
        check("clean: build_sim.py on a clean copy", ok, f"{time.time() - t0:.0f} s; {tail[0][:160]}")
        if ok:
            o = subprocess.run([str(tmp / "bin" / f"bisrun{harness.EXE}"), "-base",
                                str(tmp / "config" / "presets" / "prot_paladin.json")],
                               input='{"name":"p","iterations":2000,"seed":1}\n',
                               capture_output=True, text=True)
            out = json.loads(o.stdout.splitlines()[0])
            check("clean: its bisrun sims the preset", not out["Err"] and out["Tps"] > 0,
                  out["Err"] or f"TPS {out['Tps']:.1f}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean-build", action="store_true")
    ap.add_argument("--skip-optimize", action="store_true")
    a = ap.parse_args()
    if os.name == "nt":
        sys.stdout.reconfigure(encoding="utf-8")
    t_build()
    t_resim()
    t_compare()
    if not a.skip_optimize:
        t_optimize_isolation()
    if a.clean_build:
        t_clean_build()
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed"
          + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    print("Not automatable here: Claude Desktop sim_gear through the remote-devices bridge "
          "(see docs/wowsim.md).")
    sys.exit(len(failed))


if __name__ == "__main__":
    main()
