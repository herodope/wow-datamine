#!/usr/bin/env python3
"""Check the "Findings to verify" predictions against a build's extracted data.

CLAUDE.md records dated predictions from 1.60.1.69913. This re-checks each one
against whatever build is on disk and reports:

    RESOLVED    the watched change happened; update CLAUDE.md and close it out
    UNRESOLVED  unchanged since the finding was recorded; still open
    FALSIFIED   the data contradicts what was recorded -- the finding is wrong,
                or the world moved in a way nobody predicted
    UNCHECKABLE the data needed is missing
    UNMEASURED  the check needs the live hotfix overlay and this build has none

UNMEASURED exists because a missing overlay does not look missing. When the
client has never run on a build, WTL has no DBCache.bin for it, and
db2_hotfixed/ comes out byte-identical to db2/. Every "live" value then
silently equals the client value. At 1.60.1.70009 that made #3 report RESOLVED
("the ladder now ships in the client") while the client held the same 5 items
as the build before -- "live-only: 0" only meant "no live data". Overlay
presence is read from manifest.json: no table with hotfix_delta and none
hotfix_only means no overlay.

The recorded values are parsed out of CLAUDE.md where they are machine-readable
(timestamps, record IDs, column names) so the doc stays authoritative and the
two cannot drift silently. Where parsing fails the script says so rather than
falling back to a stale constant.

A key distinction, and the reason several checks read `db2/` rather than a diff:
the contamination cases (now only the Item stubs) were pruned by **hotfix**. They vanish from a
hotfix diff while remaining in the shipped client. "Gone from the diff" is not
"gone from the build" -- only `db2/` answers that.

Usage:
    python scripts/check_findings.py
    python scripts/check_findings.py --build 1.60.1.69913
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone

import config
from diff_hotfixes import key_index, load_csv

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

RESOLVED, UNRESOLVED, FALSIFIED, UNCHECKABLE, UNMEASURED = (
    "RESOLVED", "UNRESOLVED", "FALSIFIED", "UNCHECKABLE", "UNMEASURED")

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

    # Finding 2 was closed 2026-10-08 (shipped at 70009); see "Closed findings".

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

    # Finding 5, narrowed 2026-10-08 to the one open case: the Item stubs.
    # Achievement 9275 and LightParams 453 were fixed in the 70009 client and
    # moved to "Closed findings", outside the section this parser reads.
    m = re.search(r"(\d+)\s*`Item`\s*stubs", section)
    rec["item_stub_count"] = int(m.group(1)) if m else None
    if rec["item_stub_count"] is None:
        rec["parse_errors"].append("finding 5: item_stub_count not parsed")

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
        self.has_overlay = self._detect_overlay()

    def _detect_overlay(self):
        """True / False from manifest.json totals; None if it cannot be read."""
        try:
            m = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
            t = m["totals"]
            return bool(t.get("hotfix_delta") or t.get("hotfix_only"))
        except (OSError, ValueError, KeyError):
            return None

    NO_OVERLAY = ("  no hotfix overlay for this build: db2_hotfixed/ equals db2/, "
                  "so the live side cannot be measured")

    def table(self, name, hotfixed=False):
        """(header, {id: row}) or (None, {}) when the table has no CSV."""
        path = (self.hotfixed if hotfixed else self.plain) / f"{name}.csv"
        header, rows = load_csv(path)
        if header is None:
            return None, {}
        idx, _ = key_index(header, name)
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
        # 204-empty tables write no CSV, so "not found" is the normal state of
        # a hotfix-only table when nothing is live -- not a missing extraction.
        if h_plain is not None and plain:
            return RESOLVED, [f"  now SHIPS IN THE CLIENT: db2/TimeEventData.csv has {len(plain)} row(s)"]
        if b.has_overlay is False:
            return UNMEASURED, [b.NO_OVERLAY,
                                "  client: TimeEventData still empty, so the schedule does not ship in the build"]
        if b.has_overlay:
            return FALSIFIED, [
                "  TimeEventData is empty in the client AND in the live overlay",
                "  the recorded rows are gone: withdrawn, or not (yet) pushed for this build",
                "  a DBCache.bin captured mid-session is a lower bound; re-check after a full logout",
            ]
        return UNCHECKABLE, ["db2_hotfixed/TimeEventData.csv not found and overlay state unknown"]

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
    if b.has_overlay is False:
        return UNMEASURED, [b.NO_OVERLAY,
                            "  client: TimeEventData still empty, so the schedule does not ship in the build"]

    notes.insert(0, f"  still hotfix-only; {len(ev_out)} row(s) in db2_hotfixed/")
    if actual_ts == recorded_ts:
        return UNRESOLVED, notes + ["  same rows, same timestamps as recorded"]
    if len(actual_ts) > len(recorded_ts):
        return RESOLVED, notes + [f"  row count grew {len(recorded_ts)} -> {len(actual_ts)}: cadence extended"]
    return FALSIFIED, notes + [
        f"  timestamps differ from the record ({recorded_ts} -> {actual_ts})",
        "  the schedule shifted; update CLAUDE.md",
    ]


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

    if b.has_overlay is False:
        # "live-only: 0" here means "no live data", not "nothing is live-only".
        if recorded is not None and len(in_client) >= recorded:
            return RESOLVED, notes + ["  the recorded population is in the CLIENT; staging is over"]
        return UNMEASURED, notes + [b.NO_OVERLAY,
                                    "  the client alone does not hold the ladder; whether it is still "
                                    "staged live is unknown"]
    if not only_live and in_client and (recorded is None or len(in_client) >= recorded):
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
    """The 75 retail Item stubs: present in the CLIENT's plain data, not merely
    absent from a hotfix diff.

    Until 2026-10-08 this checked three cases and returned RESOLVED when any
    one cleared, so it read "Resolved" for a month while every stub stayed in
    the client. The other two cases are closed; this is the open one.
    """
    notes = []
    stub_ids = rec.get("item_stub_ids")
    stub_count = rec.get("item_stub_count")
    h_item, item = b.table("Item")
    if h_item is None:
        return UNCHECKABLE, ["  Item stubs: Item not extracted"]
    if not stub_ids:
        return UNCHECKABLE, ["  Item stubs: the exact ID list could not be parsed from CLAUDE.md",
                             "    refusing to substitute a population count for the finding"]
    present = [i for i in stub_ids if i in item]
    gone = [i for i in stub_ids if i not in item]
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

    if not present:
        return RESOLVED, notes + ["  every recorded stub is gone from the shipped client"]
    return UNRESOLVED, notes + [
        "  still in the client; the hotfix prune was a stopgap",
        "  (absent from a hotfix diff is NOT the same as gone from the build)",
    ]


CHECKS = [
    ("1. Weekly event schedule", check_time_events),
    ("3. Vanilla PvP rank ladder", check_pvp),
    ("4. LightData column 055", check_light_column),
    ("5. Retail Item stubs in the client", check_contamination),
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
    if b.has_overlay is False:
        log("  NO HOTFIX OVERLAY: db2_hotfixed/ equals db2/ for every table. Checks that")
        log("  need live data report UNMEASURED. Log in on this build, re-extract, re-run.")
    elif b.has_overlay is None:
        log("  warning: manifest.json unreadable; cannot tell whether an overlay exists")

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
    if tally.get(UNMEASURED):
        log("  UNMEASURED is not UNRESOLVED: the data to decide was never loaded.")
    if tally.get(RESOLVED):
        log("  RESOLVED findings can be closed out and removed from 'Findings to verify'.")
    log("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
