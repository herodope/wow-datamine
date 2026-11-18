#!/usr/bin/env python3
"""MCP server for local wowsims/forever sims: a sibling of mcp_server.py.

Gear sims, stat weights, item comparisons and gear optimization for WoW
Forever, run on this machine through bin/bisrun (scripts/build_sim.py builds
it). Separate from the wow-datamine server on purpose: that one is read-only
over wow.db; this one starts jobs and writes files, so it gets its own
approval rules, and a long or stuck sim cannot slow or crash datamine queries.

No sim runs inside this process. Short questions (sim_gear, stat_weights,
compare_items) run bisrun as a subprocess and wait for it, off the event loop.
optimize spawns a detached job (scripts/wowsim_job.py) and returns its id at
once; the job keeps running if this server stops.

Read-only tools carry readOnlyHint so a client can auto-approve them;
optimize and findings_save are not read-only.

Install and run:

    pip install mcp
    python scripts/build_sim.py --setup-toolchains   # once; builds bin/bisrun
    python scripts/wowsim_mcp_server.py              # stdio; a client spawns this

Register it beside wow-datamine (user scope for Claude Code; the Desktop app's
claude_desktop_config.json for Desktop). See docs/wowsim.md.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import anyio  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from wowsim_tools import api, harness  # noqa: E402

RO = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
RUNS = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                       open_world_hint=False)

server = MCPServer(
    name="wowsim",
    instructions=(
        "Local wowsims/forever sims for WoW Forever gear: sim a set, stat weights, item "
        "comparisons, full BiS optimization. Check findings_list / findings_get FIRST: if a "
        "finding matches the question and is not stale, answer from it and say it is saved data. "
        "Otherwise prefer compare_items or stat_weights (fast); call optimize only for a full "
        "set, then poll job_status. Always state the iterations and the ± noise you report. "
        "DTPS counts melee only; Stamina's DTPS weight is 0 by definition; TMI is "
        "health-normalised spike damage. Gear arguments take .gear.siml text, a findings set id "
        "like 2026-11-18_prot_bis/tps_t05, an optimize job id, or 'preset'."),
)


async def _call(fn, *a, **kw):
    return await anyio.to_thread.run_sync(lambda: fn(*a, **kw))


@server.tool(annotations=RO, description=(
    "What is built (sim sha vs pin), the sim item db hash, rules profiles, specs, budgets and "
    "recent jobs. Call when a sim errors or before trusting a finding."))
async def sim_status() -> str:
    return await _call(api.status)


@server.tool(annotations=RO, description=(
    "Sim one gear set: TPS/DPS/DTPS/TMI/CoD with standard errors, HP, armor, defense, active "
    "set bonuses. gear: .gear.siml text, findings set id, job id, or 'preset'. encounter: boss "
    "(the preset's lvl-63 raid boss) or trash62 (lvl-62 pack, 60 s); targets: count. tschool: "
    "physical|arcane|fire|frost|holy|nature|shadow melee school, to model magic melee."))
async def sim_gear(gear: str = "preset", spec: str | None = None, race: str | None = None,
                   iterations: int = 10000, targets: int = 1, encounter: str = "boss",
                   tschool: str | None = None, rules_profile: str | None = None) -> str:
    return await _call(api.sim_gear, gear, spec, race, iterations, targets, encounter, tschool,
                       rules_profile)


@server.tool(annotations=RO, description=(
    "Stat weights at a gear set by finite differences, two paired seeds, with ± noise. "
    "metrics: any of tps, dps, dtps, tmi. Normalised Sta=1 for TMI and SP=1 for TPS/DPS; DTPS "
    "per point. targets: list of target counts, e.g. [1,3,5,7] for AoE weights (uses the "
    "trash62 encounter when any count > 1). Weights are local to the gear measured on."))
async def stat_weights(gear: str = "preset", metrics: list[str] | None = None,
                       targets: list[int] | None = None, iterations: int = 8000,
                       spec: str | None = None, race: str | None = None,
                       encounter: str | None = None, tschool: str | None = None,
                       rules_profile: str | None = None) -> str:
    return await _call(api.stat_weights, gear, metrics or ["tps", "dps", "dtps", "tmi"],
                       targets or [1], iterations, spec, race, encounter, tschool, rules_profile)


@server.tool(annotations=RO, description=(
    "Swap alternatives into one slot of a gear set and report paired deltas on a metric "
    "(dps|tps|dtps|tmi|dtps_min_hp:N) with measured ± noise, plus the other metrics' deltas "
    "and each item's source. Give items (names or ids) or top_n (best allowed under the rules "
    "profile: a full-sim screen for trinkets/weapons/shields/librams, EP otherwise). Slot: "
    "head, neck, ..., finger1, finger2, trinket1, trinket2, main_hand, off_hand, ranged."))
async def compare_items(gear: str, slot: str, items: list[str] | None = None,
                        top_n: int | None = None, metric: str = "tps", iterations: int = 4000,
                        t05: bool = True, spec: str | None = None, race: str | None = None,
                        encounter: str | None = None, targets: int = 1,
                        tschool: str | None = None, rules_profile: str | None = None) -> str:
    return await _call(api.compare_items, gear, slot, items, top_n, metric, iterations, t05,
                       spec, race, encounter, targets, tschool, rules_profile)


@server.tool(annotations=RUNS, description=(
    "Start a full gear optimization in the background and return its job id at once. "
    "objective: tps|dps|dtps|tmi|dtps_min_hp:<HP>. t05: allow the Soulforge T0.5 set. "
    "rules_profile: exclusion rules (default: last used). budget: quick (~4 min on 12 threads) | standard "
    "| thorough. seed_from: optional gear (findings set id, job id) to start from; with t05 on "
    "and no seed_from, the matching no-T0.5 finding seeds it. Poll job_status, then job_result."))
async def optimize(objective: str, t05: bool, rules_profile: str | None = None,
                   budget: str = "quick", seed_from: str | None = None,
                   spec: str | None = None, race: str | None = None) -> str:
    return await _call(api.start_optimize, objective, t05, rules_profile, budget, seed_from,
                       spec, race)


@server.tool(annotations=RO, description=(
    "Progress of a background job: status, phase, elapsed time and the last log lines. "
    "With no job_id, lists recent jobs."))
async def job_status(job_id: str | None = None) -> str:
    return await _call(api.job_status, job_id)


@server.tool(annotations=RO, description=(
    "Result of a finished optimize job: slot table with enchants and sources, final metrics "
    "with standard errors, seed scores, and items the rules excluded that rank higher by EP."))
async def job_result(job_id: str) -> str:
    return await _call(api.job_result, job_id)


@server.tool(annotations=RO, description=(
    "A .gear.siml export (paste into the wowsims site or CLI) for an optimize job id or a "
    "findings set id, with a // header naming rules, date, sim sha and metrics."))
async def export_gear_siml(result_id: str) -> str:
    return await _call(api.export_gear_siml, result_id)


@server.tool(annotations=RO, description=(
    "Saved findings (previous sim results, not ground truth), each flagged stale when the sim "
    "sha, the sim item db or its rules profile changed. Optional substring filter."))
async def findings_list(contains: str = "") -> str:
    return await _call(api.findings_list, contains)


@server.tool(annotations=RO, description=(
    "One finding: its sets' metrics and staleness; with a set ('<id>/<set>' or set_name), that "
    "set's slot table. Re-sim a stale finding before quoting its numbers."))
async def findings_get(finding_id: str, set_name: str | None = None) -> str:
    return await _call(api.findings_get, finding_id, set_name)


@server.tool(annotations=RUNS, description=(
    "Save a finished optimize job as a new finding under findings/<date>_<topic>/ (meta, "
    "result, summary.md, .gear.siml)."))
async def findings_save(job_id: str, topic: str, set_name: str | None = None) -> str:
    return await _call(api.findings_save, job_id, topic, set_name)


if __name__ == "__main__":
    st = harness.status()
    print(f"wowsim MCP server: sim {(st['sim_sha'] or 'NOT BUILT')[:10]}"
          + (f" ({'; '.join(st['problems'])})" if st["problems"] else ""), file=sys.stderr)
    server.run(transport="stdio")
