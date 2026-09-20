#!/usr/bin/env python3
"""Diff two Forever builds -- what changed in the client itself.

Compares `db2/` against `db2/`, NOT `db2_hotfixed/`. The hotfixed variant folds
live tuning into the client data, so diffing it would report a hotfix Blizzard
pushed last week as though it shipped in this patch. Client changes and live
changes are different questions: `diff_hotfixes.py` answers the second.

Per table: added, removed and changed rows with per-field before/after. Rows are
keyed on the column named `ID` (never column 0 -- see Conventions in CLAUDE.md;
DBCD emits DBD-definition order and Achievement puts ID at index 3). Whether a
table changed at all is decided by a content hash of the two files, not a row
count, because a table can change values without changing its row count.

Also diffs `files.csv` for added/removed/retyped files and reports the
encrypted-file count broken out by status. **A fall in EncryptedUnknownKey is
the key-leak signal** -- content that was locked has become readable.

Output: reports/<from>_to_<to>.md, high-signal tables in full, everything else
collapsed.

Usage:
    python scripts/diff_builds.py 1.60.1.69913 1.60.2.70050
    python scripts/diff_builds.py <from> <to> --max-rows 40
    python scripts/diff_builds.py <from> <to> --allow-non-forever
"""

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone

import config
import contamination
from diff_hotfixes import key_index, key_rows, load_csv, trunc

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# Tables worth a full section. Everything else is collapsed to one line so the
# report stays readable -- a build diff touches hundreds of tables, most of them
# generated data nobody reads row by row.
HIGH_SIGNAL = ["Spell", "Item", "Creature", "QuestV2", "Map", "AreaTable"]

ENCRYPTION_STATUSES = ["EncryptedUnknownKey", "EncryptedKnownKey", "EncryptedMixed", "EncryptedButNot"]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def file_hash(path):
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- Table diffing ----------------------------------------------------------


def load_layouthashes(build):
    """table -> layouthash, from that build's manifest.json.

    The layouthash comes from the DB2 header itself, so it tracks the *client's*
    record layout. The CSV header comes from WoWDBDefs, so it tracks the
    *definition*. They move independently, and either moving invalidates a
    positional field-by-field comparison.
    """
    path = config.build_out_dir(build) / "manifest.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log(f"  warning: {path} unreadable; layouthash checks disabled for {build}")
        return {}
    return {k: v.get("layouthash") for k, v in data.get("tables", {}).items() if v.get("layouthash")}


def diff_table(table, old_dir, new_dir, max_field_rows, lh_old=None, lh_new=None):
    """Compare one table across builds. Returns None if byte-identical."""
    old_path, new_path = old_dir / f"{table}.csv", new_dir / f"{table}.csv"
    h_old, h_new = file_hash(old_path), file_hash(new_path)

    if h_old is not None and h_old == h_new:
        return None  # unchanged; skip the parse entirely

    header_old, rows_old = load_csv(old_path)
    header_new, rows_new = load_csv(new_path)

    only_in = None
    if header_old is None and header_new is not None:
        only_in = "new"
    elif header_new is None and header_old is not None:
        only_in = "old"
    elif header_old is None and header_new is None:
        return None

    idx, key_col = key_index(header_new or header_old)
    old = key_rows(rows_old, idx, f"{table}.csv ({old_dir.parent.name})") if header_old else {}
    new = key_rows(rows_new, idx, f"{table}.csv ({new_dir.parent.name})") if header_new else {}

    def sort_key(k):
        return (0, int(k), "") if k.lstrip("-").isdigit() else (1, 0, k)

    added = sorted(set(new) - set(old), key=sort_key)
    removed = sorted(set(old) - set(new), key=sort_key)

    # --- schema-change gating -------------------------------------------------
    # Never field-compare across a layout change: the columns a value sits in
    # may have moved, so a positional diff reports every row as changed and
    # every field as different. Two independent signals, either one fatal:
    #   layouthash differs  -> the client's record layout changed
    #   CSV header differs  -> the WoWDBDefs definition changed
    # A definition update can rename or add columns with no layouthash change
    # (unk_<offset> becoming a real name), so the header check is not redundant.
    layout_changed = bool(lh_old and lh_new and lh_old != lh_new)
    header_changed = header_old is not None and header_new is not None and header_old != header_new
    column_count_changed = (
        header_old is not None and header_new is not None and len(header_old) != len(header_new)
    )

    reasons = []
    if layout_changed:
        reasons.append(f"layouthash {lh_old} → {lh_new}")
    if column_count_changed:
        reasons.append(f"column count {len(header_old)} → {len(header_new)}")
    elif header_changed:
        renamed = [(a, b) for a, b in zip(header_old, header_new) if a != b]
        reasons.append(
            f"{len(renamed)} column(s) renamed in the definition"
            + (f" (e.g. {renamed[0][0]} → {renamed[0][1]})" if renamed else "")
        )

    fields_comparable = not layout_changed and not header_changed
    # Positional row equality is only meaningful when the columns line up. A
    # rename with identical positions keeps values in place, so row-level
    # comparison survives that; a layout or column-count change does not.
    rows_comparable = not layout_changed and not column_count_changed

    changed = []
    if rows_comparable:
        for key in set(old) & set(new):
            if old[key] != new[key]:
                deltas = []
                if fields_comparable and len(changed) < max_field_rows:
                    for i, name in enumerate(header_new):
                        before = old[key][i] if i < len(old[key]) else ""
                        after = new[key][i] if i < len(new[key]) else ""
                        if before != after:
                            deltas.append((name, before, after))
                changed.append((key, deltas))
        changed.sort(key=lambda kv: sort_key(kv[0]))

    return {
        "table": table,
        "header": header_new or header_old,
        "key_column": key_col,
        "only_in": only_in,
        "layouthash_old": lh_old,
        "layouthash_new": lh_new,
        "layout_changed": layout_changed,
        "header_changed": header_changed,
        "schema_changed": layout_changed or header_changed,
        "schema_reasons": reasons,
        "fields_comparable": fields_comparable,
        "rows_comparable": rows_comparable,
        "columns_added": [c for c in (header_new or []) if c not in (header_old or [])],
        "columns_removed": [c for c in (header_old or []) if c not in (header_new or [])],
        "rows_old": len(old),
        "rows_new": len(new),
        "added": added,
        "removed": removed,
        "changed": changed,
        "magnitude": len(added) + len(removed) + len(changed),
        # Names the contamination rules expect for the two sides.
        "old_rows": old,
        "new_rows": new,
    }


# --- files.csv --------------------------------------------------------------


def load_files_csv(path):
    """fdid -> (path, encrypted, content_type). Absent file yields None."""
    if not path.exists():
        return None
    out = {}
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if not header:
            return {}
        cols = {n.strip().lower(): i for i, n in enumerate(header)}
        fi, pi = cols.get("fdid", 0), cols.get("path", 1)
        ei, ci = cols.get("encrypted", 3), cols.get("content_type", 4)
        for r in reader:
            if len(r) > max(fi, pi, ei, ci):
                out[r[fi]] = (r[pi], r[ei], r[ci])
    return out


def diff_files(old_dir, new_dir):
    old = load_files_csv(old_dir.parent / "files.csv")
    new = load_files_csv(new_dir.parent / "files.csv")
    if old is None or new is None:
        missing = [d.parent.name for d, v in ((old_dir, old), (new_dir, new)) if v is None]
        return {"unavailable": f"files.csv missing for {', '.join(missing)} -- run inventory.py"}

    added = sorted(set(new) - set(old), key=lambda k: int(k) if k.isdigit() else 0)
    removed = sorted(set(old) - set(new), key=lambda k: int(k) if k.isdigit() else 0)
    retyped = [(k, old[k][2], new[k][2]) for k in set(old) & set(new) if old[k][2] != new[k][2]]
    renamed = [(k, old[k][0], new[k][0]) for k in set(old) & set(new) if old[k][0] != new[k][0]]

    def enc_counts(d):
        c = {}
        for _p, e, _t in d.values():
            if e:
                c[e] = c.get(e, 0) + 1
        return c

    return {
        "old_total": len(old), "new_total": len(new),
        "added": added, "removed": removed, "retyped": retyped, "renamed": renamed,
        "enc_old": enc_counts(old), "enc_new": enc_counts(new),
        "old_map": old, "new_map": new,
    }


# --- Report -----------------------------------------------------------------


def render_encryption(fd):
    L = ["## Encryption", ""]
    if "unavailable" in fd:
        L += [fd["unavailable"], "", "---", ""]
        return L

    old_c, new_c = fd["enc_old"], fd["enc_new"]
    # Known statuses first, in a fixed order, then anything unexpected.
    # NB the parentheses: `a | b - c` binds as `a | (b - c)`, which duplicates.
    statuses = [s for s in ENCRYPTION_STATUSES if s in old_c or s in new_c]
    statuses += sorted((set(old_c) | set(new_c)) - set(statuses))

    L.append("| Status | Before | After | Delta |")
    L.append("|---|--:|--:|--:|")
    for s in statuses:
        a, b = old_c.get(s, 0), new_c.get(s, 0)
        L.append(f"| `{s}` | {a:,} | {b:,} | {b - a:+,} |")
    ta, tb = sum(old_c.values()), sum(new_c.values())
    L.append(f"| **Total** | **{ta:,}** | **{tb:,}** | **{tb - ta:+,}** |")
    L.append("")

    unk_delta = new_c.get("EncryptedUnknownKey", 0) - old_c.get("EncryptedUnknownKey", 0)
    if unk_delta < 0:
        L.append(
            f"⚠️ **`EncryptedUnknownKey` fell by {abs(unk_delta):,}.** Keys leaked or "
            "content was unlocked — files that could not be decoded in the previous "
            "build now can. This is the highest-value early signal in beta datamining; "
            "check what those files are before anything else in this report."
        )
    elif unk_delta > 0:
        L.append(
            f"`EncryptedUnknownKey` rose by {unk_delta:,} — new encrypted content was "
            "added, which usually means unreleased content shipped in a locked state."
        )
    else:
        L.append("`EncryptedUnknownKey` is unchanged — no keys leaked between these builds.")
    L += ["", "---", ""]
    return L


def diff_gametables(old_dir, new_dir):
    """Compare out/<build>/gametables/ between two builds.

    GameTables are tab-separated text, not DB2s, so none of the DB2 machinery
    applies -- no layouthash, no DBD definition, no ID column. They are small
    and their header row names the columns exactly as the client does, so this
    compares them as text and reports per-file added/removed/changed plus the
    changed column names.

    They matter for scaling questions: SpellScaling ships as a 204-empty DB2
    and the per-level curves live in SpellScaling.txt here instead.
    """
    # Callers pass the db2/ directory (see resolve()), same as diff_files.
    old_gt, new_gt = old_dir.parent / "gametables", new_dir.parent / "gametables"
    if not old_gt.is_dir() and not new_gt.is_dir():
        return None

    def read(d):
        out = {}
        if not d.is_dir():
            return out
        for f in sorted(d.glob("*.txt")):
            raw = f.read_bytes()
            try:
                lines = raw.decode("utf-8", errors="replace").splitlines()
            except Exception:                       # noqa: BLE001
                lines = []
            header = lines[0].split("\t") if lines else []
            out[f.name] = {"sha": hashlib.sha256(raw).hexdigest(),
                           "rows": max(0, len(lines) - 1),
                           "header": header}
        return out

    old, new = read(old_gt), read(new_gt)
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = []
    for name in sorted(set(old) & set(new)):
        if old[name]["sha"] == new[name]["sha"]:
            continue
        cols_old, cols_new = old[name]["header"], new[name]["header"]
        changed.append({
            "name": name,
            "rows_old": old[name]["rows"],
            "rows_new": new[name]["rows"],
            "cols_added": [c for c in cols_new if c not in cols_old],
            "cols_removed": [c for c in cols_old if c not in cols_new],
        })
    return {"old_count": len(old), "new_count": len(new),
            "added": added, "removed": removed, "changed": changed}


def render_gametables(gt):
    """The "GameTables" report section."""
    L = ["## GameTables", ""]
    if gt is None:
        L += [
            "Not extracted for one or both builds. GameTables are tab-separated "
            "text files under `GameTables/` in CASC, not DB2s -- `/listfile/db2s` "
            "cannot list them and WTL exposes no route, so `extract_db2.py` never "
            "sees them. Run `scripts/extract_gametables.py` (after `inventory.py`, "
            "which it needs for discovery).",
            "",
            "**This is a gap, not a clean result.** `SpellScaling` ships as a "
            "204-empty DB2 and the per-level curves live in `SpellScaling.txt` "
            "here, so a scaling question cannot be answered without them.",
            "", "---", "",
        ]
        return L

    L.append(f"{gt['old_count']} -> {gt['new_count']} table(s).")
    L.append("")
    if not (gt["added"] or gt["removed"] or gt["changed"]):
        L += ["No GameTable changed.", "", "---", ""]
        return L

    if gt["added"]:
        L += [f"**Added:** {', '.join('`' + n + '`' for n in gt['added'])}", ""]
    if gt["removed"]:
        L += [f"**Removed:** {', '.join('`' + n + '`' for n in gt['removed'])}", ""]
    if gt["changed"]:
        L += ["| Table | Rows | Columns added | Columns removed |", "|---|---|---|---|"]
        for c in gt["changed"]:
            rows = (str(c["rows_old"]) if c["rows_old"] == c["rows_new"]
                    else f"{c['rows_old']} -> {c['rows_new']}")
            L.append(
                f"| `{c['name']}` | {rows} | "
                f"{', '.join('`' + x + '`' for x in c['cols_added']) or '—'} | "
                f"{', '.join('`' + x + '`' for x in c['cols_removed']) or '—'} |"
            )
        L.append("")
    L += ["---", ""]
    return L


def render_files(fd):
    L = ["## Files", ""]
    if "unavailable" in fd:
        L += [fd["unavailable"], "", "---", ""]
        return L

    L.append("| | |")
    L.append("|---|--:|")
    L.append(f"| Files before | {fd['old_total']:,} |")
    L.append(f"| Files after | {fd['new_total']:,} |")
    L.append(f"| Added | {len(fd['added']):,} |")
    L.append(f"| Removed | {len(fd['removed']):,} |")
    L.append(f"| Retyped | {len(fd['retyped']):,} |")
    L.append(f"| Renamed | {len(fd['renamed']):,} |")
    L.append("")

    if fd["retyped"]:
        L.append("Retyped files (content type changed — often a file gaining a listfile name):")
        L.append("")
        L.append("| FDID | Before | After | Path |")
        L.append("|---|---|---|---|")
        for k, a, b in fd["retyped"][:40]:
            L.append(f"| `{k}` | `{a}` | `{b}` | {trunc(fd['new_map'][k][0])} |")
        if len(fd["retyped"]) > 40:
            L.append(f"| … | | | *{len(fd['retyped']) - 40:,} more* |")
        L.append("")
    L += ["---", ""]
    return L


def _col_summary(names, sign):
    if not names:
        return ""
    shown = ", ".join(f"`{c}`" for c in names[:3])
    return f"{sign}{shown}" + ("…" if len(names) > 3 else "")


def render_schema_changes(results):
    """Tables whose schema moved. Prominent, because it limits what the rest of
    the report is allowed to claim."""
    hits = [r for r in results if r.get("schema_changed")]
    L = ["## Schema changes", ""]
    if not hits:
        L += [
            "No layouthash or column changes. Field-level diffs below are valid for "
            "every table.",
            "", "---", "",
        ]
        return L

    L.append(
        f"⚠️ **{len(hits)} table(s) changed schema between these builds, and "
        "field-level comparison is suppressed for them.** When columns move, a "
        "positional diff reports every row as changed and every field as different "
        "— noise, not signal. Row-level added/removed is still reported where it "
        "stays meaningful."
    )
    L.append("")
    L.append("| Table | Reason | Field diffs | Changed rows | Columns |")
    L.append("|---|---|---|---|---|")
    for r in hits:
        cols = " / ".join(
            x for x in (_col_summary(r.get("columns_added"), "+"),
                        _col_summary(r.get("columns_removed"), "−")) if x
        )
        changed_cell = "suppressed" if not r["rows_comparable"] else f"{len(r['changed']):,}"
        L.append(
            f"| `{r['table']}` | {'; '.join(r['schema_reasons']) or 'schema differs'} | "
            f"{'ok' if r['fields_comparable'] else 'suppressed'} | {changed_cell} | "
            f"{cols or '—'} |"
        )
    L += ["", "---", ""]
    return L


def render_table_section(r, max_rows):
    t = r["table"]
    L = [f"## {t}", ""]
    if r["only_in"] == "new":
        L.append("**New table** — not present in the previous build.")
        L.append("")
    elif r["only_in"] == "old":
        L.append("**Table removed** — not present in the new build.")
        L.append("")
    if r.get("schema_changed"):
        detail = (
            "Row-level changed detection is also suppressed: the columns no longer line "
            "up positionally, so added/removed rows below are the only valid row-level "
            "output."
            if not r["rows_comparable"] else
            "Row-level comparison still applies, since the columns line up."
        )
        L.append(
            "> ⚠️ **Schema changed — field-level diffs suppressed for this "
            f"table.** {'; '.join(r['schema_reasons'])}. {detail}"
        )
        L.append("")
    L.append(
        f"{len(r['added']):,} added · {len(r['removed']):,} removed · "
        f"{len(r['changed']):,} changed · {r['rows_old']:,} → {r['rows_new']:,} rows"
        + (f" · keyed on `{r['key_column']}`" if r.get("key_column") else "")
    )
    L.append("")

    for label, keys, side in (("Added", r["added"], "new_rows"), ("Removed", r["removed"], "old_rows")):
        if not keys:
            continue
        L.append(f"### {label} ({len(keys):,})")
        L.append("")
        L.append("| ID | First fields |")
        L.append("|---|---|")
        for key in keys[:max_rows]:
            row = r[side].get(key, [])
            preview = " · ".join(trunc(c) for c in row[1:4] if c)
            L.append(f"| `{key}` | {preview or '—'} |")
        if len(keys) > max_rows:
            L.append(f"| … | *{len(keys) - max_rows:,} more* |")
        L.append("")

    if r["changed"]:
        L.append(f"### Changed ({len(r['changed']):,})")
        L.append("")
        shown = 0
        for key, deltas in r["changed"]:
            if shown >= max_rows:
                break
            if not deltas:
                continue
            L.append(f"**ID {key}**")
            L.append("")
            L.append("| Field | Before | After |")
            L.append("|---|---|---|")
            for name, before, after in deltas:
                L.append(f"| `{name}` | `{trunc(before)}` | `{trunc(after)}` |")
            L.append("")
            shown += 1
        if len(r["changed"]) > shown:
            L.append(f"*… {len(r['changed']) - shown:,} more changed row(s) not shown.*")
            L.append("")
    L += ["---", ""]
    return L


def render(old_build, new_build, results, findings, fd, unchanged_count, max_rows, gt=None, coverage=None):
    L = []
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    high = [r for r in results if r["table"] in HIGH_SIGNAL]
    rest = [r for r in results if r["table"] not in HIGH_SIGNAL]

    L.append(f"# Build diff — {old_build} → {new_build}")
    L.append("")
    L.append(
        "Client data only: `db2/` against `db2/`, with the hotfix overlay excluded. "
        "Live tuning is reported separately by `diff_hotfixes.py`, so everything here "
        "shipped in the client."
    )
    L.append("")
    L.append(f"Generated {now}")
    L.append("")
    L.append("| | |")
    L.append("|---|--:|")
    L.append(f"| Tables changed | {len(results):,} |")
    L.append(f"| Tables unchanged | {unchanged_count:,} |")
    L.append(f"| Rows added | {sum(len(r['added']) for r in results):,} |")
    L.append(f"| Rows removed | {sum(len(r['removed']) for r in results):,} |")
    L.append(f"| Rows changed | {sum(len(r['changed']) for r in results):,} |")
    L.append(f"| New tables | {sum(1 for r in results if r['only_in'] == 'new'):,} |")
    L.append(f"| Removed tables | {sum(1 for r in results if r['only_in'] == 'old'):,} |")
    L.append("")
    L.append("---")
    L.append("")

    L += render_encryption(fd)
    L += render_files(fd)
    L += render_gametables(gt)
    L += render_schema_changes(results)
    L += contamination.render_markdown(findings, coverage)

    L.append("## Summary")
    L.append("")
    L.append("| Table | Added | Removed | Changed | Rows |")
    L.append("|---|--:|--:|--:|---|")
    for r in sorted(results, key=lambda r: (-r["magnitude"], r["table"])):
        note = ""
        if r["only_in"] == "new":
            note = " *(new)*"
        elif r["only_in"] == "old":
            note = " *(removed)*"
        star = " ⭐" if r["table"] in HIGH_SIGNAL else ""
        if r.get("schema_changed"):
            note += " ⚠️"
        L.append(
            f"| `{r['table']}`{note}{star} | {len(r['added']):,} | {len(r['removed']):,} | "
            f"{len(r['changed']):,} | {r['rows_old']:,} → {r['rows_new']:,} |"
        )
    L.append("")
    L.append(
        f"⭐ = high-signal table, detailed below. ⚠️ = schema changed, "
        f"field diffs suppressed. {unchanged_count:,} table(s) were byte-identical "
        f"and are omitted."
    )
    L.append("")
    L.append("---")
    L.append("")

    for r in sorted(high, key=lambda r: (HIGH_SIGNAL.index(r["table"]),)):
        L += render_table_section(r, max_rows)

    if rest:
        L.append("## Other changed tables")
        L.append("")
        L.append(
            f"{len(rest):,} further table(s) changed. They are listed in the summary "
            "above; re-run with `--detail <table>` for row-level output on any of them."
        )
        L.append("")
    return "\n".join(L) + "\n"


# --- Main -------------------------------------------------------------------


def resolve(build):
    d = config.build_out_dir(build) / "db2"
    if not d.is_dir():
        log(f"{d} not found -- run extract_db2.py for {build} first")
        raise SystemExit(2)
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("from_build", help="earlier version, e.g. 1.60.1.69913")
    ap.add_argument("to_build", help="later version")
    ap.add_argument("--max-rows", type=int, default=25)
    ap.add_argument("--detail", action="append", default=[], help="also render this table in full")
    ap.add_argument("--no-contamination", action="store_true")
    ap.add_argument("--allow-non-forever", action="store_true")
    args = ap.parse_args(argv)

    for b in (args.from_build, args.to_build):
        try:
            bid = int(b.split(".")[-1])
        except ValueError:
            log(f"cannot parse a build id from {b!r}")
            raise SystemExit(2)
        if not config.is_forever_build(b, bid) and not args.allow_non_forever:
            log("")
            log(f"{b} is not a Forever build (need {config.FOREVER_VERSION_PATTERN} and buildId >= {config.FOREVER_MIN_BUILD_ID}).")
            log("`wow_classic_beta` is a recycled product code -- diffing across that")
            log("boundary compares two different games and produces meaningless noise.")
            log("Pass --allow-non-forever if you really mean it.")
            log("")
            raise SystemExit(2)

    old_dir, new_dir = resolve(args.from_build), resolve(args.to_build)

    global HIGH_SIGNAL
    HIGH_SIGNAL = HIGH_SIGNAL + [t for t in args.detail if t not in HIGH_SIGNAL]

    lh_old = load_layouthashes(args.from_build)
    lh_new = load_layouthashes(args.to_build)
    if not lh_old or not lh_new:
        log("  warning: layouthashes unavailable for at least one build; relying on "
            "CSV headers alone to detect schema changes")

    tables = sorted({p.stem for p in old_dir.glob("*.csv")} | {p.stem for p in new_dir.glob("*.csv")})
    log(f"{len(tables)} table(s) across both builds")

    results, unchanged = [], 0
    for t in tables:
        r = diff_table(t, old_dir, new_dir, args.max_rows, lh_old.get(t), lh_new.get(t))
        if r is None:
            unchanged += 1
            continue
        # A schema change must never be collapsed into "unchanged". A table can
        # move its columns with no row differences at all -- suppressed field
        # diffs leave magnitude at 0 -- and dropping it here would hide exactly
        # the thing that invalidates comparison.
        if r["magnitude"] == 0 and not r["only_in"] and not r["schema_changed"]:
            unchanged += 1
            continue
        results.append(r)
        flag = " [SCHEMA CHANGED - field diffs suppressed]" if r["schema_changed"] else ""
        log(f"  {t}: +{len(r['added'])} -{len(r['removed'])} ~{len(r['changed'])}{flag}")

    log(f"  {len(results)} changed, {unchanged} unchanged")

    findings, coverage = [], None
    if not args.no_contamination:
        def load_table(name):
            for d in (new_dir, old_dir):
                header, rows = load_csv(d / f"{name}.csv")
                if header is not None:
                    idx, _ = key_index(header)
                    return header, {r[idx]: r for r in rows if idx < len(r)}
            return None, {}

        findings, _ref, coverage = contamination.scan(results, load_table)
        log("")
        if coverage["verdict"] == "not_scanned":
            # A zero from rules that could not read the data is not a clean
            # result. Say so here as well as in the report.
            log(f"  contamination: NOT SCANNED -- {coverage['rows_submitted']:,} row(s) across "
                f"{coverage['tables_submitted']} table(s), no rule could read any of them")
            for rule in coverage["rules_never_applicable"]:
                log(f"    skipped: {rule}")
        elif findings:
            log(f"  {len(findings)} suspected retail-contamination finding(s)")
            for f in findings:
                log(f"    [{f['confidence'].upper():6}] {f['rule']}: {f['table']} {f['record']}")
            if coverage["rules_never_applicable"]:
                log(f"    (rules never applicable here: {', '.join(coverage['rules_never_applicable'])})")
        else:
            log("  no suspected retail contamination")

    fd = diff_files(old_dir, new_dir)
    if "unavailable" not in fd:
        log(f"  files: +{len(fd['added']):,} -{len(fd['removed']):,} retyped {len(fd['retyped']):,}")

    gt = diff_gametables(old_dir, new_dir)
    if gt is None:
        log("  gametables: NOT EXTRACTED for one or both builds -- run extract_gametables.py")
    else:
        log(f"  gametables: {gt['old_count']} -> {gt['new_count']}, "
            f"+{len(gt['added'])} -{len(gt['removed'])} changed {len(gt['changed'])}")

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.report_path(args.from_build, args.to_build)
    path.write_text(render(args.from_build, args.to_build, results, findings, fd, unchanged, args.max_rows, gt, coverage), encoding="utf-8")
    log("")
    log(f"  -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
