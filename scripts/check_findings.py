#!/usr/bin/env python3
"""Check the "Findings to verify" predictions against a build's extracted data.

CLAUDE.md records dated predictions from 1.60.1.69913. This re-checks each one
against whatever build is on disk and reports:

    RESOLVED    the watched change happened; update CLAUDE.md and close it out
    UNRESOLVED  unchanged since the finding was recorded; still open
    FALSIFIED   the data contradicts what was recorded -- the finding is wrong,
                or the world moved in a way nobody predicted
    UNCHECKABLE the data needed is missing

The recorded values are parsed out of CLAUDE.md where they are machine-readable
(timestamps, record IDs, column names) so the doc stays authoritative and the
two cannot drift silently. Where parsing fails the script says so rather than
falling back to a stale constant.

A key distinction, and the reason several checks read `db2/` rather than a diff:
the three contamination cases were pruned by **hotfix**. They vanish from a
hotfix diff while remaining in the shipped client. "Gone from the diff" is not
"gone from the build" -- only `db2/` answers that.

Usage:
    python scripts/check_findings.py
    python scripts/check_findings.py --build 1.60.1.69913
"""

import argparse
import csv
import re
import sys
from datetime import datetime, timezone

import config
from diff_hotfixes import key_index, load_csv

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

RESOLVED, UNRESOLVED, FALSIFIED, UNCHECKABLE = "RESOLVED", "UNRESOLVED", "FALSIFIED", "UNCHECKABLE"

# Vanilla honour-system rank titles, both factions.
RANKS = [
    "Grand Marshal", "Field Marshal", "Marshal", "Lieutenant Commander", "Knight-Lieutenant",
    "Knight-Captain", "Knight-Champion", "Commander", "Lieutenant General", "General",
    "High Warlord", "Warlord", "Blood Guard", "Legionnaire", "Centurion", "Champion",
    "Stone Guard", "First Sergeant", "Master Sergeant", "Sergeant Major", "Sergeant",
]


def log(msg=""):
    print(msg, file=sys.stderr, flush=True)


# --- CLAUDE.md parsing ------------------------------------------------------


def findings_section(path):
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^## Findings to verify$(.*?)^## ", text, re.M | re.S)
    if not m:
        return None
    return m.group(1)


def parse_recorded(section):
    """Pull the machine-readable values out of the prose."""
    rec = {"parse_errors": []}

    # Finding 1: | 25930 | 3162 | 1791824400 | 2026-10-12 17:00 |
    rows = re.findall(r"^\|\s*(\d{4,})\s*\|\s*(\d+)\s*\|\s*(\d{9,})\s*\|", section, re.M)
    rec["time_events"] = [(int(a), int(b), int(c)) for a, b, c in rows]
    if not rec["time_events"]:
        rec["parse_errors"].append("finding 1: no TimeEventData rows parsed")

    # Finding 2: GlobalStrings IDs in the code block
    rec["string_ids"] = [int(x) for x in re.findall(r"^\s*(600\d\d|601\d\d)\s+\"", section, re.M)]
    if not rec["string_ids"]:
        rec["string_ids"] = [int(x) for x in re.findall(r"\b(60077|60078|60175)\b", section)]
    rec["string_ids"] = sorted(set(rec["string_ids"]))
    if not rec["string_ids"]:
        rec["parse_errors"].append("finding 2: no GlobalStrings IDs parsed")

    # Finding 3: "481 carry vanilla PvP rank titles"
    # The doc wraps this across lines, so allow any whitespace run.
    m = re.search(r"\*\*(\d[\d,]*)\s+carry\s+vanilla\s+PvP\s+rank\s+titles\*\*", section, re.S)
    rec["pvp_count"] = int(m.group(1).replace(",", "")) if m else None
    if rec["pvp_count"] is None:
        rec["parse_errors"].append("finding 3: recorded rank-title count not parsed")

    # Finding 4: the column name and its value
    m = re.search(r"`(Field_[\w]+)`", section)
    rec["light_column"] = m.group(1) if m else None
    m = re.search(r"`(\d{6,})`\.?\s*\n", section)
    rec["light_value"] = m.group(1) if m else "13533183"
    if not rec["light_column"]:
        rec["parse_errors"].append("finding 4: LightData column name not parsed")

    # Finding 5: the three contamination records
    rec["achievement_id"] = None
    m = re.search(r"`Achievement`\s*(\d+)", section)
    if m:
        rec["achievement_id"] = m.group(1)
    m = re.search(r"`LightParams`\s*(\d+)", section)
    rec["lightparams_id"] = m.group(1) if m else None
    m = re.search(r"(\d+)\s*`Item`\s*stubs", section)
    rec["item_stub_count"] = int(m.group(1)) if m else None
    for k in ("achievement_id", "lightparams_id", "item_stub_count"):
        if rec[k] is None:
            rec["parse_errors"].append(f"finding 5: {k} not parsed")

    return rec


def parse_stub_ids(claude_md):
    """The exact 75 removed Item IDs, from the Retail contamination section.

    Checking these IDs is not the same as counting ClassID 4/SubclassID 0
    orphans: there are 1,128 such rows, and orphanhood is normal here. The
    population figure is context; these IDs are the finding. Same
    measurement-substitution trap as the 481-vs-927 rank-item split.
    """
    text = claude_md.read_text(encoding="utf-8")
    m = re.search(r"^## Retail contamination$(.*?)^## ", text, re.M | re.S)
    if not m:
        return None
    block = re.search(r"The 75 IDs.*?```\s*(.*?)```", m.group(1), re.S)
    if not block:
        return None
    ids = re.findall(r"\d+", block.group(1))
    return [i for i in ids] or None


# --- data access ------------------------------------------------------------


class Build:
    def __init__(self, version):
        self.version = version
        self.out = config.build_out_dir(version)
        self.plain = self.out / "db2"
        self.hotfixed = self.out / "db2_hotfixed"

    def table(self, name, hotfixed=False):
        """(header, {id: row}) or (None, {}) when the table has no CSV."""
        path = (self.hotfixed if hotfixed else self.plain) / f"{name}.csv"
        header, rows = load_csv(path)
        if header is None:
            return None, {}
        idx, _ = key_index(header)
        return header, {r[idx]: r for r in rows if idx < len(r)}

    def col(self, header, name):
        for i, n in enumerate(header or []):
            if n.strip().lower() == name.lower():
                return i
        return None


# --- checks -----------------------------------------------------------------


def check_time_events(b, rec):
    ev = rec.get("time_events") or []
    if not ev:
        return UNCHECKABLE, ["CLAUDE.md rows could not be parsed"]

    h_plain, plain = b.table("TimeEventData")
    h_hot, hot = b.table("TimeEventData", hotfixed=True)
    if h_hot is None:
        return UNCHECKABLE, ["db2_hotfixed/TimeEventData.csv not found"]

    ev_out, notes = [], []
    ts_i = b.col(h_hot, "Timestamp")
    id_i = b.col(h_hot, "TimeEventID")
    for key, row in hot.items():
        if ts_i is not None and ts_i < len(row):
            ev_out.append((int(key), int(row[id_i]) if id_i is not None else None, int(row[ts_i])))
    ev_out.sort()

    recorded_ts = sorted(t for _i, _e, t in ev)
    actual_ts = sorted(t for _i, _e, t in ev_out)

    for _i, _e, t in ev_out:
        notes.append(f"    {t}  {datetime.fromtimestamp(t, timezone.utc).isoformat()}")

    if h_plain is not None and plain:
        notes.insert(0, f"  now SHIPS IN THE CLIENT: db2/TimeEventData.csv has {len(plain)} row(s)")
        return RESOLVED, notes + ["  the schedule is no longer hotfix-only"]

    notes.insert(0, f"  still hotfix-only; {len(ev_out)} row(s) in db2_hotfixed/")
    if actual_ts == recorded_ts:
        return UNRESOLVED, notes + ["  same rows, same timestamps as recorded"]
    if len(actual_ts) > len(recorded_ts):
        return RESOLVED, notes + [f"  row count grew {len(recorded_ts)} -> {len(actual_ts)}: cadence extended"]
    return FALSIFIED, notes + [
        f"  timestamps differ from the record ({recorded_ts} -> {actual_ts})",
        "  the schedule shifted; update CLAUDE.md",
    ]


def check_rename(b, rec):
    ids = [str(i) for i in (rec.get("string_ids") or [])]
    if not ids:
        return UNCHECKABLE, ["CLAUDE.md string IDs could not be parsed"]

    h_plain, plain = b.table("GlobalStrings")
    h_hot, hot = b.table("GlobalStrings", hotfixed=True)
    if h_hot is None:
        return UNCHECKABLE, ["GlobalStrings not extracted"]

    t_i = b.col(h_hot, "TagText_lang")
    if t_i is None:
        return UNCHECKABLE, ["TagText_lang column not found"]

    notes, still_renamed = [], 0
    for sid in ids:
        before = plain.get(sid, [None] * (t_i + 1))[t_i] if plain else None
        after = hot.get(sid, [None] * (t_i + 1))[t_i] if hot else None
        notes.append(f"    {sid}: client={before!r}")
        notes.append(f"           live={after!r}")
        if after and "refresh" in after.lower():
            still_renamed += 1

    def count(rows, needle):
        return sum(1 for r in rows.values() if t_i < len(r) and needle in r[t_i].lower())

    shard_client, shard_live = count(plain, "shard"), count(hot, "shard")
    refresh_client, refresh_live = count(plain, "refresh the world"), count(hot, "refresh the world")

    notes.append(f"  'shard' strings: {shard_client} in client, {shard_live} live")
    notes.append(f"  'refresh the world' strings: {refresh_client} in client, {refresh_live} live")

    if refresh_client >= still_renamed and still_renamed:
        return RESOLVED, notes + ["  the rename now ships in the client, not just live"]
    if shard_live == 0 and shard_client > 0:
        return RESOLVED, notes + ["  'shard' wording is gone from live data entirely"]
    if still_renamed == 0:
        return FALSIFIED, notes + ["  none of the recorded strings carry 'refresh' any more"]
    return UNRESOLVED, notes + ["  rename still live-only; no supporting strings beyond the recorded three"]


def check_pvp(b, rec):
    h_plain, plain = b.table("ItemSearchName")
    h_hot, hot = b.table("ItemSearchName", hotfixed=True)
    if h_hot is None:
        return UNCHECKABLE, ["ItemSearchName not extracted"]

    n_i = b.col(h_hot, "Display_lang")
    if n_i is None:
        return UNCHECKABLE, ["Display_lang column not found"]

    # The recorded 481 counts MODERN-ID (>=100k) items that are live-only --
    # i.e. the bulk-injected additions. Counting the whole table instead gives
    # 927, which would read as "the ladder grew" on the next run and resolve
    # this finding for the wrong reason. Match the recorded population exactly.
    MODERN_ID_FLOOR = 100000

    def rank_ids(rows, modern_only=True):
        out = set()
        for key, r in rows.items():
            if n_i >= len(r):
                continue
            if modern_only and not (key.isdigit() and int(key) >= MODERN_ID_FLOOR):
                continue
            name = r[n_i]
            for rank in RANKS:
                if name.startswith(rank + "'s"):
                    out.add(key)
                    break
        return out

    in_client, in_live = rank_ids(plain), rank_ids(hot)
    only_live = in_live - in_client
    all_live = rank_ids(hot, modern_only=False)
    recorded = rec.get("pvp_count")

    notes = [
        f"  modern-ID (>={MODERN_ID_FLOOR:,}) rank-titled items, the recorded population:",
        f"    in client (db2/):                {len(in_client):,}",
        f"    in live (db2_hotfixed/):         {len(in_live):,}",
        f"    live-only, i.e. bulk-injected:   {len(only_live):,}",
        f"  (whole table, all IDs, for context: {len(all_live):,} live)",
    ]
    if recorded is not None:
        notes.append(f"  recorded in CLAUDE.md: {recorded:,} arrived by bulk injection")

    if not only_live and in_client:
        return RESOLVED, notes + ["  the ladder now ships in the client; staging is over"]
    if recorded is not None and len(only_live) > recorded:
        return RESOLVED, notes + [
            f"  the ladder grew ({recorded:,} -> {len(only_live):,} live-only); more ranks added"
        ]
    if recorded is not None and len(only_live) < recorded:
        return FALSIFIED, notes + [
            f"  fewer live-only rank items than recorded ({recorded:,} -> {len(only_live):,})",
            "  some shipped in the client, or the bulk injection was rolled back",
        ]
    return UNRESOLVED, notes + ["  still live-only, same count as recorded"]


def check_light_column(b, rec):
    name = rec.get("light_column")
    if not name:
        return UNCHECKABLE, ["CLAUDE.md column name could not be parsed"]

    header, _rows = b.table("LightData", hotfixed=True)
    if header is None:
        return UNCHECKABLE, ["LightData not extracted"]

    unnamed = [c for c in header if c.startswith("Field_")]
    if name in header:
        return UNRESOLVED, [
            f"  `{name}` is still unnamed in WoWDBDefs",
            f"  LightData has {len(unnamed)} unnamed column(s) of {len(header)}",
            "  do not assert the packed-RGB reading until this is named",
        ]
    return RESOLVED, [
        f"  `{name}` is GONE from the LightData header — WoWDBDefs named it",
        f"  LightData now has {len(unnamed)} unnamed column(s) of {len(header)}",
        "  inspect the header and, if it is a colour, the reading can be reported",
        f"  current columns: {', '.join(header[:12])}…",
    ]


def check_contamination(b, rec):
    """Present in the CLIENT's plain data, not merely absent from a hotfix diff."""
    notes, open_cases = [], 0

    ach_id = rec.get("achievement_id")
    if ach_id:
        _h, ach = b.table("Achievement")
        present = ach_id in ach
        open_cases += present
        notes.append(f"  Achievement {ach_id}: {'STILL IN CLIENT' if present else 'gone from client'}")

    lp_id = rec.get("lightparams_id")
    if lp_id:
        _h, lp = b.table("LightParams")
        present = lp_id in lp
        open_cases += present
        notes.append(f"  LightParams {lp_id}: {'STILL IN CLIENT' if present else 'gone from client'}")

    stub_ids = rec.get("item_stub_ids")
    stub_count = rec.get("item_stub_count")
    h_item, item = b.table("Item")
    if h_item is None:
        notes.append("  Item stubs: Item not extracted")
    elif not stub_ids:
        notes.append("  Item stubs: the exact ID list could not be parsed from CLAUDE.md")
        notes.append("    refusing to substitute a population count for the finding")
    else:
        present = [i for i in stub_ids if i in item]
        gone = [i for i in stub_ids if i not in item]
        open_cases += 1 if present else 0
        notes.append(
            f"  Item stubs: {len(present)} of {len(stub_ids)} recorded IDs STILL IN CLIENT"
            + (f", {len(gone)} gone" if gone else "")
        )
        if gone and present:
            notes.append(f"    partially pruned; gone: {', '.join(gone[:12])}"
                         + ("…" if len(gone) > 12 else ""))
        if stub_count is not None and len(stub_ids) != stub_count:
            notes.append(f"    WARNING: CLAUDE.md says {stub_count} stubs but lists "
                         f"{len(stub_ids)} IDs — the record is inconsistent")

        # Context only, never the check. Orphanhood is normal in this build.
        cls_i, sub_i = b.col(h_item, "ClassID"), b.col(h_item, "SubclassID")
        _hs, sparse = b.table("ItemSparse")
        _hn, search = b.table("ItemSearchName")
        have = set(sparse) | set(search)
        if cls_i is not None and sub_i is not None:
            pop = sum(
                1 for k, r in item.items()
                if k not in have and cls_i < len(r) and sub_i < len(r)
                and r[cls_i] == "4" and r[sub_i] == "0"
            )
            notes.append(f"    (context: {pop:,} ClassID 4/SubclassID 0 orphans exist in "
                         "total; that population is NOT the finding)")

    if open_cases == 0:
        return RESOLVED, notes + ["  all three cleared from the shipped client"]
    if open_cases == 3:
        return UNRESOLVED, notes + [
            "  all three still present in the client; the hotfix prunes were stopgaps",
            "  (they are absent from the hotfix diff, which is NOT the same thing)",
        ]
    return RESOLVED, notes + [f"  {3 - open_cases} of 3 cleared from the client; partial fix shipped"]


CHECKS = [
    ("1. Weekly event schedule", check_time_events),
    ("2. Transfer -> Refresh rename", check_rename),
    ("3. Vanilla PvP rank ladder", check_pvp),
    ("4. LightData column 055", check_light_column),
    ("5. Retail contamination cases", check_contamination),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", help="version string; defaults to the newest out/ dir")
    args = ap.parse_args(argv)

    version = args.build
    if not version:
        dirs = sorted(p.name for p in config.OUT_DIR.iterdir() if p.is_dir()) if config.OUT_DIR.exists() else []
        if not dirs:
            log(f"no extracted builds under {config.OUT_DIR}; run extract_db2.py first")
            return 2
        version = dirs[-1]

    b = Build(version)
    if not b.hotfixed.is_dir():
        log(f"{b.hotfixed} not found; run extract_db2.py for {version} first")
        return 2

    claude_md = config.REPO_ROOT / "CLAUDE.md"
    section = findings_section(claude_md)
    if section is None:
        log("could not find a '## Findings to verify' section in CLAUDE.md")
        return 2
    rec = parse_recorded(section)
    rec["item_stub_ids"] = parse_stub_ids(claude_md)
    if not rec["item_stub_ids"]:
        rec["parse_errors"].append("finding 5: exact Item stub IDs not parsed")

    log("=" * 72)
    log(f"FINDINGS CHECK — {version}")
    log(f"  against {claude_md}")
    log("=" * 72)
    for err in rec["parse_errors"]:
        log(f"  parse warning: {err}")

    tally = {}
    for title, fn in CHECKS:
        try:
            status, notes = fn(b, rec)
        except Exception as exc:  # a broken check must not hide the others
            status, notes = UNCHECKABLE, [f"  check raised {type(exc).__name__}: {exc}"]
        tally[status] = tally.get(status, 0) + 1
        log("")
        log(f"[{status}] {title}")
        for n in notes:
            log(n)

    log("")
    log("=" * 72)
    log("  " + "  ".join(f"{k}: {v}" for k, v in sorted(tally.items())))
    if tally.get(FALSIFIED):
        log("  FALSIFIED findings contradict CLAUDE.md — update the record.")
    if tally.get(RESOLVED):
        log("  RESOLVED findings can be closed out and removed from 'Findings to verify'.")
    log("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
