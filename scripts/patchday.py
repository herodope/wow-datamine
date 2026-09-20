#!/usr/bin/env python3
"""Run the CLAUDE.md patch-day checklist end to end.

Steps 1-2 are yours (let Battle.net finish, launch the client once so the CASC
index is complete, then close WoW and idle Battle.net). This script starts at
step 3 and stops at step 10.

What it will NOT do:
  * click "extract DBCs" in WTL -- it prompts and then verifies you did it
  * start WTL -- it is a blocking server; the script waits for it instead
  * push -- it commits locally and leaves the push to you

What it WILL stop loudly for:
  * fetch_builds.py finding no new build (nothing to do; do not proceed)
  * WTL serving a different build than the one just captured
  * DBCs not actually extracted after you say they are

Usage:
    python scripts/patchday.py --dry-run     # walk the plan, change nothing
    python scripts/patchday.py
    python scripts/patchday.py --push        # also push the commit
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import config

SCRIPTS = config.SCRIPTS_DIR
PY = sys.executable

BANNER = "=" * 72


def log(msg=""):
    print(msg, file=sys.stderr, flush=True)


def step(n, title):
    """n may be "9b" as well as an int."""
    log("")
    log(BANNER)
    log(f"STEP {n}  {title}")
    log(BANNER)


def stop(reason, detail=None, code=2):
    log("")
    log("!" * 72)
    log(f"STOPPING: {reason}")
    if detail:
        for line in detail:
            log(f"  {line}")
    log("!" * 72)
    log("")
    raise SystemExit(code)


def run(args, dry, label=None):
    """Run a pipeline script, streaming its output. Returns the exit code."""
    printable = " ".join(str(a) for a in args)
    if dry:
        log(f"  [dry-run] would run: {printable}")
        return 0
    log(f"  $ {printable}")
    proc = subprocess.run([PY] + [str(a) for a in args])
    if proc.returncode != 0:
        stop(f"{label or printable} exited {proc.returncode}",
             ["Fix the failure and re-run; earlier steps are idempotent."])
    return proc.returncode


def confirm(prompt, dry):
    if dry:
        log(f"  [dry-run] would prompt: {prompt}")
        return
    if not sys.stdin.isatty():
        stop("this step needs an interactive prompt but stdin is not a terminal",
             ["Re-run from an interactive shell, or use --dry-run to inspect the plan."])
    log("")
    input(f"  {prompt}\n  Press Enter when done... ")


def wtl_get(path, timeout=30):
    req = urllib.request.Request(config.WTL_URL + path, headers={"User-Agent": "wow-datamine/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError):
        return None, None


def load_builds():
    if not config.BUILDS_JSON.exists():
        return {}
    try:
        return json.loads(config.BUILDS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def forever_sorted(builds):
    """Forever builds only, oldest first."""
    out = []
    for key, entry in builds.items():
        version, bid = entry.get("version", ""), entry.get("buildId")
        if bid is None:
            continue
        if config.is_forever_build(version, bid):
            out.append((bid, version))
    return sorted(out)


# --- Steps ------------------------------------------------------------------


def step3_fetch(dry):
    step(3, "fetch_builds.py -- capture buildConfig/cdnConfig FIRST")
    log("  Unrecoverable if skipped: once Blizzard rotates a build off the live")
    log("  version list, these hashes are the only way back to it.")
    log("")

    before = set(load_builds())
    if dry:
        run([SCRIPTS / "fetch_builds.py", "--dry-run"], dry=False, label="fetch_builds.py --dry-run")
        log("")
        log("  [dry-run] not writing builds.json; new-build detection skipped")
        return None, None

    run([SCRIPTS / "fetch_builds.py"], dry=False, label="fetch_builds.py")
    after = load_builds()
    new_keys = set(after) - before

    if not new_keys:
        current = forever_sorted(after)
        stop(
            "fetch_builds.py found no new build",
            [
                "builds.json is unchanged, so there is nothing to extract or diff.",
                f"Known Forever builds: {', '.join(v for _b, v in current) or 'none'}",
                "",
                "If you expected a new build: check Battle.net finished patching, and",
                "that the client was launched once so the CASC index is complete.",
                "Re-run this script once the version endpoint reports the new build.",
            ],
        )

    newest = max(new_keys, key=int)
    entry = after[newest]
    version = entry["version"]
    log("")
    log(f"  NEW BUILD: {version}  (buildId {entry['buildId']})")
    log(f"    buildConfig {entry['buildConfig']}")
    log(f"    cdnConfig   {entry['cdnConfig']}")
    if len(new_keys) > 1:
        log(f"    note: {len(new_keys)} new builds captured; using the newest")

    ordered = forever_sorted(after)
    previous = None
    for bid, ver in ordered:
        if ver == version:
            break
        previous = ver
    return version, previous


def step4_sync(dry):
    step(4, "sync_refs.py -- WoWDBDefs, listfile, TACTKeys")
    log("  Must precede any use of definitionDir: WTL falls back to the remote")
    log("  manifest if the clone is not on disk.")
    log("")
    run([SCRIPTS / "sync_refs.py"], dry, label="sync_refs.py")


def step5_wtl(version, dry):
    step(5, "Start WTL with definitionDir pointing at the local clone")
    log(f"  definitionDir should be: {config.DEFINITIONS_DIR}")
    log("  Launch it in another terminal:  .\\scripts\\run-wtl.ps1")
    log("  (Close WoW and idle Battle.net first -- CASC file locks.)")
    log("")

    if dry:
        log("  [dry-run] would wait for WTL and verify the loaded build")
        return

    for attempt in range(60):
        status, body = wtl_get("/casc/buildname", timeout=10)
        if status == 200 and body:
            loaded = body.decode("utf-8").strip()
            log(f"  WTL is up, serving {loaded}")
            if version and loaded != version:
                stop(
                    f"WTL has {loaded} loaded, but the new build is {version}",
                    [
                        "Extracting now would capture the OLD build's data under the",
                        "new build's name. Restart WTL so it loads the new build, or",
                        "switch product/build on its builds page, then re-run.",
                    ],
                )
            return
        if attempt == 0:
            confirm("Start WTL now (.\\scripts\\run-wtl.ps1), then continue.", dry)
        time.sleep(5)
    stop("WTL never became reachable at " + config.WTL_URL,
         ["Check the terminal running run-wtl.ps1 for errors."])


def step6_updatedefs(dry):
    step(6, "GET /dbc/updateDefs -- reload definitions and clear the cache")
    log("  WTL caches definitions; without this it keeps using the previous")
    log("  build's set even though step 4 refreshed them on disk.")
    log("")
    if dry:
        log("  [dry-run] would call GET /dbc/updateDefs")
        return
    status, body = wtl_get("/dbc/updateDefs", timeout=600)
    if status != 200:
        stop(f"/dbc/updateDefs returned {status}", ["WTL may still be loading; retry shortly."])
    log(f"  {body.decode('utf-8', errors='replace').strip()}")
    log("  (With a local definitionDir this reloads and clears only -- no download.)")


def step7_extract_dbcs(version, dry):
    step(7, "Extract DBCs for the new build -- MANUAL, in WTL")
    log("  This one is yours: the script will not click it for you.")
    log("")
    log("    1. Open http://localhost:5080/builds/")
    log(f"    2. Find {version or 'the new build'}")
    log("    3. Use the DBC extract action for that row")
    log("    4. Wait for the console to finish")
    log("")
    log("  /dbc/info needs DBCs on disk; extract_db2.py needs them for layouthashes.")
    confirm("Extract the DBCs in WTL now.", dry)

    if dry:
        log("  [dry-run] would verify extraction via /dbc/info")
        return

    status, body = wtl_get(f"/dbc/info?build={version}", timeout=600)
    if status != 200:
        stop(f"/dbc/info returned HTTP {status}", ["Cannot verify the DBC extraction."])
    payload = json.loads(body)
    err = payload.get("error") or ""
    rows = len(payload.get("data", []))
    if err or rows == 0:
        stop(
            "DBCs do not appear to be extracted for this build",
            [
                f"/dbc/info says: {err or 'no rows returned'}",
                "",
                "extract_db2.py would run without layouthashes and diff_builds.py",
                "could not detect schema changes. Extract the DBCs and re-run.",
            ],
        )
    log(f"  verified: /dbc/info reports {rows:,} table(s) on disk")


def step8_extract(version, dry):
    step(8, "extract_db2.py, inventory.py and extract_gametables.py")
    run([SCRIPTS / "extract_db2.py", "--build", version] if version else [SCRIPTS / "extract_db2.py"],
        dry, label="extract_db2.py")
    log("")
    run([SCRIPTS / "inventory.py", "--build", version] if version else [SCRIPTS / "inventory.py"],
        dry, label="inventory.py")
    log("")
    # Must follow inventory.py: discovery reads files.csv. GameTables are not
    # DB2s and have no DBD definition, so extract_db2.py can never see them --
    # /listfile/db2s enumerates definitions. 42 of them at 1.60.1.69913.
    log("  GameTables are tab-separated text, invisible to the DB2 pipeline.")
    log("  SpellScaling.txt lives here -- the DB2 of that name ships empty.")
    run([SCRIPTS / "extract_gametables.py", "--build", version] if version
        else [SCRIPTS / "extract_gametables.py"],
        dry, label="extract_gametables.py")


def step9_diff(version, previous, dry):
    step(9, "Diff -- client changes and live changes are separate questions")
    log("  diff_hotfixes.py : db2/ vs db2_hotfixed/  (what Blizzard changed live)")
    log("  diff_builds.py   : db2/ vs db2/           (what shipped in the client)")
    log("")

    reports = []
    run([SCRIPTS / "diff_hotfixes.py", "--build", version] if version else [SCRIPTS / "diff_hotfixes.py"],
        dry, label="diff_hotfixes.py")
    if version:
        reports.append(config.REPORTS_DIR / f"hotfix_{version}.md")

    log("")
    if not previous:
        log("  SKIPPING diff_builds.py: no earlier Forever build to compare against.")
        log("  This is expected on the first tracked build; it resolves next patch day.")
    else:
        log(f"  previous Forever build: {previous}")
        run([SCRIPTS / "diff_builds.py", previous, version], dry, label="diff_builds.py")
        reports.append(config.report_path(previous, version))

    # The HTML pages render the same data for reading rather than auditing.
    # They run AFTER the markdown diffs, not instead of them: the markdown is
    # the committed record, the HTML is the thing you send someone.
    log("")
    log("  render_patchnotes.py : the same data as readable patch notes")
    if version:
        run([SCRIPTS / "render_patchnotes.py", "--build", version],
            dry, label="render_patchnotes.py (hotfix)")
        reports.append(config.REPORTS_DIR / f"patchnotes_{version}.html")
    if previous and version:
        run([SCRIPTS / "render_patchnotes.py", "--from", previous, "--to", version],
            dry, label="render_patchnotes.py (build diff)")
        reports.append(config.REPORTS_DIR / f"patchnotes_{previous}_to_{version}.html")
    return reports


def step9b_findings(version, dry):
    step("9b", "check_findings.py -- re-test the recorded predictions")
    log("  CLAUDE.md 'Findings to verify' holds dated predictions from an earlier")
    log("  build. This re-checks each against the data just extracted.")
    log("")
    if dry:
        log("  [dry-run] would run: check_findings.py")
        return
    run([SCRIPTS / "check_findings.py", "--build", version] if version else [SCRIPTS / "check_findings.py"],
        dry, label="check_findings.py")
    log("")
    log("  RESOLVED or FALSIFIED findings need CLAUDE.md updated by hand.")
    log("  Resolve existing predictions before recording new ones.")


def step10_commit(version, reports, dry, push):
    step(10, "Commit builds.json and the new report(s)")

    paths = [str(config.BUILDS_JSON)] + [str(p) for p in reports]
    msg = f"data: {version or '<new build>'} build index and diff reports"

    if dry:
        log(f"  [dry-run] would: git add {' '.join(paths)}")
        log(f"  [dry-run] would: git commit -m {msg!r}")
        log(f"  [dry-run] would: git push" if push else "  [dry-run] would NOT push")
        return

    subprocess.run(["git", "add"] + paths, cwd=str(config.REPO_ROOT))
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                            cwd=str(config.REPO_ROOT), capture_output=True, text=True).stdout.strip()
    if not staged:
        log("  nothing staged -- builds.json and reports are unchanged. Not committing.")
        return
    log("  staged:")
    for line in staged.splitlines():
        log(f"    {line}")

    proc = subprocess.run(["git", "commit", "-m", msg], cwd=str(config.REPO_ROOT))
    if proc.returncode != 0:
        stop("git commit failed", ["Resolve and commit manually."])

    if push:
        proc = subprocess.run(["git", "push", "origin", "main"], cwd=str(config.REPO_ROOT))
        if proc.returncode != 0:
            stop("git push failed", ["The commit is local; push manually."])
    else:
        log("")
        log("  committed locally, not pushed. Push with:  git push origin main")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="walk the plan without changing anything")
    ap.add_argument("--push", action="store_true", help="also push the final commit")
    ap.add_argument("--build", help="skip detection and use this build (resuming a failed run)")
    args = ap.parse_args(argv)

    started = time.monotonic()
    log(BANNER)
    log("PATCH DAY -- CLAUDE.md checklist" + ("  [DRY RUN]" if args.dry_run else ""))
    log(f"  {datetime.now(timezone.utc).replace(microsecond=0).isoformat()}")
    log(BANNER)
    log("")
    log("Steps 1-2 are manual and assumed done:")
    log("  1. Battle.net finished patching, client launched once (CASC index complete)")
    log("  2. WoW closed, Battle.net idle")

    if args.build:
        version = args.build
        ordered = forever_sorted(load_builds())
        previous = None
        for _bid, ver in ordered:
            if ver == version:
                break
            previous = ver
        log("")
        log(f"  --build {version}: skipping detection (previous: {previous or 'none'})")
    else:
        version, previous = step3_fetch(args.dry_run)

    step4_sync(args.dry_run)
    step5_wtl(version, args.dry_run)
    step6_updatedefs(args.dry_run)
    step7_extract_dbcs(version, args.dry_run)
    step8_extract(version, args.dry_run)
    reports = step9_diff(version, previous, args.dry_run)
    step9b_findings(version, args.dry_run)
    step10_commit(version, reports, args.dry_run, args.push)

    log("")
    log(BANNER)
    log(f"PATCH DAY COMPLETE in {time.monotonic() - started:.0f}s" + ("  [DRY RUN]" if args.dry_run else ""))
    if not args.dry_run:
        log("")
        log("  Next: check the five predictions in CLAUDE.md 'Findings to verify',")
        log("  and whether the three retail-contamination cases cleared.")
    log(BANNER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
