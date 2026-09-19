#!/usr/bin/env python3
"""Poll the TACT version endpoint and merge Forever builds into builds.json.

This is the one step on patch day that is unrecoverable if skipped: once
Blizzard drops a build from the live version list, its buildConfig/cdnConfig
hashes cannot be recovered. Entries are therefore only ever added, never
overwritten.

Usage:
    python scripts/fetch_builds.py                  # us
    python scripts/fetch_builds.py --region eu
    python scripts/fetch_builds.py --all-regions
    python scripts/fetch_builds.py --dry-run
    python scripts/fetch_builds.py --from-file dump.txt --region us
"""

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone

import config

USER_AGENT = "wow-datamine/1.0 (+local datamining pipeline)"
TIMEOUT = 30


def log(msg):
    print(msg, file=sys.stderr)


# --- Endpoint ---------------------------------------------------------------


def fetch_versions(region):
    """Return the raw pipe-delimited body, or None on any network failure."""
    url = config.versions_url(region)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        log(f"[{region}] fetch failed: {exc}")
        return None


# --- Parsing ----------------------------------------------------------------


def parse_bpsv(body):
    """Parse Blizzard's pipe-separated version format into a list of dicts.

    Line 1 is a header of 'Name!TYPE:size' fields. Lines beginning with '#'
    are comments -- notably '## seqn = N', which is metadata, not a data row.
    """
    header = None
    rows = []

    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue  # '## seqn = N' and friends are comments, not data
        fields = line.split("|")
        if header is None:
            # 'Region!STRING:0' -> 'Region'
            header = [f.split("!", 1)[0].strip() for f in fields]
            continue
        if len(fields) != len(header):
            log(f"  skipping malformed row ({len(fields)} fields, expected {len(header)}): {line[:80]}")
            continue
        rows.append(dict(zip(header, fields)))

    if header is None:
        log("  no header line found in response")
    return rows


def row_to_build(row, region):
    """Normalize one parsed row. Returns None if it is unusable."""
    version = (row.get("VersionsName") or "").strip()
    raw_build_id = (row.get("BuildId") or "").strip()
    if not version or not raw_build_id:
        log(f"  skipping row with missing version/buildId: {row}")
        return None
    try:
        build_id = int(raw_build_id)
    except ValueError:
        log(f"  skipping row with non-numeric BuildId {raw_build_id!r}")
        return None

    return {
        "version": version,
        "buildId": build_id,
        "buildConfig": (row.get("BuildConfig") or "").strip(),
        "cdnConfig": (row.get("CDNConfig") or "").strip(),
        "region": (row.get("Region") or region).strip(),
    }


# --- builds.json ------------------------------------------------------------


def load_builds(path):
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log(f"refusing to continue: {path} exists but could not be read ({exc})")
        raise SystemExit(1)
    if not isinstance(data, dict):
        log(f"refusing to continue: {path} is not a JSON object")
        raise SystemExit(1)
    return data


def save_builds(path, builds):
    """Atomic write -- builds.json is not recoverable if we truncate it."""
    ordered = {k: builds[k] for k in sorted(builds, key=lambda k: int(k))}
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".builds.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(ordered, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --- Main -------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default=config.DEFAULT_REGION, choices=config.REGIONS)
    ap.add_argument("--all-regions", action="store_true", help=f"query all of {', '.join(config.REGIONS)}")
    ap.add_argument("--dry-run", action="store_true", help="report what would be added, write nothing")
    ap.add_argument("--from-file", metavar="PATH", help="parse a saved endpoint response instead of fetching")
    args = ap.parse_args(argv)

    regions = list(config.REGIONS) if args.all_regions else [args.region]

    builds = load_builds(config.BUILDS_JSON)
    existing = len(builds)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    preexisting = set(builds)
    added, skipped_foreign, already_known, dup_rows, reached = 0, 0, 0, 0, 0

    for region in regions:
        if args.from_file:
            with open(args.from_file, encoding="utf-8") as fh:
                body = fh.read()
        else:
            body = fetch_versions(region)
        if body is None:
            continue
        reached += 1

        rows = parse_bpsv(body)
        log(f"[{region}] {len(rows)} data row(s)")

        for row in rows:
            build = row_to_build(row, region)
            if build is None:
                continue

            # Recycled product code: anything not matching the Forever rule is
            # a different game entirely. Discard silently (logged at debug).
            if not config.is_forever_build(build["version"], build["buildId"]):
                log(f"  discard (not Forever): {build['version']} build {build['buildId']}")
                skipped_foreign += 1
                continue

            key = str(build["buildId"])
            if key in preexisting:
                already_known += 1
                continue
            if key in builds:
                # The versions endpoint lists one row per region for the same
                # build. First region wins; the rest are duplicates, not news.
                dup_rows += 1
                continue

            build["firstSeen"] = now
            builds[key] = build
            added += 1
            log(f"  + {build['version']} build {build['buildId']} [{build['region']}]")

    if not reached:
        log("no region responded; builds.json left untouched")
        return 2

    log("")
    log(
        f"{existing} already in builds.json, {added} added, "
        f"{already_known} unchanged, {dup_rows} duplicate region row(s), "
        f"{skipped_foreign} discarded as non-Forever"
    )

    if args.dry_run:
        log("--dry-run: builds.json not written")
        return 0

    if added:
        save_builds(config.BUILDS_JSON, builds)
        log(f"wrote {config.BUILDS_JSON}")
    else:
        log("nothing new; builds.json unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
