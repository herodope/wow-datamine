#!/usr/bin/env python3
"""Render a build as readable patch notes -> reports/patchnotes_<build>.html

A companion to the markdown diffs, not a replacement. `diff_builds.py` and
`diff_hotfixes.py` answer "what changed, exactly"; this answers "what happened
in this build" for someone reading rather than auditing. Raw tables are still
here, collapsed behind <details>, so nothing is hidden -- but the top of the
page is findings, not row counts.

Self-contained by construction. Icons are fetched from `/casc/blp2png` and
base64-inlined, there are no external stylesheets, fonts or scripts, and the
file opens offline with WTL stopped. It is one file you can send someone.

Three rules this renderer exists to enforce, all of them conventions that were
learned the hard way and are recorded in CLAUDE.md:

  * A `not_scanned` contamination verdict is rendered as prominently as a
    finding, and **never** as "0 findings". Convention: an unscanned zero is
    not a clean result.
  * A count that could not have moved is rendered with the note saying so.
    The encrypted-file count on a build whose file set did not change restates
    that the build did not change; it is not a measurement.
  * A column WoWDBDefs marks unverified is rendered with a visual marker and
    its raw value only. Never an interpretation, never a decoded name.

Three modes:

    hotfix      one build, shipped client vs live hotfix overlay
    build diff  two builds, shipped vs shipped
    hotfix wave one build, two overlay snapshots -- what a downtime with no
                new build actually changed. Both sides are live data, so
                `rows_plain` is the PREVIOUS live state, not the shipped
                build. Requires a snapshot preserved before the re-extract:

                    cp -rp out/<b>/db2_hotfixed                            out/<b>/db2_hotfixed.snapshot-<date>
                    python scripts/extract_db2.py --build <b> --restart
                    python scripts/render_patchnotes.py --since <date>

The build-diff page is usually sparse, and that is rendered as the result
rather than padded out. 69876 -> 69913 changes 2 of 610 tables and every
column that moves is unverified in WoWDBDefs, so the page says so plainly and
stops. A near-empty diff IS the finding: it says the build is a config
respin, which is worth more than three screens of unchanged row counts.

Usage:
    python scripts/render_patchnotes.py
    python scripts/render_patchnotes.py --build 1.60.1.69913
    python scripts/render_patchnotes.py --from 1.60.1.69876 --to 1.60.1.69913
    python scripts/render_patchnotes.py --since 20260919
    python scripts/render_patchnotes.py --no-icons     # skip the fetch
"""

import argparse
import base64
import html
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import config
import contamination
import enrich
import diff_builds
from diff_hotfixes import (SYNTHETIC_PUSH_BASE, diff_table, fetch_hotfixes,
                           key_index, key_rows, load_csv, status_label)

ICON_TIMEOUT = 20

# How many rows per section get the full treatment (icon, resolved name).
# The rest go in the collapsed raw table. 4,218 hotfix-only ItemSparse rows
# would be a 20 MB page with an icon each, and unreadable besides.
SHOWCASE = 36

# Overlay snapshots preserved before a re-extract, so a later run can diff one
# hotfix wave against the previous live state instead of against the client.
SNAPSHOT_PREFIX = "db2_hotfixed.snapshot-"

# WoW item quality colours. Canonical values -- recognisability beats theme
# purity here, a player knows these on sight.
QUALITY = {
    0: ("#9d9d9d", "Poor"), 1: ("#ffffff", "Common"), 2: ("#1eff00", "Uncommon"),
    3: ("#0070dd", "Rare"), 4: ("#a335ee", "Epic"), 5: ("#ff8000", "Legendary"),
    6: ("#e6cc80", "Artifact"), 7: ("#00ccff", "Heirloom"),
}

SECTIONS = [
    ("items", "Items", ["Item", "ItemSparse", "ItemSearchName"]),
    ("achievements", "Achievements", ["Achievement", "Achievement_Category"]),
    ("lighting", "Lighting", ["Light", "LightData", "LightParams",
                              "LightDataGlobalVolumeFog"]),
    ("strings", "Strings & dialogue", ["GlobalStrings", "BroadcastText",
                                       "ConversationLine", "Conversation"]),
    ("spells", "Spells", ["SpellMisc", "SpellTargetRestrictions", "SpellEffect",
                          "SpellName", "SpellCooldowns", "SpellPower"]),
    ("reputation", "Reputation", ["Faction", "FactionTemplate"]),
    ("creatures", "Creatures", ["Creature", "CreatureDifficulty"]),
    ("quests", "Quests", ["QuestV2", "QuestV2CliTask", "QuestObjective",
                          "QuestPOIBlob", "QuestPOIPoint", "PlayerCondition"]),
    ("events", "Events", ["TimeEventData"]),
]


def log(msg=""):
    print(msg, file=sys.stderr, flush=True)


def esc(v):
    return html.escape(str(v), quote=True)


# --- Icons ------------------------------------------------------------------


class Icons:
    """base64 data: URIs for BLP icons, fetched once each.

    A 404 from /casc/blp2png means the file is missing OR encrypted -- the
    route reads the first four bytes and 404s when they are all zero, which is
    what a missing-key file decodes to. Either way there is nothing to show and
    nothing to retry, so a miss is cached as a miss and never requested again.
    """

    def __init__(self, url, enabled=True):
        self.url, self.enabled = url, enabled
        self.cache = {}
        self.hits = self.misses = 0

    def get(self, fdid):
        if not self.enabled or not fdid:
            return None
        key = int(fdid)
        if key in self.cache:
            return self.cache[key]
        data = None
        try:
            req = urllib.request.Request(
                f"{self.url}/casc/blp2png?fileDataID={key}",
                headers={"User-Agent": "wow-datamine/1.0"})
            with urllib.request.urlopen(req, timeout=ICON_TIMEOUT) as resp:
                if resp.status == 200:
                    raw = resp.read()
                    if raw:
                        data = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        except urllib.error.HTTPError:
            data = None          # 404 missing/encrypted, 500 not a BLP -- skip
        except Exception:        # noqa: BLE001 - log, don't crash
            data = None
        self.cache[key] = data
        if data:
            self.hits += 1
        else:
            self.misses += 1
        return data


# --- Model ------------------------------------------------------------------


def snapshot_dir(build, since):
    """Resolve --since to a directory holding a previous hotfix overlay.

    Accepts a bare snapshot name (`db2_hotfixed.snapshot-20260919`), the date
    suffix alone (`20260919`), or any path. A missing directory is fatal: the
    alternative is silently diffing the overlay against itself and rendering
    "nothing changed", which is the one answer this mode must never invent.
    """
    out_dir = config.build_out_dir(build)
    for cand in (pathlib.Path(since),
                 out_dir / since,
                 out_dir / f"{SNAPSHOT_PREFIX}{since}"):
        if cand.is_dir():
            return cand
    log(f"no such snapshot: {since}")
    log(f"  looked in {out_dir} for {since!r} and "
        f"{SNAPSHOT_PREFIX + str(since)!r}")
    existing = sorted(d.name for d in out_dir.glob(f"{SNAPSHOT_PREFIX}*"))
    log(f"  available: {', '.join(existing) if existing else '(none)'}")
    raise SystemExit(2)


def scan_wave_tables(baseline_dir, hot_dir):
    """Tables whose overlay data differs between two snapshots.

    Deliberately scans every CSV rather than trusting the hotfix list to name
    the affected tables. Measured 2026-09-21: the list reported new records in
    19 tables while only 8 tables' exported rows actually moved, and it named
    5 BroadcastText records against 11 rows that appeared. Driving the diff
    off the list would have missed 6 real additions and invented 11 empty
    tables.
    """
    names = sorted({p.stem for p in baseline_dir.glob("*.csv")}
                   | {p.stem for p in hot_dir.glob("*.csv")})
    log(f"  scanning {len(names)} table(s) for overlay movement")
    results = {}
    for t in names:
        res = diff_table(t, baseline_dir, hot_dir)
        if res["magnitude"]:
            results[t] = res
    return results


def build_model(build, e, icons, manifest, baseline=None):
    """Everything the page renders, computed once.

    With `baseline`, this is a hotfix-WAVE page: one build, two overlay
    snapshots, answering "what did today's downtime change" rather than
    "what does the overlay change against the shipped client". The `plain`
    side of every diff is the older overlay, so `rows_plain` is the previous
    live state, not the shipped build.
    """
    out_dir = config.build_out_dir(build)
    hot_dir = out_dir / "db2_hotfixed"

    if baseline is not None:
        plain_dir = baseline
        results = scan_wave_tables(plain_dir, hot_dir)
        delta_tables = sorted(results)
        log(f"  {len(delta_tables)} table(s) moved since the snapshot")
    else:
        plain_dir = out_dir / "db2"
        delta_tables = sorted(
            t for t, v in manifest["tables"].items() if v.get("hotfix_delta"))
        log(f"  {len(delta_tables)} table(s) with a hotfix delta")
        results = {}
        for t in delta_tables:
            results[t] = diff_table(t, plain_dir, hot_dir)

    model = {
        "build": build,
        "manifest": manifest,
        "results": results,
        "sections": [],
        "headline": [],
        "mode": "wave" if baseline is not None else "hotfix",
        "baseline": baseline,
        "wave": None,
    }

    claimed = set()
    for key, title, tables in SECTIONS:
        present = [t for t in tables if t in results]
        claimed.update(present)
        if present:
            model["sections"].append({"key": key, "title": title,
                                      "tables": present})
    other = [t for t in delta_tables if t not in claimed]
    if other:
        model["sections"].append({"key": "other", "title": "Other systems",
                                  "tables": other})

    if baseline is not None:
        model["wave"] = wave_attribution(baseline, results,
                                         manifest.get("tables") or {})
    model["headline"] = headline(model, e)
    model["items_showcase"] = item_showcase(model, e, icons)
    model["items_provenance"] = items_provenance(results, out_dir / "db2")
    model["contamination"] = run_contamination(build, e, results)
    return model


def wave_attribution(baseline, results, manifest_tables=None):
    """Which hotfix pushes landed after the baseline snapshot was taken.

    The cutoff is the snapshot directory's mtime. `firstDetected` is when WTL
    first SAW a record, not when Blizzard pushed it, so this window is "new to
    us since the snapshot" -- an upper bound on push time and a lower bound on
    nothing. Stated on the page rather than papered over.

    Returns None when WTL is unreachable: an unattributed wave is still a
    readable wave, and a page that silently dropped the push IDs would look
    identical to one where Blizzard issued none.
    """
    cutoff = datetime.fromtimestamp(baseline.stat().st_mtime, timezone.utc)
    index, total = fetch_hotfixes()
    if not index:
        return None

    fresh = []          # (table, record_id, push, status, detected)
    for (table, record_id), entries in index.items():
        for push, status, detected in entries:
            try:
                seen = datetime.fromisoformat(detected).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if seen > cutoff:
                fresh.append((table, record_id, push, status, detected))

    pushes = {}
    synthetic = {}
    for table, record_id, push, status, detected in fresh:
        if push == SYNTHETIC_PUSH_BASE + record_id:
            synthetic[table] = synthetic.get(table, 0) + 1
            continue
        p = pushes.setdefault(push, {"push": push, "records": [],
                                     "detected": detected, "tables": set()})
        p["records"].append((table, record_id, status))
        p["tables"].add(table)
        p["detected"] = min(p["detected"], detected)

    for p in pushes.values():
        p["records"].sort()
        p["tables"] = sorted(p["tables"])

    # The hotfix list lowercases nothing itself, but fetch_hotfixes() keys on
    # tableName.lower(). Recover the real casing from the diff where possible
    # and from the manifest otherwise -- a table can appear in the hotfix list
    # without any of its rows moving, so `results` alone does not cover it.
    proper = {t.lower(): t for t in (manifest_tables or {})}
    proper.update({t.lower(): t for t in results})
    def name(t):
        return proper.get(t, t)

    # Tables the list names vs tables whose rows actually moved, and the same
    # question per record. A gap either way is a finding, not a rounding error
    # -- see scan_wave_tables(). The record-level check is the one that bites:
    # a table can be correctly listed and still have unlisted rows in it.
    listed = {t for t, _r, _p, _s, _d in fresh}
    moved = {t.lower() for t in results}
    listed_records = {(t, r) for t, r, _p, _s, _d in fresh}

    unexplained = []
    for table, res in sorted(results.items()):
        touched = [(k, "added") for k in res["added"]]
        touched += [(k, "removed") for k in res["removed"]]
        touched += [(k, "changed") for k, _d in res["changed"]]
        for key, how in touched:
            try:
                rid = int(key)
            except ValueError:
                continue
            if (table.lower(), rid) not in listed_records:
                unexplained.append((table, rid, how))
    unexplained.sort()

    # Status mix for tables that were listed but whose rows did not move. The
    # obvious reading is "all invalidated", and on 2026-09-21 that was true of
    # 8 of 11 such tables but NOT of the three carrying bulk item records.
    # Measure it; do not assert it.
    quiet = {}
    for table, record_id, push, status, _detected in fresh:
        if table in listed - moved:
            q = quiet.setdefault(table, {"valid": 0, "invalidated": 0})
            q["valid" if status == 1 else "invalidated"] += 1

    return {
        "proper": proper,
        "cutoff": cutoff,
        "records": len(fresh),
        "hotfix_total": total,
        "pushes": sorted(pushes.values(), key=lambda p: p["push"]),
        "synthetic": dict(sorted(synthetic.items())),
        "synthetic_total": sum(synthetic.values()),
        "listed_tables": sorted(listed),
        "listed_not_moved": sorted(listed - moved),
        "moved_not_listed": sorted(name(t) for t in moved - listed),
        "quiet": dict(sorted(quiet.items())),
        "unexplained": unexplained,
        "windows": sorted({d[:16] for _t, _r, _p, _s, d in fresh}),
    }


def headline(model, e):
    """The handful of things worth reading first."""
    out, r = [], model["results"]

    for t, res in sorted(r.items()):
        mf_t = model["manifest"]["tables"].get(t) or {}
        if mf_t.get("resolution") == "hotfix_only":
            out.append({
                "kind": "hotfix_only", "table": t,
                "title": f"{t} exists only as live hotfix data",
                "body": (f"The table ships empty in the client and carries "
                         f"{res['rows_hotfixed']} row(s) once the hotfix "
                         f"overlay is applied. Nothing in the shipped build "
                         f"contains it."),
            })

    wave = model.get("mode") == "wave"

    for t in ("ItemSparse", "ItemSearchName"):
        res = r.get(t)
        if res and res["added"]:
            out.append({
                "kind": "additions", "table": t,
                "title": (f"{len(res['added']):,} rows added to {t} in this wave"
                          if wave else
                          f"{len(res['added']):,} rows added to {t} by hotfix"),
                "body": (f"{res['rows_plain']:,} rows live before this wave, "
                         f"{res['rows_hotfixed']:,} after."
                         if wave else
                         f"{res['rows_plain']:,} rows shipped, "
                         f"{res['rows_hotfixed']:,} after the overlay."),
            })

    res = r.get("GlobalStrings")
    if res and res["changed"]:
        out.append({
            "kind": "strings", "table": "GlobalStrings",
            "title": f"{len(res['changed'])} user-facing string(s) reworded",
            "body": "Identical row count, different values — a count-based "
                    "check would report no change.",
        })

    for t, res in sorted(r.items()):
        if res["removed"]:
            names = []
            for k in res["removed"][:3]:
                lbl = e.label(t, k)
                names.append(f"{k} ({lbl})" if lbl else str(k))
            out.append({
                "kind": "removals", "table": t,
                "title": (f"{len(res['removed'])} row(s) withdrawn from {t} "
                          f"in this wave" if wave else
                          f"{len(res['removed'])} row(s) removed from {t} by hotfix"),
                "body": (("Live before this wave, gone from the overlay after it: "
                          if wave else
                          "Removed live but still present in the shipped client: ")
                         + ", ".join(names)
                         + ("…" if len(res["removed"]) > 3 else "")),
            })
    return out


def item_showcase(model, e, icons):
    """Resolved, icon-bearing rows for the Items section."""
    res = model["results"].get("ItemSparse")
    if not res or not res["added"]:
        return []
    log(f"  resolving {min(SHOWCASE, len(res['added']))} item(s) for the showcase")
    out = []
    for key in res["added"][:SHOWCASE]:
        info = e.item(key)
        out.append({
            "id": key,
            "name": info["name"],
            "unresolved": info["unresolved"],
            "quality": info["quality"],
            "item_level": info["item_level"],
            "required_level": info["required_level"],
            "source": info["source"],
            "hotfix_only": info["hotfix_only"],
            "icon": icons.get(info["icon_fdid"]),
        })
    return out


def items_provenance(results, client_db2):
    """Do the hotfix-added ItemSparse rows belong to items the client already has?

    An addition to ItemSparse is NOT a new item. `Item` is the base record and
    ships essentially complete; `ItemSparse` carries name, item level, quality
    and stats and ships deliberately incomplete, with the remainder supplied
    live. So "4,308 added" read as "4,308 new items" is wrong by the whole
    number -- measured 2026-09-21, all 4,308 already had a base Item row.

    Always measured against the CLIENT's db2/Item.csv, never against the diff's
    `plain` side: in wave mode that side is an earlier overlay, which would
    answer a different question.

    Returns None when there is nothing to say.
    """
    res = results.get("ItemSparse")
    if not res or not res["added"]:
        return None
    header, rows = load_csv(client_db2 / "Item.csv")
    if header is None:
        return None
    idx, _col = key_index(header)
    shipped = set(key_rows(rows, idx, "Item.csv (client)"))

    with_base = sum(1 for k in res["added"] if k in shipped)
    item_res = results.get("Item")
    removed = item_res["removed"] if item_res else []
    return {
        "added": len(res["added"]),
        "with_base": with_base,
        "without_base": len(res["added"]) - with_base,
        "item_removed": len(removed),
        "overlap": len(set(res["added"]) & set(removed)),
    }


def run_contamination(build, e, results):
    """scan() plus coverage. Coverage is the point -- see convention #15."""
    def load_table(name):
        h, rows = e.table(name, enrich.HOTFIXED)
        if not h:
            h, rows = e.table(name, enrich.PLAIN)
        return h, rows

    submitted = []
    for t, res in results.items():
        if res["magnitude"]:
            submitted.append({
                "table": t, "header": res["header"],
                "added": res["added"], "removed": res["removed"],
                "changed": res["changed"],
                "new_rows": res["hot_rows"], "old_rows": res["plain_rows"],
            })
    findings, _ref, coverage = contamination.scan(submitted, load_table)
    return {"findings": findings, "coverage": coverage}


def build_diff_model(from_build, to_build, e, manifest):
    """Two shipped builds compared. No hotfix overlay on either side."""
    old_dir = config.build_out_dir(from_build) / "db2"
    new_dir = config.build_out_dir(to_build) / "db2"
    if not old_dir.is_dir() or not new_dir.is_dir():
        raise SystemExit(f"need db2/ for both {from_build} and {to_build}")

    lh_old = diff_builds.load_layouthashes(from_build)
    lh_new = diff_builds.load_layouthashes(to_build)

    tables = sorted({f.stem for f in old_dir.glob("*.csv")}
                    | {f.stem for f in new_dir.glob("*.csv")})
    results, unchanged = {}, 0
    for t in tables:
        r = diff_builds.diff_table(t, old_dir, new_dir, 200,
                                   lh_old.get(t), lh_new.get(t))
        if r is None:
            unchanged += 1
        else:
            results[t] = r

    # Which columns actually moved, and are any of them interpretable? On a
    # sparse diff this is the whole story: if every changed column is marked
    # unverified in WoWDBDefs, the diff is real but unreadable, and saying so
    # is more useful than printing the numbers as though they meant something.
    changed_cols, unverified_cols = set(), set()
    for t, r in results.items():
        unv = e.unverified(t)
        for _key, deltas in r.get("changed", []):
            for name, _b, _a in deltas:
                changed_cols.add((t, name))
                if name.lower() in unv or name.split("[")[0].lower() in unv:
                    unverified_cols.add((t, name))
        for c in r.get("columns_added", []) + r.get("columns_removed", []):
            changed_cols.add((t, c))

    # Columns that moved vs unverified columns merely present. The two
    # numbers differ and both matter: "2 changed columns, both unverified" and
    # "these tables carry 4 unverified columns" are different statements, and
    # quoting only one of them invites the other to look like a contradiction.
    present_unverified = set()
    for t in results:
        hdr = results[t].get("header") or []
        unv = e.unverified(t)
        for c in hdr:
            if c.lower() in unv or c.split("[")[0].lower() in unv:
                present_unverified.add((t, c))

    return {
        "mode": "builddiff",
        "present_unverified": sorted(present_unverified),
        "from_build": from_build,
        "to_build": to_build,
        "build": to_build,
        "manifest": manifest,
        "results": results,
        "unchanged": unchanged,
        "table_count": len(tables),
        "changed_cols": sorted(changed_cols),
        "unverified_cols": sorted(unverified_cols),
        "all_changed_unverified": bool(changed_cols) and changed_cols == unverified_cols,
        "schema_changed": sorted(t for t, r in results.items() if r["schema_changed"]),
        "files": diff_builds.diff_files(old_dir, new_dir),
        "gametables": diff_builds.diff_gametables(old_dir, new_dir),
        "contamination": run_contamination_results(e, results),
        "sections": [],
        "headline": [],
        "items_showcase": [],
    }


def run_contamination_results(e, results):
    def load_table(name):
        h, rows = e.table(name, enrich.HOTFIXED)
        if not h:
            h, rows = e.table(name, enrich.PLAIN)
        return h, rows

    submitted = []
    for t, r in results.items():
        submitted.append({
            "table": t, "header": r["header"],
            "added": r.get("added", []), "removed": r.get("removed", []),
            "changed": r.get("changed", []),
            "new_rows": r.get("new_rows", {}), "old_rows": r.get("old_rows", {}),
        })
    findings, _ref, coverage = contamination.scan(submitted, load_table)
    return {"findings": findings, "coverage": coverage}


# --- Rendering --------------------------------------------------------------

# A browser requests /favicon.ico unprompted, and a 404 for it is the one
# external request an otherwise self-contained page still makes.
FAVICON = (
    '<link rel="icon" href="data:image/svg+xml,'
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E"
    "%3Crect width='16' height='16' rx='3' fill='%2312141a'/%3E"
    "%3Cpath d='M3 11l2-6 2 4 2-4 2 6' stroke='%237aa2f7' stroke-width='1.6' "
    "fill='none' stroke-linejoin='round'/%3E%3C/svg%3E\">"
)

CSS = """
:root{
  --bg:#12141a; --panel:#1a1d26; --panel-2:#20242f; --line:#2c3140;
  --ink:#dfe3ec; --ink-dim:#9aa2b5; --ink-faint:#6d7488;
  --accent:#7aa2f7; --warn:#e0af68; --bad:#f7768e; --good:#9ece6a;
  --unverified:#bb9af7;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:32px 16px 96px}
h1{font-size:26px;margin:0 0 4px}
h2{font-size:20px;margin:40px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{font-size:16px;margin:24px 0 8px;color:var(--ink-dim)}
a{color:var(--accent)}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px}
.sub{color:var(--ink-dim);margin:0 0 28px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:14px 16px;margin:10px 0}
.card h4{margin:0 0 6px;font-size:15px}
.card p{margin:0;color:var(--ink-dim);font-size:14px}
.tag{display:inline-block;font-size:11px;letter-spacing:.04em;text-transform:uppercase;
  padding:2px 7px;border-radius:99px;border:1px solid var(--line);
  color:var(--ink-faint);margin-right:8px;vertical-align:2px}
.banner{border-radius:8px;padding:16px 18px;margin:14px 0;border:1px solid}
.banner.bad{background:rgba(247,118,142,.09);border-color:var(--bad)}
.banner.warn{background:rgba(224,175,104,.09);border-color:var(--warn)}
.banner.good{background:rgba(158,206,106,.08);border-color:var(--good)}
.banner h4{margin:0 0 8px;font-size:16px}
.banner.bad h4{color:var(--bad)} .banner.warn h4{color:var(--warn)}
.banner.good h4{color:var(--good)}
.banner p{margin:6px 0;font-size:14px;color:var(--ink)}
.note{border-left:3px solid var(--warn);background:var(--panel-2);
  padding:10px 14px;margin:10px 0;font-size:13.5px;color:var(--ink-dim);border-radius:0 6px 6px 0}
.note strong{color:var(--warn)}
.warn-note{border-left:3px solid var(--bad);background:var(--panel-2);padding:10px 14px;margin:10px 0;font-size:13.5px;color:var(--ink);border-radius:0 6px 6px 0}
.warn-note strong{color:var(--bad)}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--ink-faint);font-weight:600;font-size:11px;letter-spacing:.05em;text-transform:uppercase}
tbody tr:hover{background:var(--panel-2)}
details{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  margin:12px 0;overflow:hidden}
summary{cursor:pointer;padding:11px 16px;font-size:14px;color:var(--ink-dim);user-select:none}
summary:hover{background:var(--panel-2);color:var(--ink)}
details[open] summary{border-bottom:1px solid var(--line)}
.details-body{padding:4px 16px 14px;overflow-x:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:8px;margin:12px 0}
.item{display:flex;gap:10px;align-items:center;background:var(--panel);
  border:1px solid var(--line);border-radius:6px;padding:8px 10px}
.item img{width:34px;height:34px;border-radius:4px;flex:none;image-rendering:auto}
.item .ph{width:34px;height:34px;border-radius:4px;flex:none;
  background:var(--panel-2);border:1px dashed var(--line)}
.item .n{font-size:13px;font-weight:600;line-height:1.3}
.item .m{font-size:11px;color:var(--ink-faint)}
.unv{color:var(--unverified);border-bottom:1px dotted var(--unverified);cursor:help}
.unv-key{font-size:12.5px;color:var(--ink-dim);margin:8px 0 0}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:16px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.stat .v{font-size:22px;font-weight:600}
.stat .k{font-size:11px;color:var(--ink-faint);text-transform:uppercase;letter-spacing:.05em}
.del{color:var(--bad)} .add{color:var(--good)} .dim{color:var(--ink-faint)}
footer{margin-top:56px;padding-top:16px;border-top:1px solid var(--line);
  font-size:12px;color:var(--ink-faint)}
@media(max-width:600px){.wrap{padding:20px 12px 64px}h1{font-size:21px}}
"""


def unverified_cell(name, value, unverified):
    """A cell. Unverified columns get a marker and their RAW value only."""
    if name.lower() in unverified or name.split("[")[0].lower() in unverified:
        return (f'<span class="unv" title="WoWDBDefs marks this column '
                f'unverified — raw value shown, no interpretation">'
                f'{esc(value)} ⚠</span>')
    return esc(value)


def render_contamination(c):
    """Coverage first. A not_scanned verdict is a banner, never '0 findings'."""
    cov, findings = c["coverage"], c["findings"]
    L = ['<h2 id="contamination">Retail contamination</h2>']

    if cov["verdict"] == "not_scanned":
        L.append('<div class="banner bad">')
        L.append("<h4>Not scanned — this is not a clean result</h4>")
        L.append(f"<p>{cov['rows_submitted']:,} row(s) across "
                 f"{cov['tables_submitted']} table(s) were submitted and "
                 f"<strong>no rule was able to read any of them</strong>. "
                 f"The rules are narrow and two of the three are pinned to a "
                 f"single table by name, so they returned nothing for lack of "
                 f"anything to read — not because the data looks clean.</p>")
        L.append("<p>Treat this as unmeasured.</p>")
        L.append("</div>")
        rows = {}
        for t in cov["per_table"]:
            for sk in t["skipped"]:
                rows.setdefault(sk["rule"], set()).add(
                    contamination._generic_reason(sk["rule"], sk["why"]))
        L.append("<table><thead><tr><th>Rule</th><th>Why it could not run</th>"
                 "</tr></thead><tbody>")
        for rule in sorted(rows):
            by_primary = {}
            for v in sorted(rows[rule], key=len):
                by_primary.setdefault(v.split("; ")[0], v)
            L.append(f"<tr><td class='mono'>{esc(rule)}</td><td>"
                     + "<br>".join(esc(x) for x in by_primary.values()) + "</td></tr>")
        L.append("</tbody></table>")
        return "\n".join(L)

    if cov["verdict"] == "no_data":
        L.append('<div class="banner warn"><h4>Nothing submitted</h4>'
                 "<p>No changed rows reached the contamination rules.</p></div>")
        return "\n".join(L)

    if not findings:
        L.append('<div class="banner good"><h4>No suspected contamination</h4>')
        L.append("<p>Rules that ran: "
                 + ", ".join(f"<code>{esc(r)}</code>" for r in cov["rules_applicable"])
                 + ".</p>")
        if cov["rules_never_applicable"]:
            L.append("<p>Never applicable here: "
                     + ", ".join(f"<code>{esc(r)}</code>"
                                 for r in cov["rules_never_applicable"])
                     + " — this result covers only the rules above.</p>")
        L.append("</div>")
        return "\n".join(L)

    L.append(f'<div class="banner warn"><h4>{len(findings)} finding(s)</h4>'
             f"<p>Rules that ran: "
             + ", ".join(f"<code>{esc(r)}</code>" for r in cov["rules_applicable"])
             + ".</p></div>")
    L.append("<table><thead><tr><th>Confidence</th><th>Rule</th><th>Table</th>"
             "<th>Record</th><th>Detail</th></tr></thead><tbody>")
    for f in findings:
        L.append(f"<tr><td>{esc(f['confidence'].upper())}</td>"
                 f"<td class='mono'>{esc(f['rule'])}</td>"
                 f"<td class='mono'>{esc(f['table'])}</td>"
                 f"<td class='mono'>{esc(f['record'])}</td>"
                 f"<td>{esc(f['detail'])}</td></tr>")
    L.append("</tbody></table>")
    return "\n".join(L)


def render_wave_pushes(w, e):
    """The hotfix pushes behind this wave, and what the list does not explain."""
    if w is None:
        return ('<h2 id="pushes">Hotfix pushes</h2><div class="note">'
                "<strong>No push attribution.</strong> WTL was unreachable, so "
                "the pushes behind these changes could not be read. The data "
                "diff above is unaffected — it comes from the CSVs on disk.</div>")

    L = ['<h2 id="pushes">Hotfix pushes</h2>']
    L.append(f"<p class='sub'>{w['records']:,} hotfix record(s) newly detected "
             f"since the baseline snapshot, out of {w['hotfix_total']:,} the "
             f"client knows for this build. "
             f"{len(w['pushes'])} real push ID(s); "
             f"{w['synthetic_total']:,} record(s) carry a synthetic push ID "
             f"derived from the record ID rather than issued by a push.</p>")

    if w["windows"]:
        L.append(f"<p class='sub'>Detection window(s): "
                 f"{esc(', '.join(w['windows']))} UTC. "
                 f"<strong>firstDetected is when WTL first saw a record, not "
                 f"when Blizzard pushed it</strong> — treat these as "
                 f"“new to us since "
                 f"{esc(w['cutoff'].isoformat())}”, not as push times.</p>")

    if w["pushes"]:
        L.append("<table><thead><tr><th>Push</th><th>Records</th>"
                 "<th>Tables</th><th>What it touched</th></tr></thead><tbody>")
        for p in w["pushes"]:
            cells = []
            for table, record_id, status in p["records"][:14]:
                lbl = e.label(table, str(record_id))
                shown = f"{w['proper'].get(table, table)}:{record_id}"
                if lbl:
                    shown += f" ({lbl})"
                cells.append(f"{shown} [{status_label(status)}]")
            more = len(p["records"]) - 14
            if more > 0:
                cells.append(f"… +{more} more")
            L.append(f"<tr><td class='mono'>{p['push']}</td>"
                     f"<td>{len(p['records'])}</td>"
                     f"<td class='mono'>"
                     f"{esc(', '.join(w['proper'].get(t, t) for t in p['tables']))}"
                     f"</td>"
                     f"<td class='mono dim'>{esc('; '.join(cells))}</td></tr>")
        L.append("</tbody></table>")

    if w["synthetic"]:
        L.append("<h3>Bulk-injected records (synthetic push IDs)</h3>")
        L.append("<p class='sub'>A synthetic ID is <code>2^24 + recordID</code>. "
                 "Counting these as pushes invents authoring events that never "
                 "happened, so they are reported as one bulk load per table.</p>")
        L.append("<p class='mono dim'>"
                 + esc(", ".join(f"{t} ×{n:,}"
                                 for t, n in w["synthetic"].items()))
                 + "</p>")

    # Convention: a gap between what the list names and what the data does is
    # rendered as a finding. Silence here would read as agreement.
    if w["listed_not_moved"]:
        rows = []
        for t in w["listed_not_moved"]:
            q = w["quiet"].get(t) or {}
            rows.append(f"{w['proper'].get(t, t)} "
                        f"({q.get('valid', 0)} valid, "
                        f"{q.get('invalidated', 0)} invalidated)")
        any_valid = any((w["quiet"].get(t) or {}).get("valid")
                        for t in w["listed_not_moved"])
        L.append('<div class="note"><strong>'
                 f"{len(w['listed_not_moved'])} table(s) have new hotfix "
                 "records but unchanged exported rows.</strong> "
                 + esc(", ".join(rows)) + ". "
                 + ("An invalidated record leaving the data untouched is "
                    "unsurprising. Records marked <em>valid</em> that change "
                    "nothing are not, and some here are valid — so "
                    "“invalidated, therefore inert” does not explain "
                    "this set. Mechanism unverified; the row counts are the "
                    "measurement."
                    if any_valid else
                    "Every record here is invalidated rather than valid, which "
                    "is consistent with a withdrawn or no-op override — "
                    "but the mechanism is not verified and the row counts are "
                    "the measurement.")
                 + " Blizzard touched these tables; the client-visible data "
                   "did not move.</div>")

    if w["moved_not_listed"]:
        L.append('<div class="warn-note"><strong>'
                 f"{len(w['moved_not_listed'])} table(s) moved with no hotfix "
                 "record naming them.</strong> "
                 + esc(", ".join(w["moved_not_listed"])) + ".</div>")


    if w["unexplained"]:
        by_table = {}
        for table, record_id, how in w["unexplained"]:
            by_table.setdefault(table, []).append((record_id, how))
        L.append('<div class="warn-note"><strong>'
                 f"{len(w['unexplained'])} changed row(s) have no hotfix record "
                 "at all.</strong> These rows moved between the two overlay "
                 "snapshots, but <code>/dbc/hotfixes/list</code> holds no entry "
                 "for them — not under this table, not at any push ID. "
                 "They are also absent from the plain export, so they are not "
                 "shipped client data surfacing late. "
                 "<strong>Unexplained.</strong> Recorded rather than reconciled "
                 "away: a wave driven off the hotfix list alone would have "
                 "missed them entirely.</div>")
        L.append("<table><thead><tr><th>Table</th><th>Rows</th>"
                 "</tr></thead><tbody>")
        for table, entries in sorted(by_table.items()):
            shown = []
            for record_id, how in entries[:24]:
                lbl = e.label(table, str(record_id))
                shown.append(f"{record_id} ({how}"
                             + (f", {lbl}" if lbl else "") + ")")
            if len(entries) > 24:
                shown.append(f"… +{len(entries) - 24} more")
            L.append(f"<tr><td class='mono'>{esc(table)}</td>"
                     f"<td class='mono dim'>{esc(', '.join(shown))}</td></tr>")
        L.append("</tbody></table>")

    return "\n".join(L)


def render_inventory(manifest):
    """Counts, with the non-measurement note where one applies."""
    inv = manifest.get("inventory") or {}
    if not inv:
        return ""
    L = ['<h2 id="files">Files</h2>', '<div class="stats">']
    for k, v in (("Files in build", inv.get("rows_written")),
                 ("Without a name", inv.get("files_without_name")),
                 ("Encrypted", inv.get("encrypted"))):
        L.append(f'<div class="stat"><div class="v">{v:,}</div>'
                 f'<div class="k">{esc(k)}</div></div>' if isinstance(v, int) else "")
    L.append("</div>")

    by = inv.get("encrypted_by_status") or {}
    if by:
        L.append("<table><thead><tr><th>Encryption status</th><th>Files</th>"
                 "</tr></thead><tbody>")
        for k in sorted(by):
            L.append(f"<tr><td class='mono'>{esc(k)}</td><td>{by[k]:,}</td></tr>")
        L.append("</tbody></table>")

    # Convention: a count that could not have moved is not a measurement.
    if inv.get("fdid_set_changed") is False:
        prev = inv.get("encrypted_baseline_from") or "the previous build"
        L.append('<div class="note"><strong>Not a measurement this build.</strong> '
                 f"The file set is unchanged from {esc(prev)}, so the encrypted "
                 "count could not have moved either. A match here restates that "
                 "the build did not change; it says nothing about encryption.</div>")
    elif inv.get("fdid_set_changed") is None:
        L.append('<div class="note"><strong>No baseline.</strong> '
                 "There is no earlier extracted build to compare against, so "
                 "these counts are a starting point rather than a result.</div>")
    return "\n".join(L)


def render_items(model, e):
    show = model["items_showcase"]
    res = model["results"].get("ItemSparse")
    if not show and not res:
        return ""
    prov = model.get("items_provenance")
    wave = model.get("mode") == "wave"
    L = []
    if show:
        # "added items" was the old heading, and it read as "new items shipped".
        # It is an ItemSparse row count, and the table right below it is Item,
        # which moves independently -- name the table and say what added means.
        L.append(f"<h3>{len(show)} of {len(res['added']):,} items given display "
                 f"data by hotfix "
                 f"<span class='tag'>ItemSparse</span></h3>")
        if prov:
            if prov["without_base"] == 0:
                L.append(f'<p class="sub">Not new items. All '
                         f'{prov["added"]:,} already have a base '
                         f'<code>Item</code> row in the shipped client; what '
                         f'arrived {"in this wave" if wave else "by hotfix"} is '
                         f'their name, item level, quality and stats. '
                         f'<code>ItemSparse</code> ships deliberately '
                         f'incomplete and is filled in live, so an addition '
                         f'here means <em>display data supplied</em>, not '
                         f'<em>item created</em>.</p>')
            else:
                L.append(f'<p class="sub">{prov["with_base"]:,} of '
                         f'{prov["added"]:,} already have a base '
                         f'<code>Item</code> row in the shipped client — for '
                         f'those, what arrived is display data, not a new '
                         f'item. The other {prov["without_base"]:,} have no '
                         f'base row and may be genuinely new.</p>')
        L.append('<div class="grid">')
        for it in show:
            colour, qname = QUALITY.get(it["quality"], ("#dfe3ec", "?"))
            icon = (f'<img src="{it["icon"]}" alt="">' if it["icon"]
                    else '<div class="ph"></div>')
            name = esc(it["name"]) if it["name"] else f'<span class="dim">#{esc(it["id"])} unresolved</span>'
            meta = []
            if it["item_level"]:
                meta.append(f"ilvl {it['item_level']}")
            if it["required_level"]:
                meta.append(f"req {it['required_level']}")
            meta.append(qname)
            L.append(
                f'<div class="item">{icon}<div>'
                f'<div class="n" style="color:{colour}">{name}</div>'
                f'<div class="m">{esc(" · ".join(meta))}</div></div></div>')
        L.append("</div>")
        if len(res["added"]) > len(show):
            L.append(f'<p class="sub">Showing {len(show)} of '
                     f'{len(res["added"]):,}; the rest are in the raw table below.</p>')

    # The Item table sits directly below this section and moves independently.
    # Left unexplained, its row reads as a contradiction of the count above.
    if prov and prov["item_removed"]:
        L.append(f'<div class="note"><strong>The <code>Item</code> table '
                 f'moved the other way: {prov["item_removed"]} row(s) '
                 f'removed.</strong> That is a different table from '
                 f'<code>ItemSparse</code> above, and a different set of rows '
                 f'— {"no IDs appear" if not prov["overlap"] else str(prov["overlap"]) + " ID(s) appear"} '
                 f'in both. <code>Item</code> losing rows and '
                 f'<code>ItemSparse</code> gaining them is not a '
                 f'contradiction: one is the base record being withdrawn '
                 f'live, the other is display data being filled in.</div>')

    return "\n".join(L)


def render_table_details(t, res, e):
    """The raw diff for one table, collapsed."""
    unv = e.unverified(t)
    hdr = res["header"] or []
    L = [f"<details><summary>{esc(t)} — "
         f"<span class='add'>+{len(res['added'])}</span> "
         f"<span class='del'>−{len(res['removed'])}</span> "
         f"~{len(res['changed'])} "
         f"<span class='dim'>({res['rows_plain']:,} → {res['rows_hotfixed']:,} rows)</span>"
         f"</summary><div class='details-body'>"]

    if not res["fields_comparable"]:
        L.append('<div class="note"><strong>Field comparison suppressed.</strong> '
                 "The two variants have different headers, so a positional "
                 "diff would report every field as different.</div>")

    if res["changed"]:
        L.append("<table><thead><tr><th>Row</th><th>Column</th><th>Before</th>"
                 "<th>After</th></tr></thead><tbody>")
        for key, deltas in res["changed"][:60]:
            lbl = e.label(t, key)
            shown = f"{esc(key)}" + (f" <span class='dim'>{esc(lbl)}</span>" if lbl else "")
            for name, before, after in deltas[:12]:
                L.append(f"<tr><td class='mono'>{shown}</td>"
                         f"<td class='mono'>{unverified_cell(name, name, unv)}</td>"
                         f"<td class='mono del'>{esc(before)[:90]}</td>"
                         f"<td class='mono add'>{esc(after)[:90]}</td></tr>")
                shown = ""
        L.append("</tbody></table>")
        if len(res["changed"]) > 60:
            L.append(f"<p class='dim'>… {len(res['changed']) - 60:,} more changed row(s).</p>")

    for label, keys in (("Added", res["added"]), ("Removed", res["removed"])):
        if not keys:
            continue
        L.append(f"<h3>{label} ({len(keys):,})</h3><p class='mono dim'>")
        parts = []
        for k in keys[:200]:
            lbl = e.label(t, k)
            parts.append(f"{esc(k)}" + (f" ({esc(lbl)})" if lbl else ""))
        L.append(", ".join(parts))
        if len(keys) > 200:
            L.append(f" … {len(keys) - 200:,} more")
        L.append("</p>")

    if unv:
        L.append('<p class="unv-key">Columns marked '
                 '<span class="unv">like this ⚠</span> are unverified in '
                 'WoWDBDefs for this build: raw values only, no interpretation.</p>')
    L.append("</div></details>")
    return "\n".join(L)


def render_build_diff(m, e):
    """The two-build page. Sparseness is rendered, not padded."""
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    changed, unchanged = len(m["results"]), m["unchanged"]

    L = ["<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f"<title>{esc(m['from_build'])} to {esc(m['to_build'])}</title>",
         FAVICON, f"<style>{CSS}</style></head><body><div class='wrap'>"]

    L.append(f"<h1>{esc(m['from_build'])} &rarr; {esc(m['to_build'])}</h1>")
    L.append("<p class='sub'>Shipped client against shipped client. No hotfix "
             f"overlay on either side. Generated {esc(now)}.</p>")

    L.append('<div class="stats">')
    for k, v in (("Tables changed", changed), ("Byte-identical", unchanged),
                 ("Tables compared", m["table_count"])):
        L.append(f'<div class="stat"><div class="v">{v:,}</div>'
                 f'<div class="k">{esc(k)}</div></div>')
    L.append("</div>")

    # --- the sparseness, stated -------------------------------------------
    pct = (100.0 * unchanged / m["table_count"]) if m["table_count"] else 0
    L.append('<h2 id="headline">Headline</h2>')

    if changed == 0:
        L.append('<div class="banner good"><h4>Nothing changed</h4>'
                 f"<p>All {m['table_count']:,} shipped tables are byte-identical "
                 "between these two builds.</p></div>")
    else:
        L.append('<div class="banner good">')
        L.append(f"<h4>{changed} of {m['table_count']:,} tables changed "
                 f"({pct:.1f}% byte-identical)</h4>")
        L.append("<p>This is a small diff, and that is the result rather than a "
                 "gap in the extraction — every other shipped table is identical "
                 "byte for byte. A build that moves this little is a config "
                 "respin, not a content patch.</p>")
        L.append("<p class='mono'>"
                 + ", ".join(esc(t) for t in sorted(m["results"])) + "</p>")
        L.append("</div>")

    if m["all_changed_unverified"]:
        L.append('<div class="banner warn">')
        L.append(f"<h4>All {len(m['changed_cols'])} changed column(s) are "
                 "unverified — nothing here can be interpreted</h4>")
        L.append("<p>Every column that moved is marked unverified in WoWDBDefs "
                 "for this build. The values below are real and are reported "
                 "exactly as they appear, but their <em>meaning</em> is unknown, "
                 "and this page will not guess at one. Until a definition sync "
                 "names them, the only honest reading is that something changed "
                 "in these positions.</p>")
        L.append("<p class='mono'>"
                 + ", ".join(f"{esc(t)}.{esc(c)}" for t, c in m["changed_cols"])
                 + "</p>")
        pres = m.get("present_unverified") or []
        if len(pres) > len(m["changed_cols"]):
            L.append(f"<p class='dim'>These {len(m['results'])} table(s) carry "
                     f"{len(pres)} unverified column(s) in total; the "
                     f"{len(m['changed_cols'])} above are the ones whose values "
                     f"actually moved.</p>")
        L.append("</div>")
    elif m["changed_cols"]:
        known = [f"{t}.{c}" for t, c in m["changed_cols"]
                 if (t, c) not in set(m["unverified_cols"])]
        L.append('<div class="card"><h4>Changed columns</h4>')
        L.append(f"<p>{len(known)} of {len(m['changed_cols'])} carry a verified "
                 "definition: <span class='mono'>"
                 + ", ".join(esc(k) for k in known) + "</span></p></div>")

    if m["schema_changed"]:
        L.append('<div class="banner warn"><h4>Schema changed in '
                 f"{len(m['schema_changed'])} table(s)</h4>")
        L.append("<p>A layout or definition change invalidates positional "
                 "comparison. These tables are reported even where their "
                 "magnitude is zero — a suppressed diff is not an unchanged "
                 "table.</p></div>")
        for t in m["schema_changed"]:
            r = m["results"][t]
            L.append(f"<div class='card'><h4>{esc(t)}</h4><p>"
                     + esc("; ".join(r["schema_reasons"]))
                     + f" — fields comparable: {r['fields_comparable']}, "
                       f"rows comparable: {r['rows_comparable']}</p></div>")

    L.append(render_contamination(m["contamination"]))

    # --- per-table detail --------------------------------------------------
    if m["results"]:
        L.append('<h2 id="tables">Changed tables</h2>')
        for t in sorted(m["results"]):
            L.append(render_build_table(t, m["results"][t], e))

    # --- files, with the non-measurement note ------------------------------
    L.append('<h2 id="files">Files</h2>')
    fd = m["files"]
    if "unavailable" in fd:
        L.append(f'<div class="note">{esc(fd["unavailable"])}</div>')
    else:
        moved = bool(fd.get("added") or fd.get("removed") or fd.get("retyped"))
        L.append('<div class="stats">')
        for k, v in (("Added", len(fd.get("added", []))),
                     ("Removed", len(fd.get("removed", []))),
                     ("Retyped", len(fd.get("retyped", [])))):
            L.append(f'<div class="stat"><div class="v">{v:,}</div>'
                     f'<div class="k">{esc(k)}</div></div>')
        L.append("</div>")
        if not moved:
            L.append('<div class="note"><strong>The file set did not move.</strong> '
                     "Nothing was added, removed or retyped between these builds, "
                     "so any per-file count compared across them — the encrypted "
                     "total included — could not have changed either. A match is "
                     "not a measurement here.</div>")

    # --- gametables --------------------------------------------------------
    L.append('<h2 id="gametables">GameTables</h2>')
    gt = m["gametables"]
    if gt is None:
        L.append('<div class="banner warn"><h4>Not extracted</h4>'
                 "<p>GameTables are tab-separated text under <code>GameTables/</code> "
                 "in CASC, not DB2s — <code>/listfile/db2s</code> cannot list them "
                 "and <code>extract_db2.py</code> never sees them. Run "
                 "<code>scripts/extract_gametables.py</code>. This is a gap, not a "
                 "clean result: <code>SpellScaling</code> ships as a 204-empty DB2 "
                 "and its curves live in <code>SpellScaling.txt</code> here.</p></div>")
    elif not (gt["added"] or gt["removed"] or gt["changed"]):
        L.append(f"<p class='sub'>{gt['old_count']} &rarr; {gt['new_count']} tables, "
                 "none changed.</p>")
    else:
        L.append(f"<p class='sub'>{gt['old_count']} &rarr; {gt['new_count']} tables.</p>")
        if gt["added"]:
            L.append("<p><strong>Added:</strong> <span class='mono'>"
                     + ", ".join(esc(n) for n in gt["added"]) + "</span></p>")
        if gt["removed"]:
            L.append("<p><strong>Removed:</strong> <span class='mono'>"
                     + ", ".join(esc(n) for n in gt["removed"]) + "</span></p>")
        if gt["changed"]:
            L.append("<table><thead><tr><th>Table</th><th>Rows</th>"
                     "<th>Columns added</th><th>Columns removed</th></tr></thead><tbody>")
            for c in gt["changed"]:
                rows = (str(c["rows_old"]) if c["rows_old"] == c["rows_new"]
                        else f"{c['rows_old']} &rarr; {c['rows_new']}")
                L.append(f"<tr><td class='mono'>{esc(c['name'])}</td><td>{rows}</td>"
                         f"<td class='mono'>{esc(', '.join(c['cols_added'])) or '—'}</td>"
                         f"<td class='mono'>{esc(', '.join(c['cols_removed'])) or '—'}</td></tr>")
            L.append("</tbody></table>")

    L.append("<footer>Shipped-vs-shipped. Unverified columns are marked and show "
             "raw values only. Self-contained — no external requests. Generated by "
             "<code>scripts/render_patchnotes.py</code>.</footer>")
    L.append("</div></body></html>")
    return "\n".join(x for x in L if x)


def render_build_table(t, r, e):
    unv = e.unverified(t)
    L = [f"<details open><summary>{esc(t)} — "
         f"<span class='add'>+{len(r.get('added', []))}</span> "
         f"<span class='del'>−{len(r.get('removed', []))}</span> "
         f"~{len(r.get('changed', []))} "
         f"<span class='dim'>({r['rows_old']:,} → {r['rows_new']:,} rows)</span>"
         f"</summary><div class='details-body'>"]

    if r["schema_changed"]:
        L.append('<div class="note"><strong>Schema changed:</strong> '
                 + esc("; ".join(r["schema_reasons"]))
                 + ". Comparison suppressed where it would be meaningless.</div>")

    if r.get("changed"):
        L.append("<table><thead><tr><th>Row</th><th>Column</th><th>Before</th>"
                 "<th>After</th></tr></thead><tbody>")
        for key, deltas in r["changed"][:80]:
            lbl = e.label(t, key)
            shown = esc(key) + (f" <span class='dim'>{esc(lbl)}</span>" if lbl else "")
            if not deltas:
                L.append(f"<tr><td class='mono'>{shown}</td><td colspan='3' "
                         "class='dim'>row differs; field comparison suppressed</td></tr>")
                continue
            for name, before, after in deltas:
                L.append(f"<tr><td class='mono'>{shown}</td>"
                         f"<td class='mono'>{unverified_cell(name, name, unv)}</td>"
                         f"<td class='mono del'>{esc(before)[:90]}</td>"
                         f"<td class='mono add'>{esc(after)[:90]}</td></tr>")
                shown = ""
        L.append("</tbody></table>")

    for label, keys in (("Added", r.get("added", [])), ("Removed", r.get("removed", []))):
        if not keys:
            continue
        L.append(f"<h3>{label} ({len(keys):,})</h3><p class='mono dim'>")
        parts = []
        for k in keys[:200]:
            lbl = e.label(t, k)
            parts.append(esc(k) + (f" ({esc(lbl)})" if lbl else ""))
        L.append(", ".join(parts))
        if len(keys) > 200:
            L.append(f" … {len(keys) - 200:,} more")
        L.append("</p>")

    if unv:
        L.append('<p class="unv-key">Columns marked '
                 '<span class="unv">like this \u26a0</span> are unverified in '
                 'WoWDBDefs for this build: raw values only, no interpretation.</p>')
    L.append("</div></details>")
    return "\n".join(L)


def render(model, e, icons):
    m, mf = model, model["manifest"]
    totals = mf.get("totals") or {}
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    L = ["<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f"<title>{esc(m['build'])} "
         f"{'hotfix wave' if m.get('mode') == 'wave' else 'patch notes'}</title>",
         FAVICON,
         f"<style>{CSS}</style></head><body><div class='wrap'>"]

    wave = m.get("mode") == "wave"
    w = m.get("wave")

    L.append(f"<h1>World of Warcraft: Forever — {esc(m['build'])}</h1>")
    if wave:
        # The point of this page is that the build did NOT change. Say it in
        # the subtitle, not three screens down.
        L.append(f"<p class='sub'><strong>Hotfix wave on an unchanged "
                 f"client.</strong> The build is identical either side of this "
                 f"diff — same buildConfig, same client bytes. Both sides are "
                 f"live overlay snapshots: before is "
                 f"<code>{esc(m['baseline'].name)}</code>, after is the current "
                 f"overlay. Generated {esc(now)} from local extraction.</p>")
    else:
        L.append(f"<p class='sub'>Live hotfix changes against the shipped client. "
                 f"Generated {esc(now)} from local extraction.</p>")

    L.append('<div class="stats">')
    if wave:
        stats = (("Tables moved", len(m["results"])),
                 ("Hotfix records", (w or {}).get("records")),
                 ("Real pushes", len((w or {}).get("pushes") or [])
                  if w else None),
                 ("Bulk records", (w or {}).get("synthetic_total")))
    else:
        stats = (("Tables changed", len(m["results"])),
                 ("Tables ok", totals.get("ok")),
                 ("Tables empty", totals.get("empty")),
                 ("Rows shipped", totals.get("rows_plain")))
    for k, v in stats:
        if isinstance(v, int):
            L.append(f'<div class="stat"><div class="v">{v:,}</div>'
                     f'<div class="k">{esc(k)}</div></div>')
    L.append("</div>")

    if wave and not m["results"]:
        # An empty wave is a result. Render it as one rather than an empty page.
        L.append('<div class="note"><strong>No exported row changed.</strong> '
                 "Every table is byte-identical between the two overlay "
                 "snapshots. If the hotfix list below shows records, they were "
                 "withdrawals or no-ops; if it is also empty, nothing reached "
                 "this client.</div>")

    if m["headline"]:
        L.append('<h2 id="headline">Headline</h2>')
        for h in m["headline"]:
            L.append(f'<div class="card"><h4><span class="tag">{esc(h["table"])}</span>'
                     f'{esc(h["title"])}</h4><p>{esc(h["body"])}</p></div>')

    if wave:
        L.append(render_wave_pushes(w, e))

    L.append(render_contamination(m["contamination"]))

    for sec in m["sections"]:
        L.append(f'<h2 id="{esc(sec["key"])}">{esc(sec["title"])}</h2>')
        if sec["key"] == "items":
            L.append(render_items(m, e))
        for t in sec["tables"]:
            L.append(render_table_details(t, m["results"][t], e))

    if not wave:
        L.append(render_inventory(mf))

    gt = mf.get("gametables") if not wave else None
    if gt:
        L.append('<h2 id="gametables">GameTables</h2>')
        L.append(f"<p class='sub'>{gt.get('count', 0)} tab-separated table(s), "
                 f"{gt.get('bytes', 0):,} bytes. These are not DB2s and carry "
                 f"no DBD definition.</p>")

    L.append("<footer>")
    L.append(f"Icons: {icons.hits} inlined, {icons.misses} skipped "
             f"(missing or encrypted). " if icons.enabled else "Icons disabled. ")
    L.append("Self-contained — no external requests. Unverified columns are "
             "marked and show raw values only. Generated by "
             "<code>scripts/render_patchnotes.py</code>.")
    L.append("</footer></div></body></html>")
    return "\n".join(x for x in L if x)


# --- Main -------------------------------------------------------------------


def load_manifest(build):
    path = config.build_out_dir(build) / "manifest.json"
    if not path.exists():
        log(f"no {path} -- run extract_db2.py and inventory.py first")
        raise SystemExit(2)
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", help="hotfix mode: version string; "
                                    "defaults to the newest extracted")
    ap.add_argument("--from", dest="from_build", help="build-diff mode: older build")
    ap.add_argument("--to", dest="to_build", help="build-diff mode: newer build")
    ap.add_argument("--since", metavar="SNAPSHOT",
                    help="hotfix-wave mode: diff the current overlay against a "
                         "preserved overlay snapshot under out/<build>/ "
                         "(name, date suffix, or path) instead of against the "
                         "shipped client")
    ap.add_argument("--no-icons", action="store_true", help="skip icon fetching")
    ap.add_argument("--out", help="output path; defaults under reports/")
    args = ap.parse_args(argv)

    if bool(args.from_build) != bool(args.to_build):
        ap.error("--from and --to must be given together")
    if args.since and args.from_build:
        ap.error("--since is a one-build mode; it cannot be combined with "
                 "--from/--to")

    started = time.monotonic()

    if args.from_build:
        # Build diff. No icons: nothing here is an item card, and a page that
        # is meant to say "almost nothing changed" should not spend 200 KB
        # saying it.
        e = enrich.get(args.to_build)
        log(f"build diff {args.from_build} -> {args.to_build}")
        model = build_diff_model(args.from_build, args.to_build, e,
                                 load_manifest(args.to_build))
        html_text = render_build_diff(model, e)
        default = config.REPORTS_DIR / f"patchnotes_{args.from_build}_to_{args.to_build}.html"
        log(f"  {len(model['results'])} changed, {model['unchanged']} byte-identical")
        if model["all_changed_unverified"]:
            log(f"  all {len(model['changed_cols'])} changed column(s) are unverified")
        icons = None
    else:
        e = enrich.get(args.build)
        log(f"build {e.build}")
        icons = Icons(e.wtl.url, enabled=not args.no_icons)
        if icons.enabled and not e.wtl.probe():
            log(f"  WTL unreachable ({e.wtl.reason}) -- rendering without icons")
            icons.enabled = False
        baseline = snapshot_dir(e.build, args.since) if args.since else None
        if baseline is not None:
            log(f"hotfix wave: {baseline.name} -> current overlay")
        model = build_model(e.build, e, icons, load_manifest(e.build),
                            baseline=baseline)
        html_text = render(model, e, icons)
        if baseline is not None:
            stamp = baseline.name.replace(SNAPSHOT_PREFIX, "")
            default = (config.REPORTS_DIR
                       / f"hotfixwave_{e.build}_since_{stamp}.html")
        else:
            default = config.REPORTS_DIR / f"patchnotes_{e.build}.html"

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = pathlib.Path(args.out) if args.out else default
    path.write_text(html_text, encoding="utf-8")
    e.flush()

    log("")
    if icons is not None:
        log(f"  icons: {icons.hits} inlined, {icons.misses} skipped")
    log(f"  {len(html_text):,} bytes in {round(time.monotonic() - started, 1)}s")
    log(f"  -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
