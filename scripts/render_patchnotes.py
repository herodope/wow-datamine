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

Usage:
    python scripts/render_patchnotes.py
    python scripts/render_patchnotes.py --build 1.60.1.69913
    python scripts/render_patchnotes.py --no-icons     # skip the fetch
"""

import argparse
import base64
import html
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import config
import contamination
import enrich
from diff_hotfixes import diff_table

ICON_TIMEOUT = 20

# How many rows per section get the full treatment (icon, resolved name).
# The rest go in the collapsed raw table. 4,218 hotfix-only ItemSparse rows
# would be a 20 MB page with an icon each, and unreadable besides.
SHOWCASE = 36

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
    ("strings", "Strings", ["GlobalStrings", "BroadcastText"]),
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


def build_model(build, e, icons, manifest):
    """Everything the page renders, computed once."""
    out_dir = config.build_out_dir(build)
    plain_dir, hot_dir = out_dir / "db2", out_dir / "db2_hotfixed"

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

    model["headline"] = headline(model, e)
    model["items_showcase"] = item_showcase(model, e, icons)
    model["contamination"] = run_contamination(build, e, results)
    return model


def headline(model, e):
    """The handful of things worth reading first."""
    out, r = [], model["results"]

    for t, res in sorted(r.items()):
        if model["manifest"]["tables"][t].get("resolution") == "hotfix_only":
            out.append({
                "kind": "hotfix_only", "table": t,
                "title": f"{t} exists only as live hotfix data",
                "body": (f"The table ships empty in the client and carries "
                         f"{res['rows_hotfixed']} row(s) once the hotfix "
                         f"overlay is applied. Nothing in the shipped build "
                         f"contains it."),
            })

    for t in ("ItemSparse", "ItemSearchName"):
        res = r.get(t)
        if res and res["added"]:
            out.append({
                "kind": "additions", "table": t,
                "title": f"{len(res['added']):,} rows added to {t} by hotfix",
                "body": (f"{res['rows_plain']:,} rows shipped, "
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
                "title": f"{len(res['removed'])} row(s) removed from {t} by hotfix",
                "body": "Removed live but still present in the shipped client: "
                        + ", ".join(names)
                        + ("…" if len(res["removed"]) > 3 else ""),
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


# --- Rendering --------------------------------------------------------------

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
    L = []
    if show:
        L.append(f"<h3>{len(show)} of {len(res['added']):,} added items</h3>")
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


def render(model, e, icons):
    m, mf = model, model["manifest"]
    totals = mf.get("totals") or {}
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    L = ["<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f"<title>{esc(m['build'])} patch notes</title>",
         # Inline favicon: a browser requests /favicon.ico unprompted, and a
         # 404 for it is the one external request an otherwise self-contained
         # page still makes.
         '<link rel="icon" href="data:image/svg+xml,'
         "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E"
         "%3Crect width='16' height='16' rx='3' fill='%2312141a'/%3E"
         "%3Cpath d='M3 11l2-6 2 4 2-4 2 6' stroke='%237aa2f7' stroke-width='1.6' "
         "fill='none' stroke-linejoin='round'/%3E%3C/svg%3E\">",
         f"<style>{CSS}</style></head><body><div class='wrap'>"]

    L.append(f"<h1>World of Warcraft: Forever — {esc(m['build'])}</h1>")
    L.append(f"<p class='sub'>Live hotfix changes against the shipped client. "
             f"Generated {esc(now)} from local extraction.</p>")

    L.append('<div class="stats">')
    for k, v in (("Tables changed", len(m["results"])),
                 ("Tables ok", totals.get("ok")),
                 ("Tables empty", totals.get("empty")),
                 ("Rows shipped", totals.get("rows_plain"))):
        if isinstance(v, int):
            L.append(f'<div class="stat"><div class="v">{v:,}</div>'
                     f'<div class="k">{esc(k)}</div></div>')
    L.append("</div>")

    if m["headline"]:
        L.append('<h2 id="headline">Headline</h2>')
        for h in m["headline"]:
            L.append(f'<div class="card"><h4><span class="tag">{esc(h["table"])}</span>'
                     f'{esc(h["title"])}</h4><p>{esc(h["body"])}</p></div>')

    L.append(render_contamination(m["contamination"]))

    for sec in m["sections"]:
        L.append(f'<h2 id="{esc(sec["key"])}">{esc(sec["title"])}</h2>')
        if sec["key"] == "items":
            L.append(render_items(m, e))
        for t in sec["tables"]:
            L.append(render_table_details(t, m["results"][t], e))

    L.append(render_inventory(mf))

    gt = mf.get("gametables")
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", help="version string; defaults to the newest extracted")
    ap.add_argument("--no-icons", action="store_true", help="skip icon fetching")
    ap.add_argument("--out", help="output path; defaults to reports/patchnotes_<build>.html")
    args = ap.parse_args(argv)

    started = time.monotonic()
    e = enrich.get(args.build)
    build = e.build
    log(f"build {build}")

    manifest_path = config.build_out_dir(build) / "manifest.json"
    if not manifest_path.exists():
        log(f"no {manifest_path} -- run extract_db2.py and inventory.py first")
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    icons = Icons(e.wtl.url, enabled=not args.no_icons)
    if icons.enabled and not e.wtl.probe():
        log(f"  WTL unreachable ({e.wtl.reason}) -- rendering without icons")
        icons.enabled = False

    model = build_model(build, e, icons, manifest)
    html_text = render(model, e, icons)

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = (config.REPORTS_DIR / f"patchnotes_{build}.html") if not args.out else \
        __import__("pathlib").Path(args.out)
    path.write_text(html_text, encoding="utf-8")
    e.flush()

    log("")
    log(f"  icons: {icons.hits} inlined, {icons.misses} skipped")
    log(f"  {len(html_text):,} bytes in {round(time.monotonic() - started, 1)}s")
    log(f"  -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
