#!/usr/bin/env python3
"""Extract every DB2 table from WTL as CSV, twice: plain and hotfix-applied.

Drives WTL's HTTP API (see docs/wtl-api.md). For each table in the
build-filtered list it issues two GETs to /dbc/export/ differing only in
useHotfixes, writing:

    out/<version>/db2/<table>.csv            plain, as shipped in the build
    out/<version>/db2_hotfixed/<table>.csv   with the live hotfix overlay

Keeping the two separate is what makes a client patch distinguishable from live
tuning: a value that moves only in db2_hotfixed/ was hotfixed, one that moves in
both shipped in the build.

Resumable -- completed tables are appended to a JSONL checkpoint and skipped on
a re-run. Safe to interrupt and restart.

Usage:
    python scripts/extract_db2.py
    python scripts/extract_db2.py --build 1.60.1.69913
    python scripts/extract_db2.py --restart          # ignore checkpoint
    python scripts/extract_db2.py --limit 20         # smoke test
"""

import argparse
import csv
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import config

USER_AGENT = "wow-datamine/1.0 (+local datamining pipeline)"
PROBE_TIMEOUT = 10
EXPORT_TIMEOUT = 900
INFO_TIMEOUT = 600

# GOTCHA (docs/wtl-api.md): the API parameter is `useHotfixes`. The browse page
# URL spells it `hotfixes=` and rewrites it client-side. Sending `hotfixes=`
# here would bind nothing, return 200, and silently yield NON-hotfixed data --
# filling db2_hotfixed/ with plain rows and never reporting a problem.
HOTFIX_PARAM = "useHotfixes"

# GOTCHA: the default locale on DB2 routes is All_WoW, not enUS. Always explicit.
LOCALE = "enUS"

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

_print_lock = threading.Lock()
_ckpt_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, file=sys.stderr, flush=True)


# --- HTTP -------------------------------------------------------------------


def get(path, params=None, timeout=EXPORT_TIMEOUT):
    """GET a WTL route. Returns (status, body_bytes). Raises on transport error."""
    url = config.WTL_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        # 404 / 400 are meaningful answers here, not failures.
        return exc.code, exc.read()


def require_wtl():
    """Fail clearly if WTL is not reachable, rather than 1161 confusing errors."""
    try:
        status, body = get("/casc/buildname", timeout=PROBE_TIMEOUT)
    except (urllib.error.URLError, OSError) as exc:
        log("")
        log(f"WTL is not reachable at {config.WTL_URL}")
        log(f"  {exc}")
        log("")
        log("  Start it first:   .\\scripts\\run-wtl.ps1")
        log("  It must finish loading the build before extraction can begin.")
        log("")
        raise SystemExit(2)
    if status != 200 or not body:
        log(f"WTL answered {config.WTL_URL}/casc/buildname with HTTP {status}; expected 200 + a build name")
        raise SystemExit(2)
    return body.decode("utf-8").strip()


# --- Metadata ---------------------------------------------------------------


def fetch_table_list(build):
    """Build-filtered table list.

    GOTCHA: unfiltered this returns every table WoWDBDefs defines (1342);
    filtered by build it returns only those present (1161). Iterating the
    unfiltered list means ~181 tables that can never resolve.
    """
    status, body = get("/listfile/db2s", {"build": build}, timeout=INFO_TIMEOUT)
    if status != 200:
        log(f"/listfile/db2s returned HTTP {status}")
        raise SystemExit(2)
    names = json.loads(body)
    if not isinstance(names, list):
        log(f"/listfile/db2s returned {type(names).__name__}, expected a list")
        raise SystemExit(2)
    return names


def fetch_layouthashes(build):
    """name.lower() -> layouthash, from /dbc/info.

    Row shape is positional: [name, recordCount, fieldCount, recordSize,
    tableHash, layoutHash, ...] -- layoutHash is index 5.

    This route needs DBCs extracted to disk and reports that failure as HTTP
    200 with a populated `error` string, so check `error`, not the status.
    """
    status, body = get("/dbc/info", {"build": build}, timeout=INFO_TIMEOUT)
    if status != 200:
        log(f"/dbc/info returned HTTP {status}; layouthashes unavailable")
        return {}
    payload = json.loads(body)
    err = payload.get("error") or ""
    if err:
        log(f"/dbc/info error: {err}")
        log("  Extract DBCs for this build first (builds page, or /dbc/export/alltodisk).")
        log("  Continuing without layouthashes.")
        return {}
    out = {}
    for row in payload.get("data", []):
        if len(row) > 5:
            out[row[0].lower()] = row[5]
    return out


# --- Extraction -------------------------------------------------------------


def count_csv_rows(path):
    """Data rows, excluding the header.

    Uses csv.reader rather than counting newlines: with the default
    newLinesInStrings=true, string cells may contain embedded CR/LF, so a
    newline count over-reports.
    """
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        n = -1  # header
        for _ in reader:
            n += 1
    return max(n, 0)


def export_one(table, build, hotfixed, dest_dir):
    """One /dbc/export call. Returns (http_status, rows, sha256|None)."""
    params = {
        "name": table,
        "build": build,
        "locale": LOCALE,
        HOTFIX_PARAM: "true" if hotfixed else "false",
    }
    status, body = get("/dbc/export/", params)

    if status == 204:
        # Table loaded fine, it just has no rows in this variant. Success.
        # Hash empty content rather than None so the plain/hotfixed comparison
        # stays uniform -- a table that is empty in the build but populated by
        # the hotfix overlay is still a delta, and the most interesting kind.
        return status, 0, hashlib.sha256(b"").hexdigest()
    if status != 200:
        # 404 = not in this build; 400 = generation failure.
        return status, None, None

    dest = dest_dir / (table + ".csv")
    fd, tmp = tempfile.mkstemp(dir=str(dest_dir), prefix="." + table + ".", suffix=".csv")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        os.replace(tmp, dest)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    return status, count_csv_rows(dest), hashlib.sha256(body).hexdigest()


def extract_table(table, build, plain_dir, hotfixed_dir, layouthashes):
    """Both variants of one table. Never raises -- failures become a record."""
    rec = {
        "table": table,
        "layouthash": layouthashes.get(table.lower()),
        "http": {"plain": None, "hotfixed": None},
        "rows": {"plain": None, "hotfixed": None},
        "hotfix_delta": False,
        "resolution": "error",
        "error": None,
    }
    try:
        s_p, r_p, h_p = export_one(table, build, False, plain_dir)
        rec["http"]["plain"] = s_p
        rec["rows"]["plain"] = r_p

        s_h, r_h, h_h = export_one(table, build, True, hotfixed_dir)
        rec["http"]["hotfixed"] = s_h
        rec["rows"]["hotfixed"] = r_h

        # Content comparison, not row count: a hotfix can change a value
        # without adding or removing a row (5 tables here do exactly that).
        if h_p is not None and h_h is not None:
            rec["hotfix_delta"] = h_p != h_h

        if s_p == 404:
            # Not present in this build at all -- distinct from empty.
            rec["resolution"] = "not_in_build"
        elif s_p == 204:
            # Empty as shipped. If the hotfix overlay supplies rows, the table
            # exists only as live data -- call that out rather than burying it
            # under "empty".
            rec["resolution"] = "hotfix_only" if (r_h or 0) > 0 else "empty"
        elif s_p == 200:
            rec["resolution"] = "ok"
        else:
            rec["resolution"] = "error"
            rec["error"] = f"plain export returned HTTP {s_p}"

        # A hotfixed variant that disagrees about existence is worth surfacing.
        if s_p == 200 and s_h not in (200, 204):
            rec["error"] = f"plain 200 but hotfixed HTTP {s_h}"

    except (urllib.error.URLError, OSError, ValueError) as exc:
        rec["resolution"] = "error"
        rec["error"] = f"{type(exc).__name__}: {exc}"

    return rec


# --- Checkpoint -------------------------------------------------------------


def load_checkpoint(path):
    """Completed records from a previous run, keyed by table."""
    if not path.exists():
        return {}
    done = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # truncated final line from an interrupted run
            if "table" in rec:
                done[rec["table"]] = rec
    return done


def append_checkpoint(path, rec):
    with _ckpt_lock:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())


# --- Manifest ---------------------------------------------------------------


def write_manifest(path, build, records, started, wtl_build):
    by_res = {}
    for rec in records.values():
        by_res[rec["resolution"]] = by_res.get(rec["resolution"], 0) + 1

    manifest = {
        "build": build,
        "buildId": int(build.split(".")[-1]),
        "locale": LOCALE,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "durationSeconds": round(time.monotonic() - started, 1),
        "wtlLoadedBuild": wtl_build,
        "totals": {
            "tables": len(records),
            "ok": by_res.get("ok", 0),
            "empty": by_res.get("empty", 0),
            "hotfix_only": by_res.get("hotfix_only", 0),
            "not_in_build": by_res.get("not_in_build", 0),
            "error": by_res.get("error", 0),
            "hotfix_delta": sum(1 for r in records.values() if r["hotfix_delta"]),
            "rows_plain": sum(r["rows"]["plain"] or 0 for r in records.values()),
            "rows_hotfixed": sum(r["rows"]["hotfixed"] or 0 for r in records.values()),
        },
        "tables": {k: records[k] for k in sorted(records)},
    }

    # Other scripts merge their own sections into this file -- inventory.py
    # writes "inventory". Rewriting the whole dict used to drop them: every
    # re-extract (the hotfix-only-day procedure prescribes one) erased the
    # inventory section, so 69913 and 69977 lost theirs, and 70009's
    # encrypted-count comparison ran with no baseline on the first build whose
    # file set actually moved. Carry forward any key this script does not own.
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        for key, value in existing.items():
            if key not in manifest:
                manifest[key] = value

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".manifest.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(manifest, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return manifest


# --- Main -------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", help="version string; defaults to WTL's loaded build")
    ap.add_argument("--restart", action="store_true", help="ignore the checkpoint and re-extract everything")
    ap.add_argument("--limit", type=int, help="only the first N tables (smoke test)")
    ap.add_argument("--tables", help="comma-separated subset; resolved against the build-filtered list")
    ap.add_argument("--workers", type=int, default=config.MAX_WORKERS)
    ap.add_argument("--allow-non-forever", action="store_true", help="skip the Forever build filter")
    args = ap.parse_args(argv)

    started = time.monotonic()

    wtl_build = require_wtl()
    build = args.build or wtl_build
    log(f"WTL is up, serving {wtl_build}")

    if build != wtl_build:
        log(f"NOTE: extracting {build} while WTL has {wtl_build} loaded (reads from disk)")

    build_id = int(build.split(".")[-1])
    if not config.is_forever_build(build, build_id) and not args.allow_non_forever:
        log("")
        log(f"{build} is not a Forever build (need {config.FOREVER_VERSION_PATTERN} and buildId >= {config.FOREVER_MIN_BUILD_ID}).")
        log("`wow_classic_beta` is a recycled product code -- this is a different game.")
        log("Pass --allow-non-forever if you really mean it.")
        log("")
        raise SystemExit(2)

    out_dir = config.build_out_dir(build)
    plain_dir = out_dir / "db2"
    hotfixed_dir = out_dir / "db2_hotfixed"
    for d in (plain_dir, hotfixed_dir):
        d.mkdir(parents=True, exist_ok=True)

    ckpt_path = out_dir / ".extract_checkpoint.jsonl"
    if args.restart and ckpt_path.exists():
        ckpt_path.unlink()
        log("checkpoint discarded (--restart)")

    records = load_checkpoint(ckpt_path)
    if records:
        log(f"resuming: {len(records)} table(s) already done")

    tables = fetch_table_list(build)
    log(f"{len(tables)} table(s) in the build-filtered list")

    if args.tables:
        # Resolve against the build-filtered list rather than passing the
        # caller's spelling straight to /dbc/export. A name that is not in this
        # build must fail loudly here: exported, it would come back 404 and be
        # recorded as "not_in_build", which reads exactly like a table the
        # build genuinely does not ship.
        by_lower = {t.lower(): t for t in tables}
        wanted, missing = [], []
        for raw in args.tables.split(","):
            name = raw.strip()
            if not name:
                continue
            resolved = by_lower.get(name.lower())
            wanted.append(resolved) if resolved else missing.append(name)
        if missing:
            log("")
            log(f"not in {build}'s table list: {', '.join(missing)}")
            log("  Exported anyway these would 404 and be logged as not_in_build,")
            log("  which is indistinguishable from a genuine absence.")
            log("")
            raise SystemExit(2)
        seen = set()
        tables = [t for t in wanted if not (t in seen or seen.add(t))]
        log(f"--tables: extracting {len(tables)} of them")

    if args.limit:
        tables = tables[: args.limit]
        log(f"--limit {args.limit}: extracting {len(tables)}")

    layouthashes = fetch_layouthashes(build)
    log(f"{len(layouthashes)} layouthash(es) from /dbc/info")

    todo = [t for t in tables if t not in records]
    log(f"{len(todo)} to extract, {len(tables) - len(todo)} skipped from checkpoint")
    log("")

    done = 0
    total = len(todo)
    if total:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(extract_table, t, build, plain_dir, hotfixed_dir, layouthashes): t
                for t in todo
            }
            try:
                for fut in as_completed(futures):
                    rec = fut.result()
                    records[rec["table"]] = rec
                    append_checkpoint(ckpt_path, rec)
                    done += 1

                    mark = {"ok": " ", "empty": "0", "hotfix_only": "H",
                            "not_in_build": "-", "error": "!"}[rec["resolution"]]
                    delta = " *" if rec["hotfix_delta"] else "  "
                    rp, rh = rec["rows"]["plain"], rec["rows"]["hotfixed"]
                    log(
                        f"  [{done:4}/{total}] {mark}{delta} {rec['table'][:42]:42} "
                        f"{'' if rp is None else rp:>8} / {'' if rh is None else rh:<8} "
                        f"{rec['error'] or ''}"
                    )
            except KeyboardInterrupt:
                log("\ninterrupted -- checkpoint kept, re-run to resume")
                pool.shutdown(wait=False, cancel_futures=True)
                raise SystemExit(130)

    manifest = write_manifest(out_dir / "manifest.json", build, records, started, wtl_build)
    t = manifest["totals"]

    log("")
    log(f"  ok            {t['ok']}")
    log(f"  empty (204)   {t['empty']}")
    log(f"  hotfix only   {t['hotfix_only']}")
    log(f"  not in build  {t['not_in_build']}")
    log(f"  errors        {t['error']}")
    log(f"  hotfix delta  {t['hotfix_delta']}")
    log(f"  rows          {t['rows_plain']:,} plain / {t['rows_hotfixed']:,} hotfixed")
    log(f"  {manifest['durationSeconds']}s -> {out_dir}")

    return 1 if t["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
