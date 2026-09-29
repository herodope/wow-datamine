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

`files.csv` carries no content hash, so it cannot see a file whose bytes change
under an unchanged FDID. 1.60.1.70058 did exactly that: every DB2 identical, the
file set unchanged, and 79 files (FrameXML, shaders, DLLs) rewritten. The
content check asks WTL's `/casc/diff` to compare content keys, fetches both
sides of every changed text file (Lua, XML, TOC) for a unified diff, and caches
the result as out/<to>/content_diff_<from>.json so the report can be rebuilt
with WTL stopped. With neither WTL nor a cache it reports NOT MEASURED, never 0.

Output: reports/<from>_to_<to>.md, high-signal tables in full, everything else
collapsed.

Usage:
    python scripts/diff_builds.py 1.60.1.69913 1.60.2.70050
    python scripts/diff_builds.py <from> <to> --max-rows 40
    python scripts/diff_builds.py <from> <to> --allow-non-forever
    python scripts/diff_builds.py <from> <to> --refresh-contents   # ignore the cache
"""

import argparse
import csv
import difflib
import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
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

# Content types whose changes are worth reading line by line. Everything else
# modified is counted and listed, not diffed.
CONTENT_TEXT_TYPES = {"lua", "xml", "toc"}
CONTENT_MAX_TEXT_FILES = 60
CONTENT_MAX_DIFF_LINES = 400
# /casc/diff took about a minute on 70009 -> 70058, cold.
CONTENT_DIFF_TIMEOUT = 1800
CONTENT_FETCH_TIMEOUT = 120


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
    layout_changed = bool(lh_old and lh_new and lh_old != lh_new)

    # Byte-identical CSVs are skipped unparsed -- unless the layouthash moved.
    # Checking the hash first used to hide that: SpellDispelType changed layout
    # 47AA7AEB -> 3B574D4B at 1.60.1.70009 (retail 12.1.5's layout, Mask
    # u8 -> u16) with all 11 rows identical, and never reached the schema
    # section. Identical data means nothing is wrong, but the layout change is
    # still news and belongs in the report.
    data_identical = h_old is not None and h_old == h_new
    if data_identical and not layout_changed:
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

    # Key each side on its own header. Across a schema change the ID column
    # can be named differently on the two sides -- UiModelSceneActor is `ID`
    # at 69977 and `Field_1_60_1_70009_002` (`$id$` in the DBD) at 70009.
    idx_old, key_old = key_index(header_old, table) if header_old else (None, None)
    idx_new, key_new = key_index(header_new, table) if header_new else (None, None)
    key_col = key_new or key_old
    if key_old and key_new and key_old != key_new:
        key_col = f"{key_old} → {key_new}"
    old = key_rows(rows_old, idx_old, f"{table}.csv ({old_dir.parent.name})") if header_old else {}
    new = key_rows(rows_new, idx_new, f"{table}.csv ({new_dir.parent.name})") if header_new else {}

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
    if data_identical:
        reasons.append("data byte-identical (storage-only change)")

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
        "data_identical": data_identical,
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


def content_cache_path(from_build, to_build):
    return config.build_out_dir(to_build) / f"content_diff_{from_build}.json"


def _wtl_get(path, params, timeout):
    """(status, body). status None means WTL did not answer at all."""
    url = config.WTL_URL + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "wow-datamine/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError):
        return None, None


def _text_diff(fdid, name, from_build, to_build):
    """Unified diff of one text file across two builds, or an error string."""
    sides = []
    for b in (from_build, to_build):
        status, body = _wtl_get("/casc/fdid", {"fileDataID": fdid, "build": b}, CONTENT_FETCH_TIMEOUT)
        if status != 200 or body is None:
            return None, f"could not fetch from {b} (HTTP {status})"
        sides.append(body.decode("utf-8-sig", errors="replace").splitlines())
    lines = list(difflib.unified_diff(sides[0], sides[1], f"{from_build}/{name}", f"{to_build}/{name}", lineterm=""))
    if len(lines) > CONTENT_MAX_DIFF_LINES:
        lines = lines[:CONTENT_MAX_DIFF_LINES] + [f"... {len(lines) - CONTENT_MAX_DIFF_LINES:,} more line(s)"]
    return "\n".join(lines), None


def diff_contents(from_build, to_build, refresh=False):
    """Files whose bytes changed between two builds, by CASC content key.

    Returns {"status": "measured"|"cached"|"unavailable", ...}. "unavailable"
    carries a reason and no counts: an unmeasured zero is not a result.
    """
    cache = content_cache_path(from_build, to_build)
    if cache.exists() and not refresh:
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            data["status"] = "cached"
            return data
        except (OSError, json.JSONDecodeError) as exc:
            log(f"  content cache {cache.name} unreadable ({exc}); re-measuring")

    status, body = _wtl_get("/casc/diff", {"from": from_build, "to": to_build}, CONTENT_DIFF_TIMEOUT)
    if status is None:
        why = ("--refresh-contents skipped the cache" if cache.exists()
               else "no cached result exists")
        return {"status": "unavailable",
                "reason": f"WTL is not reachable at {config.WTL_URL} and {why}"}
    if status != 200:
        # A 500 here is usually a build WTL has no manifest for, not a dead server.
        return {"status": "unavailable",
                "reason": f"/casc/diff answered HTTP {status}: "
                          + body.decode("utf-8", errors="replace").strip()[:200]}
    raw = json.loads(body)

    entries = {"Added": [], "Removed": [], "Modified": []}
    for e in raw.get("data", []):
        entries.setdefault(e.get("action"), []).append({
            "fdid": e.get("id"), "type": e.get("type") or "unk",
            "filename": e.get("filename") or "", "encrypted": e.get("encryptedStatus"),
        })
    for v in entries.values():
        v.sort(key=lambda x: (x["type"], x["filename"], x["fdid"] or 0))

    text = [e for e in entries["Modified"] if e["type"] in CONTENT_TEXT_TYPES]
    text_diffs = {}
    for e in text[:CONTENT_MAX_TEXT_FILES]:
        diff, err = _text_diff(e["fdid"], e["filename"] or str(e["fdid"]), from_build, to_build)
        text_diffs[str(e["fdid"])] = {"diff": diff, "error": err}
    skipped = max(0, len(text) - CONTENT_MAX_TEXT_FILES)

    data = {
        "from_build": from_build, "to_build": to_build,
        "measured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "added": entries["Added"], "removed": entries["Removed"], "modified": entries["Modified"],
        "text_diffs": text_diffs, "text_diffs_skipped": skipped,
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, indent=1), encoding="utf-8")
    data["status"] = "measured"
    return data


def content_type_counts(entries):
    c = {}
    for e in entries:
        c[e["type"]] = c.get(e["type"], 0) + 1
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))


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

    identical = [r for r in hits if r.get("data_identical")]
    L.append(
        f"⚠️ **{len(hits)} table(s) changed schema between these builds, and "
        "field-level comparison is suppressed for them.** When columns move, a "
        "positional diff reports every row as changed and every field as different "
        "— noise, not signal. Row-level added/removed is still reported where it "
        "stays meaningful."
    )
    if identical:
        L.append("")
        L.append(
            f"{len(identical)} of them changed layout with **byte-identical exported "
            "data** — a storage change (e.g. a column widened) with no content "
            "change. Nothing is suppressed for those; they are listed because the "
            "layout move itself is news."
        )
    L.append("")
    L.append("| Table | Reason | Field diffs | Changed rows | Columns |")
    L.append("|---|---|---|---|---|")
    for r in hits:
        cols = " / ".join(
            x for x in (_col_summary(r.get("columns_added"), "+"),
                        _col_summary(r.get("columns_removed"), "−")) if x
        )
        if r.get("data_identical"):
            field_cell, changed_cell = "n/a — identical", "0"
        else:
            field_cell = "ok" if r["fields_comparable"] else "suppressed"
            changed_cell = "suppressed" if not r["rows_comparable"] else f"{len(r['changed']):,}"
        L.append(
            f"| `{r['table']}` | {'; '.join(r['schema_reasons']) or 'schema differs'} | "
            f"{field_cell} | {changed_cell} | "
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


def render_contents(cd):
    L = ["## File contents", ""]
    if cd is None:
        return L + ["Not requested (`--no-contents`). **Not measured** -- not a clean result.", "", "---", ""]
    if cd["status"] == "unavailable":
        L += [
            "**NOT MEASURED -- this is not a clean result.**",
            "",
            f"{cd['reason']}. `files.csv` has no content hash, so a file rewritten under an "
            "unchanged FDID is invisible everywhere else in this report. Re-run with WTL up.",
            "", "---", "",
        ]
        return L

    mod, add, rem = cd["modified"], cd["added"], cd["removed"]
    src = "read from cache" if cd["status"] == "cached" else "live"
    L.append(f"By CASC content key, via WTL `/casc/diff` ({src}; measured {cd['measured_at']}).")
    L.append("")
    L.append("| | |")
    L.append("|---|--:|")
    L.append(f"| Modified | {len(mod):,} |")
    L.append(f"| Added | {len(add):,} |")
    L.append(f"| Removed | {len(rem):,} |")
    L.append("")
    if not (mod or add or rem):
        L += ["No file's content changed between these builds.", "", "---", ""]
        return L

    if mod:
        L.append("Modified, by type: " + ", ".join(f"`{t}` {n:,}" for t, n in content_type_counts(mod)))
        L.append("")
        L.append("| FDID | Type | Path |")
        L.append("|---|---|---|")
        # Text files first: they are the readable part.
        shown = sorted(mod, key=lambda e: (e["type"] not in CONTENT_TEXT_TYPES, e["type"], e["filename"]))
        for e in shown[:80]:
            L.append(f"| `{e['fdid']}` | `{e['type']}` | {trunc(e['filename'] or '(unnamed)')} |")
        if len(shown) > 80:
            L.append(f"| … | | *{len(shown) - 80:,} more* |")
        L.append("")

    diffs = cd.get("text_diffs") or {}
    if diffs:
        L.append(f"### Text diffs ({len(diffs)})")
        L.append("")
        by_id = {str(e["fdid"]): e for e in mod}
        for fdid, d in diffs.items():
            name = by_id.get(fdid, {}).get("filename") or fdid
            L.append(f"<details><summary><code>{name}</code></summary>")
            L.append("")
            if d.get("error"):
                L.append(f"*{d['error']}*")
            else:
                L += ["```diff", d.get("diff") or "(no textual difference; likely line endings or encoding)", "```"]
            L += ["", "</details>", ""]
        if cd.get("text_diffs_skipped"):
            L.append(f"{cd['text_diffs_skipped']:,} further text file(s) changed and were not diffed "
                     f"(cap {CONTENT_MAX_TEXT_FILES}).")
            L.append("")
    L += ["---", ""]
    return L


def render(old_build, new_build, results, findings, fd, unchanged_count, max_rows, gt=None, coverage=None, cd=None):
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
    if cd is not None and cd["status"] != "unavailable":
        L.append(f"| Files with changed content | {len(cd['modified']):,} |")
    else:
        L.append("| Files with changed content | **not measured** |")
    L.append("")
    L.append("---")
    L.append("")

    L += render_encryption(fd)
    L += render_files(fd)
    L += render_contents(cd)
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
        f"⭐ = high-signal table, detailed below. ⚠️ = schema changed (field diffs "
        f"suppressed unless the data is byte-identical). {unchanged_count:,} table(s) "
        f"were byte-identical with an unchanged layout and are omitted."
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
    ap.add_argument("--refresh-contents", action="store_true",
                    help="re-query /casc/diff instead of using out/<to>/content_diff_<from>.json")
    ap.add_argument("--no-contents", action="store_true", help="skip the content-key check")
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
        flag = ""
        if r.get("data_identical"):
            flag = " [LAYOUT CHANGED - data byte-identical]"
        elif r["schema_changed"]:
            flag = " [SCHEMA CHANGED - field diffs suppressed]"
        log(f"  {t}: +{len(r['added'])} -{len(r['removed'])} ~{len(r['changed'])}{flag}")

    log(f"  {len(results)} changed, {unchanged} unchanged")

    findings, coverage = [], None
    if not args.no_contamination:
        def load_table(name):
            for d in (new_dir, old_dir):
                header, rows = load_csv(d / f"{name}.csv")
                if header is not None:
                    idx, _ = key_index(header, name)
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

    cd = None
    if not args.no_contents:
        log("  contents: comparing CASC content keys via /casc/diff ...")
        cd = diff_contents(args.from_build, args.to_build, refresh=args.refresh_contents)
        if cd["status"] == "unavailable":
            log(f"  contents: NOT MEASURED -- {cd['reason']}")
        else:
            kinds = ", ".join(f"{t} {n}" for t, n in content_type_counts(cd["modified"]))
            log(f"  contents ({cd['status']}): {len(cd['modified']):,} modified"
                + (f" [{kinds}]" if kinds else "")
                + f", {len(cd['text_diffs']):,} text diff(s)")

    gt = diff_gametables(old_dir, new_dir)
    if gt is None:
        log("  gametables: NOT EXTRACTED for one or both builds -- run extract_gametables.py")
    else:
        log(f"  gametables: {gt['old_count']} -> {gt['new_count']}, "
            f"+{len(gt['added'])} -{len(gt['removed'])} changed {len(gt['changed'])}")

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.report_path(args.from_build, args.to_build)
    path.write_text(render(args.from_build, args.to_build, results, findings, fd, unchanged, args.max_rows, gt, coverage, cd), encoding="utf-8")
    log("")
    log(f"  -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
