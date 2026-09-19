#!/usr/bin/env python3
"""Sync third-party reference data into vendor/. Idempotent; run every patch day.

Clones (or refreshes) WoWDBDefs, wow-listfile and TACTKeys, then downloads the
prebuilt community listfile CSV. Everything here lives under vendor/, which is
gitignored -- these are read-only mirrors of upstream, never edited locally.

Must run BEFORE WTL's definitionDir is configured or used: that setting points
at vendor/WoWDBDefs/definitions, and WTL silently falls back to its remote
manifest if the directory is missing.

Usage:
    python scripts/sync_refs.py
    python scripts/sync_refs.py --no-listfile     # repos only
    python scripts/sync_refs.py --only WoWDBDefs
"""

import argparse
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

import config

USER_AGENT = "wow-datamine/1.0 (+local datamining pipeline)"
TIMEOUT = 300


def log(msg):
    print(msg, file=sys.stderr)


def run_git(args, cwd=None):
    """Run git, returning (ok, combined_output)."""
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def head_short(repo_dir):
    ok, out = run_git(["rev-parse", "--short", "HEAD"], cwd=repo_dir)
    return out if ok else "?"


def clone_or_update(name, url):
    """Clone the repo if absent, otherwise fast-forward it to upstream.

    Shallow (depth 1) -- we only ever read the current state, never history.
    Updates use fetch + reset so a shallow clone stays consistent and the run
    is idempotent regardless of prior local state.
    """
    dest = config.VENDOR_DIR / name

    if not (dest / ".git").is_dir():
        if dest.exists():
            log(f"  {name}: path exists but is not a git repo -- skipping")
            return False
        log(f"  {name}: cloning...")
        ok, out = run_git(["clone", "--depth", "1", url, str(dest)])
        if not ok:
            log(f"  {name}: clone FAILED\n{out}")
            return False
        log(f"  {name}: cloned at {head_short(dest)}")
        return True

    before = head_short(dest)
    ok, out = run_git(["fetch", "--depth", "1", "origin", "HEAD"], cwd=dest)
    if not ok:
        log(f"  {name}: fetch FAILED (keeping existing clone at {before})\n{out}")
        return False
    ok, out = run_git(["reset", "--hard", "FETCH_HEAD"], cwd=dest)
    if not ok:
        log(f"  {name}: reset FAILED (still at {before})\n{out}")
        return False

    after = head_short(dest)
    log(f"  {name}: {'unchanged at ' + after if before == after else before + ' -> ' + after}")
    return True


def download_listfile():
    """Fetch the listfile CSV, skipping the transfer if upstream is unchanged.

    The ETag from the previous run is stored beside the CSV; a 304 means the
    release has not been republished since.
    """
    dest = config.LISTFILE_CSV
    etag_path = dest.with_suffix(dest.suffix + ".etag")

    headers = {"User-Agent": USER_AGENT}
    if dest.exists() and etag_path.exists():
        try:
            headers["If-None-Match"] = etag_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass

    req = urllib.request.Request(config.LISTFILE_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            etag = resp.headers.get("ETag", "")
            fd, tmp = tempfile.mkstemp(dir=str(dest.parent), prefix=".listfile.", suffix=".csv")
            try:
                with os.fdopen(fd, "wb") as fh:
                    total = 0
                    while chunk := resp.read(1 << 20):
                        fh.write(chunk)
                        total += len(chunk)
                os.replace(tmp, dest)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
        if etag:
            etag_path.write_text(etag, encoding="utf-8")
        log(f"  listfile: downloaded {total:,} bytes -> {dest.name}")
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            log(f"  listfile: unchanged upstream, kept {dest.name}")
            return True
        log(f"  listfile: HTTP {exc.code} {exc.reason}")
        return False
    except (urllib.error.URLError, OSError) as exc:
        log(f"  listfile: download FAILED: {exc}")
        return False


def report():
    """Summarize what is on disk after the sync."""
    log("")
    log("On disk:")

    defs = config.DEFINITIONS_DIR
    if defs.is_dir():
        dbds = sorted(defs.glob("*.dbd"))
        log(f"  {defs.name}/: {len(dbds)} .dbd files (e.g. {', '.join(p.name for p in dbds[:3])})")
    else:
        log(f"  {defs}: MISSING -- WTL will fall back to its remote manifest")

    if config.TACTKEYS_FILE.is_file():
        keys = sum(
            1
            for line in config.TACTKEYS_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and not line.startswith("#")
        )
        log(f"  TACTKeys/WoW.txt: {keys} keys")
    else:
        log(f"  {config.TACTKEYS_FILE}: MISSING")

    if config.LISTFILE_CSV.is_file():
        size = config.LISTFILE_CSV.stat().st_size
        with open(config.LISTFILE_CSV, encoding="utf-8", errors="replace") as fh:
            rows = sum(1 for _ in fh)
        log(f"  {config.LISTFILE_CSV.name}: {size:,} bytes, {rows:,} rows")
    else:
        log(f"  {config.LISTFILE_CSV}: MISSING")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-listfile", action="store_true", help="skip the listfile CSV download")
    ap.add_argument("--only", metavar="NAME", choices=sorted(config.REF_REPOS), help="sync a single repo")
    args = ap.parse_args(argv)

    config.VENDOR_DIR.mkdir(parents=True, exist_ok=True)

    repos = {args.only: config.REF_REPOS[args.only]} if args.only else config.REF_REPOS

    log(f"Syncing {len(repos)} repo(s) into {config.VENDOR_DIR}")
    failed = [name for name, url in repos.items() if not clone_or_update(name, url)]

    if not args.no_listfile and not args.only:
        if not download_listfile():
            failed.append("listfile")

    report()

    if failed:
        log(f"\nFAILED: {', '.join(failed)}")
        return 1
    log("\nall refs synced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
