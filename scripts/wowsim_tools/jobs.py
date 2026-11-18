"""Background jobs: sim_out/jobs/<id>/{args.json, state.json, log.txt, result.json}.

start() spawns scripts/wowsim_job.py as a detached process and returns at
once, so the MCP server stays responsive and a job survives the server being
stopped or restarted. state.json is written by the runner; status() also
checks the runner's pid, so a runner that died without writing "failed" reads
as "died" rather than running forever.
"""

import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime

import config

ROOT = config.SIM_JOBS_DIR
RUNNER = config.SCRIPTS_DIR / "wowsim_job.py"


class JobError(ValueError):
    pass


def _dir(job_id):
    d = ROOT / job_id
    if not job_id or "/" in job_id or "\\" in job_id or not (d / "args.json").exists():
        raise JobError(f"no job {job_id!r}")
    return d


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_state(job_id, **kw):
    d = ROOT / job_id
    st = read_json(d / "state.json", {})
    st.update(kw)
    tmp = d / "state.json.tmp"
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    os.replace(tmp, d / "state.json")


def pid_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259          # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _spawn_wmi(cmd, cwd):
    """Start cmd through WMI (Win32_Process.Create); returns its pid.

    WMI creates the process from its own service, so it is in no job object of
    ours. Needed when the MCP client runs the server in a KILL_ON_JOB_CLOSE job
    that forbids breakaway (the Python mcp client does): a child created
    normally would be killed with the server.
    """
    ps = ("$si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly "
          "-Property @{ShowWindow=[uint16]0}; "
          "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
          "@{CommandLine=$env:WOWSIM_CMD; CurrentDirectory=$env:WOWSIM_CWD; "
          "ProcessStartupInformation=$si}; "
          "if ($r.ReturnValue -ne 0) { Write-Error \"Win32_Process.Create returned $($r.ReturnValue)\"; exit 1 }; "
          "$r.ProcessId")
    env = dict(os.environ, WOWSIM_CMD=subprocess.list2cmdline(cmd), WOWSIM_CWD=str(cwd))
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, env=env, timeout=120,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode != 0 or not r.stdout.strip().isdigit():
        raise JobError(f"could not start the job runner: {(r.stderr or r.stdout).strip()[:300]}")
    return int(r.stdout.strip())


def start(kind, args):
    job_id = f"{datetime.now():%Y%m%d-%H%M%S}-{kind}-{secrets.token_hex(2)}"
    d = ROOT / job_id
    d.mkdir(parents=True)
    (d / "args.json").write_text(json.dumps({"kind": kind, **args}, indent=1), encoding="utf-8")
    write_state(job_id, status="starting", created=time.time())
    # The runner writes its own log.txt, so it needs no inherited handles.
    cmd = [sys.executable, str(RUNNER), job_id]
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              cwd=str(config.REPO_ROOT), close_fds=True)
    if os.name == "nt":
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.CREATE_BREAKAWAY_FROM_JOB)
        try:
            pid = subprocess.Popen(cmd, creationflags=flags, **kw).pid
            how = "breakaway"
        except OSError:
            pid = _spawn_wmi(cmd, config.REPO_ROOT)
            how = "wmi"
    else:
        pid = subprocess.Popen(cmd, start_new_session=True, **kw).pid
        how = "setsid"
    write_state(job_id, pid=pid, spawned_by=how)
    return job_id


def status(job_id):
    d = _dir(job_id)
    st = read_json(d / "state.json", {})
    if st.get("status") in ("starting", "running") and not pid_alive(st.get("pid")):
        if not (d / "result.json").exists():
            st["status"] = "died"
    lines = []
    try:
        lines = (d / "log.txt").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        pass
    st["log_tail"] = [ln for ln in lines if ln.strip()][-8:]
    st["args"] = read_json(d / "args.json", {})
    end = st.get("finished") or time.time()
    st["elapsed_s"] = round(end - (st.get("started") or st.get("created") or end), 1)
    return st


def result(job_id):
    d = _dir(job_id)
    r = read_json(d / "result.json")
    if r is None:
        raise JobError(f"job {job_id} has no result yet ({status(job_id)['status']})")
    return r


def list_jobs(limit=10):
    if not ROOT.is_dir():
        return []
    out = []
    for d in sorted(ROOT.iterdir(), reverse=True)[:limit]:
        if (d / "args.json").exists():
            st = status(d.name)
            out.append({"id": d.name, "status": st.get("status"), "phase": st.get("phase"),
                        "elapsed_s": st["elapsed_s"], "args": st["args"]})
    return out
