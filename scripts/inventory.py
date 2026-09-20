#!/usr/bin/env python3
"""Inventory every file in the build -> out/<version>/files.csv.

Columns: fdid, path, size, encrypted, content_type

Pages /listfile/files (ordered by fdid so paging is deterministic and resume is
safe) and writes one row per file. Files whose content_type is `unk` -- WTL's
marker for a file it could not classify -- get a magic-byte pass to identify
them; everything else already carries a type from the listfile.

Two caveats, both verified against WTL's source rather than assumed:

  size      WTL exposes NO per-file size over HTTP. /size/data aggregates
            (`results[type] += fileSize`) and every other route works from
            Listfile.NameMap, which carries no size. Per-file sizes live in the
            CASC indices, which SizeController reads directly from disk. The
            column is emitted for schema stability but left empty.

  unnamed   /listfile/files iterates Listfile.NameMap, so it can only ever
            return NAMED files. recordsTotal (CASC.AvailableFDIDs.Count) minus
            the unfiltered recordsFiltered gives the unnamed count -- reported
            in the manifest as files_without_name.

Merges its results into the existing manifest.json under "inventory" rather
than overwriting what extract_db2.py wrote there.

Usage:
    python scripts/inventory.py
    python scripts/inventory.py --restart
    python scripts/inventory.py --page-size 25000
"""

import argparse
import csv
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
PAGE_TIMEOUT = 900
MAGIC_TIMEOUT = 120

# /listfile/files row indices (ListfileController.cs:267). Positional, no names.
I_FDID, I_PATH, I_TYPE, I_ENC = 0, 1, 4, 5

# Magic bytes -> content type, for files WTL could not classify.
MAGIC = {
    b"MD21": "m2",
    b"REVM": "wmo_or_adt",  # 'MVER' little-endian; both formats start with it
    b"BLP2": "blp",
}
MAGIC_PREFIX = {b"WDC": "db2"}  # WDC1..WDC5

CSV_HEADER = ["fdid", "path", "size", "encrypted", "content_type"]

_print_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, file=sys.stderr, flush=True)


# --- HTTP -------------------------------------------------------------------


def get(path, params=None, timeout=PAGE_TIMEOUT):
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
    """Same clear failure as extract_db2.py rather than a wall of tracebacks."""
    try:
        status, body = get("/casc/buildname", timeout=PROBE_TIMEOUT)
    except (urllib.error.URLError, OSError) as exc:
        log("")
        log(f"WTL is not reachable at {config.WTL_URL}")
        log(f"  {exc}")
        log("")
        log("  Start it first:   .\\scripts\\run-wtl.ps1")
        log("  It must finish loading the build before inventory can run.")
        log("")
        raise SystemExit(2)
    if status != 200 or not body:
        log(f"WTL answered /casc/buildname with HTTP {status}; expected 200 + a build name")
        raise SystemExit(2)
    return body.decode("utf-8").strip()


def fetch_page(start, length):
    """One page of /listfile/files, ordered by fdid ascending.

    GOTCHA: this envelope has NO `error` key (ListfileController.cs:942) --
    d["error"] would raise KeyError. Failures show up as a non-200 instead.
    """
    params = {
        "draw": 1,
        "start": start,
        "length": length,
        "order[0][column]": 0,  # fdid
        "order[0][dir]": "asc",  # deterministic paging -> safe resume
    }
    status, body = get("/listfile/files", params)
    if status != 200:
        raise RuntimeError(f"/listfile/files returned HTTP {status} at start={start}")
    payload = json.loads(body)
    return payload


# --- Magic-byte classification ----------------------------------------------


def classify(fdid):
    """First 4 bytes of a file -> content type.

    Reads only the header and abandons the rest of the response, so this never
    downloads whole files.
    """
    url = config.WTL_URL + "/casc/fdid?" + urllib.parse.urlencode({"fileDataID": fdid})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=MAGIC_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            head = resp.read(4)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return None

    if head in MAGIC:
        return MAGIC[head]
    for prefix, name in MAGIC_PREFIX.items():
        if head.startswith(prefix):
            return name
    return None


# --- Checkpoint -------------------------------------------------------------


def load_checkpoint(path, build):
    if not path.exists():
        return None
    try:
        ck = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if ck.get("build") != build:
        log(f"checkpoint is for {ck.get('build')}, not {build} -- ignoring")
        return None
    return ck


def save_checkpoint(path, ck):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".ckpt.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(ck, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --- Manifest ---------------------------------------------------------------


def merge_manifest(path, section):
    """Add our results without clobbering extract_db2.py's table data."""
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log(f"warning: {path.name} unreadable, starting a fresh one")
            existing = {}
    existing["inventory"] = section

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".manifest.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(existing, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --- Main -------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", help="version string; defaults to WTL's loaded build")
    ap.add_argument("--restart", action="store_true", help="ignore the checkpoint and start over")
    ap.add_argument("--page-size", type=int, default=50000)
    ap.add_argument("--workers", type=int, default=config.MAX_WORKERS)
    ap.add_argument("--allow-non-forever", action="store_true")
    args = ap.parse_args(argv)

    started = time.monotonic()

    wtl_build = require_wtl()
    build = args.build or wtl_build
    log(f"WTL is up, serving {wtl_build}")

    build_id = int(build.split(".")[-1])
    if not config.is_forever_build(build, build_id) and not args.allow_non_forever:
        log(f"\n{build} is not a Forever build -- `wow_classic_beta` is a recycled product code.")
        log("Pass --allow-non-forever if you really mean it.\n")
        raise SystemExit(2)

    out_dir = config.build_out_dir(build)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "files.csv"
    ckpt_path = out_dir / ".inventory_checkpoint.json"

    if args.restart:
        for p in (ckpt_path, csv_path):
            if p.exists():
                p.unlink()
        log("checkpoint and files.csv discarded (--restart)")

    ck = load_checkpoint(ckpt_path, build)
    start = ck["next_start"] if ck else 0
    written = ck["rows_written"] if ck else 0
    enc_counts = ck["encrypted_by_status"] if ck else {}
    type_counts = ck["by_content_type"] if ck else {}
    unk_fdids = ck["unclassified"] if ck else []

    # Probe for the totals before committing to a full pass.
    head = fetch_page(0, 1)
    total_available = head["recordsTotal"]
    total_named = head["recordsFiltered"]
    without_name = total_available - total_named

    log(f"  {total_available:,} files available in build (CASC.AvailableFDIDs)")
    log(f"  {total_named:,} of them carry a listfile name")
    log(f"  {without_name:,} lack a listfile name")
    log("")

    if start:
        log(f"resuming at row {start:,} ({written:,} already written)")

    mode = "a" if start else "w"
    fh = open(csv_path, mode, encoding="utf-8", newline="")
    writer = csv.writer(fh)
    if not start:
        writer.writerow(CSV_HEADER)

    try:
        while start < total_named:
            page = fetch_page(start, args.page_size)
            rows = page["data"]
            if not rows:
                break

            for r in rows:
                ctype = r[I_TYPE]
                enc = r[I_ENC]
                # size: unavailable over WTL's HTTP API -- see module docstring.
                writer.writerow([r[I_FDID], r[I_PATH], "", enc, ctype])
                type_counts[ctype] = type_counts.get(ctype, 0) + 1
                if enc:
                    enc_counts[enc] = enc_counts.get(enc, 0) + 1
                if ctype == "unk":
                    unk_fdids.append(int(r[I_FDID]))

            written += len(rows)
            start += len(rows)
            fh.flush()
            os.fsync(fh.fileno())
            save_checkpoint(
                ckpt_path,
                {
                    "build": build,
                    "next_start": start,
                    "rows_written": written,
                    "encrypted_by_status": enc_counts,
                    "by_content_type": type_counts,
                    "unclassified": unk_fdids,
                },
            )
            log(f"  {written:,} / {total_named:,} rows")
    except KeyboardInterrupt:
        log("\ninterrupted -- checkpoint kept, re-run to resume")
        raise SystemExit(130)
    finally:
        fh.close()

    # Magic-byte pass over whatever WTL could not classify.
    resolved = {}
    if unk_fdids:
        log("")
        log(f"classifying {len(unk_fdids):,} unclassified file(s) by magic bytes")
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(classify, f): f for f in unk_fdids}
            for fut in as_completed(futures):
                fdid = futures[fut]
                kind = fut.result()
                if kind:
                    resolved[fdid] = kind
        log(f"  identified {len(resolved):,}, still unknown {len(unk_fdids) - len(resolved):,}")

        # Keep the tallies consistent with the CSV we are about to rewrite,
        # otherwise the manifest reports the pre-classification `unk` count.
        for kind in resolved.values():
            type_counts[kind] = type_counts.get(kind, 0) + 1
        if resolved:
            type_counts["unk"] = type_counts.get("unk", 0) - len(resolved)
            if type_counts["unk"] <= 0:
                type_counts.pop("unk", None)

        if resolved:
            # Rewrite content_type for the rows we just identified.
            tmp_path = csv_path.with_suffix(".csv.tmp")
            with open(csv_path, "r", encoding="utf-8", newline="") as src, \
                 open(tmp_path, "w", encoding="utf-8", newline="") as dst:
                rd, wr = csv.reader(src), csv.writer(dst)
                wr.writerow(next(rd))
                for row in rd:
                    if row and row[0].isdigit() and int(row[0]) in resolved:
                        row[4] = resolved[int(row[0])]
                    wr.writerow(row)
            os.replace(tmp_path, csv_path)
    else:
        log("")
        log("no unclassified files -- magic-byte pass has nothing to do")

    encrypted_total = sum(enc_counts.values())
    section = {
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "durationSeconds": round(time.monotonic() - started, 1),
        "files_available": total_available,
        "files_named": total_named,
        "files_without_name": without_name,
        "rows_written": written,
        "encrypted": encrypted_total,
        "encrypted_baseline": 5035,
        "encrypted_matches_baseline": encrypted_total == 5035,
        "encrypted_by_status": enc_counts,
        "magic_classified": len(resolved),
        "still_unclassified": len(unk_fdids) - len(resolved),
        "size_column": "empty - WTL exposes no per-file size over HTTP; /size/data aggregates only",
        "top_content_types": dict(sorted(type_counts.items(), key=lambda kv: -kv[1])[:15]),
    }
    merge_manifest(out_dir / "manifest.json", section)

    log("")
    log(f"  rows written        {written:,}")
    log(f"  without a name      {without_name:,}")
    log(f"  encrypted           {encrypted_total:,} (baseline 5035: {'MATCH' if encrypted_total == 5035 else 'DIFFERS'})")
    for k, v in sorted(enc_counts.items()):
        log(f"    {k:22} {v:,}")
    log(f"  {section['durationSeconds']}s -> {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
