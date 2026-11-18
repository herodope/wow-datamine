#!/usr/bin/env python3
"""Run one background sim job: python scripts/wowsim_job.py <job id>.

Spawned detached by wowsim_tools.jobs.start(); not meant to be run by hand
except to re-run a job directory. Reads sim_out/jobs/<id>/args.json, logs to
log.txt (its stdout), writes state.json as it goes and result.json at the end.
"""

import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wowsim_tools import api, jobs  # noqa: E402


def main(job_id):
    d = jobs.ROOT / job_id
    args = json.loads((d / "args.json").read_text(encoding="utf-8"))
    # Started detached (possibly through WMI): no console, no inherited handles.
    sys.stdout = sys.stderr = open(d / "log.txt", "a", encoding="utf-8", buffering=1)

    def log(msg):
        print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)

    jobs.write_state(job_id, status="running", started=time.time(), phase="setup")
    try:
        if args["kind"] != "optimize":
            raise ValueError(f"unknown job kind {args['kind']!r}")
        res = api.run_optimize(args, log=log,
                               progress=lambda phase: jobs.write_state(job_id, phase=phase))
        res["job_id"] = job_id
        tmp = d / "result.json.tmp"
        tmp.write_text(json.dumps(res, indent=1), encoding="utf-8")
        tmp.replace(d / "result.json")
        e = res["eval"]
        log(f"done in {res['elapsed_s']:.0f}s: TPS {e['Tps']:.1f} DPS {e['Dps']:.1f} "
            f"DTPS {e['Dtps']:.1f} TMI {e['Tmi']:.1f}")
        jobs.write_state(job_id, status="done", phase="done", finished=time.time())
    except Exception as exc:  # noqa: BLE001 -- recorded for job_status, then re-raised
        traceback.print_exc()
        jobs.write_state(job_id, status="failed", finished=time.time(),
                         error=f"{type(exc).__name__}: {exc}"[:500])
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: wowsim_job.py <job id>")
    main(sys.argv[1])
