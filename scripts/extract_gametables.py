#!/usr/bin/env python3
"""Extract the build's GameTables -> out/<version>/gametables/*.txt

GameTables are tab-separated text files under GameTables/ in CASC, not DB2s.
They hold the per-level scaling curves the client interpolates at runtime --
base mana by class and level, combat ratings, NPC damage by level, and so on.
A spell whose SpellEffect row carries flat base points and no coefficient is
not necessarily unscaled: the scaling may live here instead.

They are invisible to the DB2 pipeline. `/listfile/db2s` enumerates DBD
definitions, and a GameTable has none, so `extract_db2.py` can never see one.
WTL has a GameTableProvider internally but does not expose it over HTTP --
checked, there is no route in Controllers/. The files are ordinary CASC files
though, so `/casc/fdid` serves them verbatim, which is what this uses.

Discovery is via out/<version>/files.csv, so `inventory.py` must have run
first. That file lists every FDID in the build with its listfile path; the
GameTables are the rows whose path starts with `GameTables/`. At
1.60.1.69913 there are 42 of them.

Output is the raw TSV, byte-for-byte as shipped, one file per table. They are
small (tens of KB) and diffing them as text is the point, so there is no
conversion to CSV -- converting would lose the header row's exact spelling,
which is how the client names the columns.

Usage:
    python scripts/extract_gametables.py
    python scripts/extract_gametables.py --build 1.60.1.69913
    python scripts/extract_gametables.py --list
"""

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import config

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

USER_AGENT = "wow-datamine/1.0 (+local datamining pipeline)"
PROBE_TIMEOUT = 10
FETCH_TIMEOUT = 300

GAMETABLE_PREFIX = "gametables/"


def log(msg=""):
    print(msg, file=sys.stderr, flush=True)


def get(path, params=None, timeout=FETCH_TIMEOUT):
    url = config.WTL_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def require_wtl():
    try:
        status, body = get("/casc/buildname", timeout=PROBE_TIMEOUT)
    except (urllib.error.URLError, OSError) as exc:
        log(f"\nWTL is not reachable at {config.WTL_URL}\n  {exc}\n")
        log("  Start it first:   .\\scripts\\run-wtl.ps1")
        raise SystemExit(2)
    if status != 200 or not body:
        log(f"WTL answered /casc/buildname with HTTP {status}; expected 200 + a build name")
        raise SystemExit(2)
    return body.decode("utf-8").strip()


def discover(out_dir):
    """[(fdid, path)] for every GameTables/ file, from files.csv."""
    files_csv = out_dir / "files.csv"
    if not files_csv.exists():
        log(f"no {files_csv}")
        log("  run scripts/inventory.py first -- discovery needs the file list")
        raise SystemExit(2)

    found = []
    with open(files_csv, "r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if not header:
            raise SystemExit(f"{files_csv} is empty")
        fi = header.index("fdid")
        pi = header.index("path")
        for row in reader:
            if pi < len(row) and row[pi].lower().startswith(GAMETABLE_PREFIX):
                found.append((int(row[fi]), row[pi]))
    return sorted(found)


def fetch_one(fdid, path, dest_dir):
    """Write one GameTable verbatim. Returns (name, bytes) or (name, None)."""
    name = path.split("/")[-1]
    status, body = get("/casc/fdid", {"fileDataID": fdid})
    if status != 200 or not body:
        return name, None
    (dest_dir / name).write_bytes(body)
    return name, len(body)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", help="version string; defaults to WTL's loaded build")
    ap.add_argument("--workers", type=int, default=config.MAX_WORKERS)
    ap.add_argument("--list", action="store_true", help="list what would be extracted, fetch nothing")
    ap.add_argument("--allow-non-forever", action="store_true")
    args = ap.parse_args(argv)

    started = time.monotonic()
    wtl_build = require_wtl()
    build = args.build or wtl_build
    log(f"WTL is up, serving {wtl_build}")

    # /casc/fdid DOES take a build parameter, but it can only read a build that
    # is loaded or extracted to disk. Keep the same guard inventory.py uses so
    # one build's tables cannot be filed under another's name.
    if build != wtl_build:
        log(f"\nrefusing: --build is {build} but WTL has {wtl_build} loaded.")
        log(f"  Load it first via /casc/switchConfigs (hashes are in builds.json).")
        return 2

    build_id = int(build.split(".")[-1])
    if not config.is_forever_build(build, build_id) and not args.allow_non_forever:
        log(f"\n{build} is not a Forever build -- `{config.PRODUCT_CODE}` is recycled.")
        log("  Pass --allow-non-forever if you mean it.")
        return 2

    out_dir = config.build_out_dir(build)
    tables = discover(out_dir)
    log(f"  {len(tables)} GameTable(s) in {build}")

    if args.list:
        for fdid, path in tables:
            log(f"    {fdid:>9}  {path}")
        return 0

    if not tables:
        log("  nothing to extract")
        return 0

    dest = out_dir / "gametables"
    dest.mkdir(parents=True, exist_ok=True)

    written, failed = {}, []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_one, f, p, dest): (f, p) for f, p in tables}
        for fut in as_completed(futures):
            fdid, path = futures[fut]
            try:
                name, size = fut.result()
            except Exception as exc:                  # noqa: BLE001 - log, don't crash
                failed.append((fdid, path, str(exc)))
                continue
            if size is None:
                failed.append((fdid, path, "fetch returned no content"))
            else:
                written[name] = size

    for name in sorted(written):
        log(f"    {name:<44} {written[name]:>9,} bytes")
    for fdid, path, why in failed:
        log(f"    FAILED {fdid} {path}: {why}")

    section = {
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "durationSeconds": round(time.monotonic() - started, 1),
        "count": len(written),
        "failed": [{"fdid": f, "path": p, "reason": w} for f, p, w in failed],
        "bytes": sum(written.values()),
        "source": "/casc/fdid -- GameTables are not DB2s and have no DBD definition",
    }
    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists():
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            m = {}
        m["gametables"] = section
        manifest_path.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")

    log("")
    log(f"  {len(written)} written, {len(failed)} failed, {section['bytes']:,} bytes")
    log(f"  {section['durationSeconds']}s -> {dest}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
