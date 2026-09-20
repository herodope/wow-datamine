"""Detect retail data that leaked into a Classic+ build.

`wow_classic_beta` is a recycled product code and Forever is built on shared
tooling, so retail-era rows turn up in tables that should only contain Classic
content. They are worth tracking per build: they get pruned over time, and a
new one appearing is a signal in itself.

Rules are ranked by how much they actually discriminate, measured against
1.60.1.69913:

  dangling_map_ref   HIGH   -- a row references a Map ID absent from this
                              build. Exactly 1 of 233 Achievement rows hits
                              this, so it is near zero false positives.
  light_absent_map   HIGH   -- a LightParams ID whose only referencing Light
                              rows sit on maps absent from this build.
  orphan_removal     MEDIUM -- rows removed together that carry no supporting
                              display data. Orphanhood ALONE is not a signal:
                              12,504 of 31,675 Item rows (39.5%) lack an
                              ItemSparse/ItemSearchName row in this build,
                              because ItemSparse ships incomplete and arrives
                              by hotfix. What is suspicious is a coordinated
                              removal of such rows in a single push.

Deliberately NOT a rule: "ID falls in a modern retail range". Achievement IDs
here run 627-64159 with 160 of 233 rows above 61000, so an ID-range test would
flag most of the table. ID ranges are reported as supporting evidence only.
"""

import csv
import sys

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# Column names that reference Map.ID. UiMapID is a different namespace and is
# deliberately excluded.
MAP_REF_COLUMNS = {"instance_id", "instanceid", "continentid", "mapid", "map_id"}

HIGH, MEDIUM = "high", "medium"


def _rows(result, side):
    """Rows from one side of a diff.

    Hotfix diffs name the sides plain/hotfixed; build diffs name them old/new.
    Both mean "before" and "after", so the rules work off either naming.
    """
    if side == "old":
        return result.get("old_rows", result.get("plain_rows", {}))
    return result.get("new_rows", result.get("hot_rows", {}))


def _col(header, name):
    for i, n in enumerate(header or []):
        if n.strip().lower() == name.lower():
            return i
    return None


def build_reference(load_table):
    """Collect what the rules need. `load_table(name)` -> (header, {key: row})."""
    ref = {"map_ids": set(), "items_with_data": set(), "light_by_param": {}, "map_names": {}}

    mh, maps = load_table("Map")
    if mh:
        ref["map_ids"] = set(maps)
        ni = _col(mh, "MapName_lang")
        if ni is not None:
            ref["map_names"] = {k: v[ni] for k, v in maps.items() if ni < len(v)}

    for t in ("ItemSparse", "ItemSearchName"):
        _h, rows = load_table(t)
        ref["items_with_data"].update(rows)

    lh, lights = load_table("Light")
    if lh:
        param_cols = [i for i, n in enumerate(lh) if n.lower().startswith("lightparamsid")]
        cont_i = _col(lh, "ContinentID")
        by_param = {}
        for k, row in lights.items():
            cont = row[cont_i] if cont_i is not None and cont_i < len(row) else None
            for i in param_cols:
                if i < len(row) and row[i] not in ("0", ""):
                    by_param.setdefault(row[i], []).append((k, cont))
        ref["light_by_param"] = by_param

    return ref


def _dangling_map_refs(result, ref, rows_by_key, which):
    """Rows referencing a Map ID this build does not contain."""
    findings = []
    header = result["header"]
    if not header or not ref["map_ids"]:
        return findings

    targets = [(i, n) for i, n in enumerate(header) if n.strip().lower() in MAP_REF_COLUMNS]
    if not targets:
        return findings

    for key in rows_by_key:
        row = _rows(result, "old" if which == "removed" else "new").get(key)
        if not row:
            continue
        for i, name in targets:
            if i >= len(row):
                continue
            val = row[i]
            if val in ("", "0", "-1") or val in ref["map_ids"]:
                continue
            findings.append({
                "rule": "dangling_map_ref",
                "confidence": HIGH,
                "table": result["table"],
                "record": key,
                "detail": f"{name} = {val}, a map not present in this build",
                "side": which,
            })
    return findings


def _light_absent_map(result, ref):
    """LightParams values whose referencing Light rows are all on absent maps."""
    findings = []
    if result["table"] != "Light" or not ref["light_by_param"]:
        return findings

    header = result["header"] or []
    param_cols = {i for i, n in enumerate(header) if n.lower().startswith("lightparamsid")}

    for key, deltas in result["changed"]:
        for name, before, after in deltas:
            i = _col(header, name)
            if i not in param_cols:
                continue
            for value, role in ((before, "replaced"), (after, "new")):
                if value in ("", "0"):
                    continue
                refs = ref["light_by_param"].get(value, [])
                absent = [(lid, c) for lid, c in refs if c not in ref["map_ids"]]
                if refs and len(absent) == len(refs):
                    findings.append({
                        "rule": "light_absent_map",
                        "confidence": HIGH,
                        "table": "LightParams",
                        "record": value,
                        "detail": (
                            f"{role} in Light {key}.{name}; referenced only by Light "
                            f"{', '.join(lid for lid, _ in absent)} on map(s) "
                            f"{', '.join(sorted({c for _, c in absent}))} absent from this build"
                        ),
                        "side": "changed",
                    })
    return findings


def _orphan_removal(result, ref, load_table):
    """Coordinated removal of rows with no supporting display data."""
    if result["table"] != "Item" or not result["removed"]:
        return []

    orphans = [k for k in result["removed"] if k not in ref["items_with_data"]]
    if not orphans:
        return []

    # Removed rows exist only on the "before" side -- the after-side Item table
    # no longer contains them, so load_table() cannot describe them.
    ih, items = result["header"], _rows(result, "old")
    cls_i, sub_i = _col(ih, "ClassID"), _col(ih, "SubclassID")
    inv_i = _col(ih, "InventoryType")
    combos, invs = {}, {}
    for k in orphans:
        row = items.get(k)
        if not row:
            continue
        if cls_i is not None and sub_i is not None:
            combos[(row[cls_i], row[sub_i])] = combos.get((row[cls_i], row[sub_i]), 0) + 1
        if inv_i is not None:
            invs[row[inv_i]] = invs.get(row[inv_i], 0) + 1

    combo_str = ", ".join(f"ClassID {c}/SubclassID {s} × {n}" for (c, s), n in sorted(combos.items(), key=lambda kv: -kv[1]))
    inv_str = ", ".join(f"InventoryType {k} × {v}" for k, v in sorted(invs.items(), key=lambda kv: -kv[1]))

    return [{
        "rule": "orphan_removal",
        "confidence": MEDIUM,
        "table": "Item",
        "record": f"{len(orphans)} rows",
        "detail": (
            f"{len(orphans)} removed rows have no ItemSparse/ItemSearchName data "
            f"({combo_str}; {inv_str}). Orphanhood alone is NOT a signal — "
            f"{sum(1 for k in items if k not in ref['items_with_data']):,} of "
            f"{len(items):,} Item rows lack display data in this build, because "
            f"ItemSparse ships incomplete and arrives by hotfix. The signal is that "
            f"these were removed together."
        ),
        "side": "removed",
    }]


# Every rule here is narrow, and two of the three are pinned to a single table
# by name. applicability() states that up front so a zero can be told apart
# from data no rule could read. See "an unscanned zero is not a clean result"
# in CLAUDE.md.
def applicability(result):
    """[(rule, applicable, why)] for one diff result."""
    table = result.get("table", "")
    header = result.get("header") or []
    has_add_rm = bool(result.get("added") or result.get("removed"))

    map_cols = [n for n in header if n.strip().lower() in MAP_REF_COLUMNS]
    why = "reads " + ", ".join(sorted(MAP_REF_COLUMNS)) + "; "
    why += ("found " + ", ".join(map_cols)) if map_cols else "table carries none of them"
    if not has_add_rm:
        why += "; no added/removed rows"
    yield ("dangling_map_ref", bool(map_cols) and has_add_rm, why)

    why = "scoped to table Light"
    if table != "Light":
        why += "; this is " + str(table)
    if not result.get("changed"):
        why += "; no changed rows"
    yield ("light_absent_map", table == "Light" and bool(result.get("changed")), why)

    why = "scoped to table Item and to removed rows"
    if table != "Item":
        why += "; this is " + str(table)
    if not result.get("removed"):
        why += "; no removed rows"
    yield ("orphan_removal", table == "Item" and bool(result.get("removed")), why)


ALL_RULES = {"dangling_map_ref", "light_absent_map", "orphan_removal"}


def scan(results, load_table):
    """Run every rule over the diff results.

    Returns (findings, ref, coverage). `coverage` exists because a rule that
    could not read the data returns [] exactly like a rule that read it and
    found nothing -- and callers were rendering both as "0 findings".

    coverage["verdict"] is one of:

        "scanned"      at least one rule was applicable somewhere
        "not_scanned"  NO rule could read ANY submitted table. A zero here
                       says nothing whatever about the data.
        "no_data"      nothing was submitted

    Measured example: the six 461xxx Thunder Clap rows span 11 spell tables
    and 72 rows, and every rule is inapplicable to all of them -- no spell
    table carries a map-reference column, and the other two rules are pinned
    to Light and Item. The honest answer there is "not scanned", not "clean".
    """
    ref = build_reference(load_table)
    findings = []
    per_table, applicable_rules = [], set()

    for r in results:
        rules = list(applicability(r))
        for name, ok, _why in rules:
            if ok:
                applicable_rules.add(name)
        per_table.append({
            "table": r.get("table"),
            "rows": len(r.get("added", [])) + len(r.get("removed", [])) + len(r.get("changed", [])),
            "applicable": [n for n, ok, _ in rules if ok],
            "skipped": [{"rule": n, "why": w} for n, ok, w in rules if not ok],
        })

        findings += _dangling_map_refs(r, ref, r["removed"], "removed")
        findings += _dangling_map_refs(r, ref, r["added"], "added")
        findings += _light_absent_map(r, ref)
        findings += _orphan_removal(r, ref, load_table)

    if not results:
        verdict = "no_data"
    elif applicable_rules:
        verdict = "scanned"
    else:
        verdict = "not_scanned"

    coverage = {
        "verdict": verdict,
        "tables_submitted": len(results),
        "rows_submitted": sum(t["rows"] for t in per_table),
        "rules_applicable": sorted(applicable_rules),
        "rules_never_applicable": sorted(ALL_RULES - applicable_rules),
        "tables_with_no_applicable_rule": [t["table"] for t in per_table if not t["applicable"]],
        "per_table": per_table,
    }

    order = {HIGH: 0, MEDIUM: 1}
    findings.sort(key=lambda f: (order.get(f["confidence"], 9), f["table"], str(f["record"])))
    return findings, ref, coverage


def _generic_reason(rule, why):
    """Strip the per-table clause so reasons collapse to one row per rule.

    Keeps the structural cause (what the rule reads, what table it is pinned
    to) and drops "this is <Table>", which is the only part that varies purely
    by which table was submitted.
    """
    return "; ".join(
        part for part in why.split("; ")
        if not part.startswith("this is ")
    )


def render_markdown(findings, coverage=None):
    """The "Retail contamination" report section. Shared by both diff scripts.

    `coverage` comes from scan(). Without it a zero renders as a clean result,
    which is wrong whenever no rule could read the submitted tables.
    """
    L = ["## Retail contamination", ""]
    verdict = (coverage or {}).get("verdict")

    if verdict == "no_data":
        L += ["Nothing was submitted to the contamination rules.", "", "---", ""]
        return L

    if verdict == "not_scanned":
        L += [
            "**NOT SCANNED -- this is not a clean result.**",
            "",
            "{:,} row(s) across {} table(s) were submitted and **no contamination "
            "rule was able to read any of them**. The rules are narrow, and two of "
            "the three are pinned to a single table by name, so they returned "
            "nothing for lack of anything to read -- not because the data looks "
            "clean. Treat this as unmeasured.".format(
                coverage["rows_submitted"], coverage["tables_submitted"]),
            "",
            "| Rule | Why it could not run |",
            "|---|---|",
        ]
        # One row per RULE, not per rule-and-table: the per-table reasons
        # differ only by the table name, and 3 rules x N tables of near
        # identical text buries the point.
        reasons = {}
        for t in coverage["per_table"]:
            for sk in t["skipped"]:
                reasons.setdefault(sk["rule"], set()).add(_generic_reason(sk["rule"], sk["why"]))
        for rule in sorted(reasons):
            # Distinct reasons across tables go on their own lines rather than
            # being run together with semicolons, which reads as one garbled
            # sentence when two tables are skipped for different reasons.
            # Collapse variants that share a primary cause. Two tables can be
            # skipped for the same structural reason with a different trailing
            # clause ("...; no added/removed rows"); that is one reason, not
            # two, and the shortest phrasing is the clearest.
            by_primary = {}
            for v in sorted(reasons[rule], key=len):
                by_primary.setdefault(v.split("; ")[0], v)
            cell = "<br>".join(by_primary[k] for k in sorted(by_primary))
            L.append("| `" + rule + "` | " + cell + " |")
        L += [
            "",
            "Submitted tables: " + ", ".join(
                "`" + str(t) + "`" for t in coverage["tables_with_no_applicable_rule"]) + ".",
            "",
            "---",
            "",
        ]
        return L

    if not findings:
        L += ["No suspected retail contamination detected in this diff.", ""]
        if coverage:
            L.append("Rules that actually ran: "
                     + ", ".join("`" + r + "`" for r in coverage["rules_applicable"]) + ".")
            if coverage["rules_never_applicable"]:
                L.append("Never applicable to this diff: "
                         + ", ".join("`" + r + "`" for r in coverage["rules_never_applicable"])
                         + " -- so this zero covers only the rules listed above.")
            if coverage["tables_with_no_applicable_rule"]:
                L.append("{} changed table(s) had no applicable rule and were not "
                         "scanned.".format(len(coverage["tables_with_no_applicable_rule"])))
            L.append("")
        L += ["---", ""]
        return L

    L.append(
        "Rows that look like retail-era data in a Classic+ build. `wow_classic_beta` "
        "is a recycled product code and Forever shares tooling with retail, so these "
        "turn up and get pruned over time — a **new** one appearing is itself a signal."
    )
    L.append("")
    L.append("| Confidence | Rule | Table | Record | Detail |")
    L.append("|---|---|---|---|---|")
    for f in findings:
        L.append(
            f"| {f['confidence'].upper()} | `{f['rule']}` | `{f['table']}` | "
            f"`{f['record']}` | {f['detail']} |"
        )
    L += ["", "---", ""]
    return L
