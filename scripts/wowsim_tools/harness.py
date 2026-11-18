"""Run jobs through bin/bisrun and report what the binary was built from."""

import json
import os
import subprocess
import threading
from pathlib import Path

import config

EXE = ".exe" if os.name == "nt" else ""
BISRUN = config.SIM_BIN_DIR / f"bisrun{EXE}"
BUILD_INFO = config.SIM_BIN_DIR / "build_info.json"


class SimError(RuntimeError):
    pass


def build_info():
    try:
        return json.loads(BUILD_INFO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def sim_head():
    if not (config.SIM_DIR / ".git").exists():
        return None
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=config.SIM_DIR,
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None     # git not on PATH: report the checkout as unknown
    return r.stdout.strip() or None


def status():
    """What is built, and whether it still matches the checkout and the pin."""
    info = build_info()
    head = sim_head()
    built = info.get("sim_sha")
    problems = []
    if not BISRUN.exists():
        problems.append("bin/bisrun is not built: run python scripts/build_sim.py")
    if head and built and head != built:
        problems.append(f"bin/ was built from {built[:10]} but vendor/forever is at "
                        f"{head[:10]}: run python scripts/build_sim.py")
    if built and built != config.SIM_PIN:
        problems.append(f"built sim {built[:10]} differs from config.SIM_PIN "
                        f"{config.SIM_PIN[:10]}")
    return {"sim_sha": built, "head": head, "pin": config.SIM_PIN,
            "db_json_sha256": info.get("db_json_sha256"),
            "built_at": info.get("built_at"), "problems": problems}


def require_built():
    if not BISRUN.exists():
        raise SimError("bin/bisrun is not built. Run: python scripts/build_sim.py "
                       "(first time: add --setup-toolchains)")


def preset_path(spec):
    specs = load_specs()
    if spec not in specs:
        raise SimError(f"unknown spec {spec!r}; known: {', '.join(sorted(specs))}")
    return config.PRESETS_DIR / specs[spec]["preset"]


_specs = None


def load_specs():
    global _specs
    if _specs is None:
        _specs = json.loads((config.SIM_CONFIG_DIR / "specs.json").read_text(encoding="utf-8"))
    return _specs


def run(jobs, base, workers=None, on_result=None):
    """Run jobs (dicts) against base (a RaidSimRequest JSON path).

    Returns {name: result}. Names must be unique. on_result(result) is called
    as each finishes, in completion order, from this thread.
    """
    require_built()
    names = [j["name"] for j in jobs]
    if len(set(names)) != len(names):
        raise SimError("job names must be unique within a batch")
    if not jobs:
        return {}
    cmd = [str(BISRUN), "-base", str(base),
           "-workers", str(workers or config.SIM_WORKERS)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            creationflags=flags)

    def feed():
        try:
            for j in jobs:
                proc.stdin.write(json.dumps(j) + "\n")
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    errbuf = []
    t_in = threading.Thread(target=feed, daemon=True)
    t_err = threading.Thread(target=lambda: errbuf.append(proc.stderr.read()), daemon=True)
    t_in.start()
    t_err.start()
    out = {}
    with proc:
        for line in proc.stdout:
            if not line.strip():
                continue
            o = json.loads(line)
            out[o["Name"]] = o
            if on_result:
                on_result(o)
        t_err.join(30)
    t_in.join(5)
    if proc.returncode != 0:
        raise SimError(f"bisrun exited {proc.returncode}: {''.join(errbuf).strip()[:500]}")
    missing = [n for n in names if n not in out]
    if missing:
        raise SimError(f"bisrun returned no result for {len(missing)} job(s), e.g. {missing[0]!r}")
    return out


def one(job, base):
    return run([job], base)[job["name"]]


def rel(path):
    try:
        return Path(path).resolve().relative_to(config.REPO_ROOT).as_posix()
    except ValueError:
        return Path(path).name
