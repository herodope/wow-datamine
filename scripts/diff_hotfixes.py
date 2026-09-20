#!/usr/bin/env python3
"""Diff db2/ against db2_hotfixed/ within one build.

This answers a different question from a build-to-build diff: what Blizzard
changed on live realms WITHOUT shipping a client patch. The client bytes are
identical in both variants -- only the hotfix overlay differs.

Per table it reports added, removed and changed rows, with per-field
before/after for the changed ones, and attributes each affected record to a
hotfix push ID where /dbc/hotfixes/list knows one.

Tables whose plain export returned 204 but whose hotfixed export returned rows
(`resolution: hotfix_only`) have no plain CSV at all -- every row is an
addition.

Output: reports/hotfix_<version>.md

Usage:
    python scripts/diff_hotfixes.py
    python scripts/diff_hotfixes.py --build 1.60.1.69913
    python scripts/diff_hotfixes.py --max-rows 50
    python scripts/diff_hotfixes.py --no-attribution   # skip WTL entirely
"""

import argparse
import csv
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

import config

USER_AGENT = "wow-datamine/1.0 (+local datamining pipeline)"
HOTFIX_TIMEOUT = 600
PROBE_TIMEOUT = 10

CELL_LIMIT = 70  # truncate long field values in the report

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# --- Hotfix attribution -----------------------------------------------------


def fetch_hotfixes():
    """(table_lower, record_id) -> [(pushID, status, firstDetected), ...].

    GOTCHA: /dbc/hotfixes/list short-circuits on `!Request.QueryString.HasValue`
    and returns all zeros for a bare request. It MUST be given a query string.
    We send ?length=N, reading the real total from a probe first.

    GOTCHA: this envelope has no `error` key -- d["error"] raises KeyError.
    """
    def call(params):
        url = config.WTL_URL + "/dbc/hotfixes/list?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HOTFIX_TIMEOUT) as resp:
            return json.loads(resp.read())

    try:
        probe = call({"draw": 1, "start": 0, "length": 1})
    except (urllib.error.URLError, OSError) as exc:
        log(f"  WTL unreachable ({exc}) -- continuing without push-ID attribution")
        return {}, 0

    total = probe.get("recordsTotal", 0)
    if not total:
        log("  /dbc/hotfixes/list reports 0 records -- no attribution available")
        return {}, 0

    payload = call({"draw": 1, "start": 0, "length": total})
    rows = payload.get("data", [])

    # Row order (HotfixController.cs:56):
    # [pushID, tableName, recordID, build, status, firstDetected, tableIsKnown]
    index = defaultdict(list)
    for r in rows:
        try:
            index[(r[1].lower(), int(r[2]))].append((int(r[0]), int(r[4]), r[5]))
        except (ValueError, IndexError):
            continue
    return index, len(rows)


def status_label(status):
    """hotfixes.html: status == 1 renders "Valid", anything else "Invalidated"."""
    return "valid" if status == 1 else f"invalidated({status})"


# --- CSV loading ------------------------------------------------------------


def key_index(header):
    """Index of the ID column.

    DBCD emits columns in DBD definition order, and the ID column is NOT always
    first -- Achievement puts Description_lang first and ID at index 3. Keying
    on column 0 silently collapses rows that share that value (114 of
    Achievement's 233), which makes a diff quietly wrong rather than noisy.
    """
    if not header:
        return 0, None
    for i, name in enumerate(header):
        if name.strip().lower() == "id":
            return i, name
    return 0, header[0]


def load_csv(path):
    """(header, rows) -- raw, unkeyed. Keying happens once the header is known."""
    if not path.exists():
        return None, []
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            return None, []
        return header, [r for r in reader if r]


def key_rows(rows, idx, label):
    keyed = {}
    dupes = 0
    for row in rows:
        k = row[idx] if idx < len(row) else ""
        if k in keyed:
            dupes += 1
        keyed[k] = row
    if dupes:
        log(f"    warning: {label} has {dupes} duplicate key(s) on the ID column; last one wins")
    return keyed


def trunc(v):
    v = v.replace("\n", "\\n").replace("\r", "")
    return v if len(v) <= CELL_LIMIT else v[: CELL_LIMIT - 1] + "\u2026"


# --- Diff -------------------------------------------------------------------


def diff_table(table, plain_dir, hotfixed_dir):
    """Compare one table's two variants."""
    h_plain, plain_rows = load_csv(plain_dir / (table + ".csv"))
    h_hot, hot_rows = load_csv(hotfixed_dir / (table + ".csv"))

    idx, key_col = key_index(h_hot or h_plain)
    plain = key_rows(plain_rows, idx, f"{table}.csv (plain)")
    hot = key_rows(hot_rows, idx, f"{table}.csv (hotfixed)")

    plain_keys, hot_keys = set(plain), set(hot)
    def sort_key(k):
        return (0, int(k), "") if k.lstrip("-").isdigit() else (1, 0, k)

    added = sorted(hot_keys - plain_keys, key=sort_key)
    removed = sorted(plain_keys - hot_keys, key=sort_key)

    fields_comparable = h_plain is not None and h_hot is not None and h_plain == h_hot
    changed = []
    for key in plain_keys & hot_keys:
        if plain[key] != hot[key]:
            deltas = []
            if fields_comparable:
                for i, name in enumerate(h_hot):
                    before = plain[key][i] if i < len(plain[key]) else ""
                    after = hot[key][i] if i < len(hot[key]) else ""
                    if before != after:
                        deltas.append((name, before, after))
            changed.append((key, deltas))
    changed.sort(key=lambda kv: sort_key(kv[0]))

    return {
        "table": table,
        "header": h_hot or h_plain,
        "key_column": key_col,
        "fields_comparable": fields_comparable,
        "header_mismatch": (h_plain is not None and h_hot is not None and h_plain != h_hot),
        "plain_missing": h_plain is None,
        "rows_plain": len(plain),
        "rows_hotfixed": len(hot),
        "added": added,
        "removed": removed,
        "changed": changed,
        "magnitude": len(added) + len(removed) + len(changed),
        "hot_rows": hot,
        "plain_rows": plain,
    }


# --- Report -----------------------------------------------------------------


def attribution_for(index, table, key):
    try:
        entries = index.get((table.lower(), int(key)), [])
    except ValueError:
        return ""
    if not entries:
        return ""
    pairs = sorted({(push, status) for push, status, _detected in entries})
    return ", ".join(f"{push} ({status_label(status)})" for push, status in pairs)


def render(build, results, index, hotfix_rows, max_rows, manifest):
    L = []
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    total_added = sum(len(r["added"]) for r in results)
    total_removed = sum(len(r["removed"]) for r in results)
    total_changed = sum(len(r["changed"]) for r in results)

    L.append(f"# Hotfix diff \u2014 {build}")
    L.append("")
    L.append(
        "Live hotfix data versus the DB2s as shipped in the client. Both sides come "
        "from the same build, so every difference here is something Blizzard changed "
        "**without a client patch**."
    )
    L.append("")
    L.append(f"Generated {now}")
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| Tables differing | {len(results)} |")
    L.append(f"| Rows added | {total_added:,} |")
    L.append(f"| Rows removed | {total_removed:,} |")
    L.append(f"| Rows changed | {total_changed:,} |")
    L.append(f"| Hotfix records known to WTL | {hotfix_rows:,} |")
    if manifest:
        L.append(f"| Tables extracted | {manifest.get('totals', {}).get('tables', '?')} |")
    L.append("")
    L.append("---")
    L.append("")

    # Summary, ordered by magnitude.
    L.append("## Summary")
    L.append("")
    L.append("| Table | Added | Removed | Changed | Plain \u2192 Hotfixed | Push IDs |")
    L.append("|---|--:|--:|--:|---|---|")
    for r in results:
        pushes = set()
        for key in list(r["added"])[:2000] + list(r["removed"])[:2000] + [k for k, _ in r["changed"][:2000]]:
            try:
                for p, _s, _d in index.get((r["table"].lower(), int(key)), []):
                    pushes.add(p)
            except ValueError:
                pass
        push_str = ", ".join(str(p) for p in sorted(pushes)[:4])
        if len(pushes) > 4:
            push_str += f" +{len(pushes) - 4}"
        note = " *(hotfix-only)*" if r["plain_missing"] else ""
        L.append(
            f"| `{r['table']}`{note} | {len(r['added']):,} | {len(r['removed']):,} | "
            f"{len(r['changed']):,} | {r['rows_plain']:,} \u2192 {r['rows_hotfixed']:,} | {push_str or '\u2014'} |"
        )
    L.append("")
    L.append("---")
    L.append("")

    # Per-table detail.
    for r in results:
        t = r["table"]
        L.append(f"## {t}")
        L.append("")
        if r["plain_missing"]:
            L.append(
                "**Hotfix-only table.** The plain export returned 204 (no rows shipped "
                "in the build), so every row below exists only as live hotfix data."
            )
            L.append("")
        if r["header_mismatch"]:
            L.append(
                "> Column headers differ between the two variants, so per-field "
                "comparison is unavailable for this table."
            )
            L.append("")
        L.append(
            f"{len(r['added']):,} added \u00b7 {len(r['removed']):,} removed \u00b7 "
            f"{len(r['changed']):,} changed \u00b7 {r['rows_plain']:,} \u2192 {r['rows_hotfixed']:,} rows"
            + (f" \u00b7 keyed on `{r['key_column']}`" if r.get("key_column") else "")
        )
        L.append("")

        if r["added"]:
            L.append(f"### Added ({len(r['added']):,})")
            L.append("")
            L.append("| ID | Push ID | First fields |")
            L.append("|---|---|---|")
            for key in r["added"][:max_rows]:
                row = r["hot_rows"][key]
                preview = " \u00b7 ".join(trunc(c) for c in row[1:4] if c)
                L.append(f"| `{key}` | {attribution_for(index, t, key) or '\u2014'} | {preview or '\u2014'} |")
            if len(r["added"]) > max_rows:
                L.append(f"| \u2026 | | *{len(r['added']) - max_rows:,} more* |")
            L.append("")

        if r["removed"]:
            L.append(f"### Removed ({len(r['removed']):,})")
            L.append("")
            L.append("| ID | Push ID | First fields |")
            L.append("|---|---|---|")
            for key in r["removed"][:max_rows]:
                row = r["plain_rows"][key]
                preview = " \u00b7 ".join(trunc(c) for c in row[1:4] if c)
                L.append(f"| `{key}` | {attribution_for(index, t, key) or '\u2014'} | {preview or '\u2014'} |")
            if len(r["removed"]) > max_rows:
                L.append(f"| \u2026 | | *{len(r['removed']) - max_rows:,} more* |")
            L.append("")

        if r["changed"]:
            L.append(f"### Changed ({len(r['changed']):,})")
            L.append("")
            for key, deltas in r["changed"][:max_rows]:
                attr = attribution_for(index, t, key)
                L.append(f"**ID {key}**" + (f" \u2014 push {attr}" if attr else ""))
                L.append("")
                if deltas:
                    L.append("| Field | Before | After |")
                    L.append("|---|---|---|")
                    for name, before, after in deltas:
                        L.append(f"| `{name}` | `{trunc(before) or ''}` | `{trunc(after) or ''}` |")
                else:
                    L.append("*Row differs but per-field comparison unavailable.*")
                L.append("")
            if len(r["changed"]) > max_rows:
                L.append(f"*\u2026 {len(r['changed']) - max_rows:,} more changed row(s) not shown.*")
                L.append("")

        L.append("---")
        L.append("")

    return "\n".join(L) + "\n"


# --- Main -------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", help="version string; defaults to the newest out/ dir")
    ap.add_argument("--max-rows", type=int, default=25, help="rows shown per section per table")
    ap.add_argument("--no-attribution", action="store_true", help="skip the WTL hotfix lookup")
    ap.add_argument("--allow-non-forever", action="store_true")
    args = ap.parse_args(argv)

    build = args.build
    if not build:
        candidates = sorted(p.name for p in config.OUT_DIR.iterdir() if p.is_dir()) if config.OUT_DIR.exists() else []
        if not candidates:
            log(f"no extracted builds under {config.OUT_DIR}; run extract_db2.py first")
            raise SystemExit(2)
        build = candidates[-1]

    build_id = int(build.split(".")[-1])
    if not config.is_forever_build(build, build_id) and not args.allow_non_forever:
        log(f"\n{build} is not a Forever build. Pass --allow-non-forever to override.\n")
        raise SystemExit(2)

    out_dir = config.build_out_dir(build)
    plain_dir, hotfixed_dir = out_dir / "db2", out_dir / "db2_hotfixed"
    if not hotfixed_dir.is_dir():
        log(f"{hotfixed_dir} not found -- run extract_db2.py for {build} first")
        raise SystemExit(2)

    manifest = {}
    mpath = out_dir / "manifest.json"
    if mpath.exists():
        manifest = json.loads(mpath.read_text(encoding="utf-8"))

    # Prefer the manifest's delta flags; fall back to comparing every table.
    tables = [k for k, v in manifest.get("tables", {}).items() if v.get("hotfix_delta")]
    if tables:
        log(f"{len(tables)} table(s) flagged hotfix_delta in manifest.json")
    else:
        tables = sorted({p.stem for p in hotfixed_dir.glob("*.csv")} | {p.stem for p in plain_dir.glob("*.csv")})
        log(f"no delta flags in manifest; comparing all {len(tables)} table(s)")

    index, hotfix_rows = ({}, 0)
    if not args.no_attribution:
        log("fetching hotfix records for push-ID attribution...")
        index, hotfix_rows = fetch_hotfixes()
        log(f"  {hotfix_rows:,} hotfix record(s), {len(index):,} distinct (table, recordID) key(s)")

    log("")
    results = []
    for t in tables:
        r = diff_table(t, plain_dir, hotfixed_dir)
        if r["magnitude"] == 0:
            log(f"  {t}: byte-differs but no row-level change detected")
            continue
        results.append(r)
        log(f"  {t}: +{len(r['added'])} -{len(r['removed'])} ~{len(r['changed'])}")

    results.sort(key=lambda r: (-r["magnitude"], r["table"]))

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = config.REPORTS_DIR / f"hotfix_{build}.md"
    report_path.write_text(render(build, results, index, hotfix_rows, args.max_rows, manifest), encoding="utf-8")

    log("")
    log(f"  {len(results)} table(s) with row-level differences")
    log(f"  +{sum(len(r['added']) for r in results):,} / -{sum(len(r['removed']) for r in results):,} / ~{sum(len(r['changed']) for r in results):,}")
    log(f"  -> {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
