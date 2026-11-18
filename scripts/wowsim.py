#!/usr/bin/env python3
"""Command line for the wowsim tools; same answers as the wowsim MCP server.

    python scripts/wowsim.py status
    python scripts/wowsim.py sim --gear 2026-11-18_prot_bis/tps_t05 [--iterations 10000]
    python scripts/wowsim.py weights --gear preset [--metrics tps,tmi] [--targets 1,3,5]
    python scripts/wowsim.py compare --gear <set> --slot trinket2 --items "Heart of the Scale,Heart of Wyrmthalak" --metric dps
    python scripts/wowsim.py compare --gear <set> --slot neck --top-n 5 --metric tps
    python scripts/wowsim.py close-calls --gear <set> --metric tps
    python scripts/wowsim.py optimize --objective tps --t05 on [--budget quick] [--wait]
    python scripts/wowsim.py optimize --spec ret_paladin --objective dps --t05 off --wait
    python scripts/wowsim.py job [<job id>]           # status; no id lists recent jobs
    python scripts/wowsim.py result <job id>
    python scripts/wowsim.py export <job id | finding/set>
    python scripts/wowsim.py findings [<id>[/<set>]]
    python scripts/wowsim.py save <job id> --topic "prot tps t05"
    python scripts/wowsim.py import-prototype wowsim-kit/findings_nov18_2026
    python scripts/wowsim.py rebase-rules <finding id> [--old-profile old.json]

--gear takes .gear.siml file paths, findings set ids, job ids, or 'preset'.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wowsim_tools import api, findings, jobs  # noqa: E402


def csv(s):
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


def main():
    ap = argparse.ArgumentParser(description="wowsims/forever gear sims",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--spec", help="spec key in config/specs.json: prot_paladin (default), ret_paladin")
    shared.add_argument("--rules", help="rules profile in config/rules/ (default: the last one used "
                                        "for this spec, else the spec's default_rules)")
    shared.add_argument("--race", help="proto race, e.g. RaceUndead (default: the profile's)")
    subs = ap.add_subparsers(dest="cmd", required=True)

    class _Sub:
        def add_parser(self, name):
            return subs.add_parser(name, parents=[shared])

    sub = _Sub()

    sub.add_parser("status")
    p = sub.add_parser("sim")
    p.add_argument("--gear", default="preset")
    p.add_argument("--iterations", type=int, default=10000)
    p.add_argument("--targets", type=int, default=1)
    p.add_argument("--encounter", default="boss")
    p.add_argument("--tschool")

    p = sub.add_parser("weights")
    p.add_argument("--gear", default="preset")
    p.add_argument("--metrics", default="tps,dps,dtps,tmi")
    p.add_argument("--targets", default="1")
    p.add_argument("--iterations", type=int, default=8000)
    p.add_argument("--encounter")
    p.add_argument("--tschool")

    p = sub.add_parser("compare")
    p.add_argument("--gear", required=True)
    p.add_argument("--slot", required=True)
    p.add_argument("--items")
    p.add_argument("--top-n", type=int)
    p.add_argument("--metric", help="default: the spec's default_metric (tps prot, dps ret)")
    p.add_argument("--iterations", type=int, default=4000)
    p.add_argument("--t05", default="on")

    p = sub.add_parser("close-calls")
    p.add_argument("--gear", required=True)
    p.add_argument("--metric", help="default: the spec's default_metric (tps prot, dps ret)")
    p.add_argument("--t05", default="on")
    p.add_argument("--iterations", type=int, default=4000)

    p = sub.add_parser("optimize")
    p.add_argument("--objective", required=True)
    p.add_argument("--t05", required=True)
    p.add_argument("--budget", default="quick")
    p.add_argument("--seed-from")
    p.add_argument("--wait", action="store_true", help="poll until the job finishes")

    p = sub.add_parser("job")
    p.add_argument("job_id", nargs="?")
    p = sub.add_parser("result")
    p.add_argument("job_id")
    p = sub.add_parser("export")
    p.add_argument("result_id")
    p = sub.add_parser("findings")
    p.add_argument("finding_id", nargs="?")
    p = sub.add_parser("save")
    p.add_argument("job_id")
    p.add_argument("--topic", required=True)
    p.add_argument("--set-name")
    p = sub.add_parser("import-prototype")
    p.add_argument("kit_dir")
    p = sub.add_parser("rebase-rules")
    p.add_argument("finding_id")
    p.add_argument("--old-profile", help="the profile JSON the finding was simmed under "
                                         "(default: its rules_snapshot)")

    a = ap.parse_args()
    common = dict(spec=a.spec, race=a.race, rules=a.rules)
    c = a.cmd
    if c == "status":
        print(api.status())
    elif c == "sim":
        print(api.sim_gear(a.gear, iterations=a.iterations, targets=a.targets,
                           encounter=a.encounter, tschool=a.tschool, **common))
    elif c == "weights":
        print(api.stat_weights(a.gear, csv(a.metrics), [int(t) for t in csv(a.targets)],
                               a.iterations, encounter=a.encounter, tschool=a.tschool, **common))
    elif c == "compare":
        print(api.compare_items(a.gear, a.slot, csv(a.items), a.top_n, a.metric, a.iterations,
                                a.t05, **common))
    elif c == "close-calls":
        print(api.close_calls(a.gear, a.metric, a.t05, iterations=a.iterations, **common))
    elif c == "optimize":
        jid, msg = api.start_optimize_job(a.objective, a.t05, a.rules, a.budget, a.seed_from,
                                          a.spec, a.race)
        print(msg)
        if a.wait:
            while jobs.status(jid)["status"] in ("starting", "running"):
                time.sleep(5)
            print(api.job_status(jid))
            if jobs.status(jid)["status"] == "done":
                print(api.job_result(jid))
    elif c == "job":
        print(api.job_status(a.job_id))
    elif c == "result":
        print(api.job_result(a.job_id))
    elif c == "export":
        print(api.export_gear_siml(a.result_id))
    elif c == "findings":
        print(api.findings_get(a.finding_id) if a.finding_id else api.findings_list())
    elif c == "save":
        print(api.findings_save(a.job_id, a.topic, a.set_name))
    elif c == "rebase-rules":
        meta, _ = findings.load(a.finding_id)
        ctx = api.context(meta.get("spec"), meta["rules_profile"], a.race, log=print)
        old = json.loads(Path(a.old_profile).read_text(encoding="utf-8")) if a.old_profile else None
        print(findings.rebase_rules(a.finding_id, ctx, old))
    elif c == "import-prototype":
        ctx = api.context(a.spec, a.rules or "prot_v3", a.race, log=print)
        print("imported", findings.import_prototype(Path(a.kit_dir), ctx))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        main()
    except (ValueError, RuntimeError) as exc:
        sys.exit(f"wowsim: {exc}")
