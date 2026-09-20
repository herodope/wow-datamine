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
        row = result["plain_rows"].get(key) if which == "removed" else result["hot_rows"].get(key)
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

    # Removed rows exist only on the plain side -- the hotfixed Item table no
    # longer contains them, so load_table() cannot describe them.
    ih, items = result["header"], result["plain_rows"]
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
            f"these were pulled together in one push."
        ),
        "side": "removed",
    }]


def scan(results, load_table):
    """Run every rule over the diff results. Returns a list of findings."""
    ref = build_reference(load_table)
    findings = []
    for r in results:
        findings += _dangling_map_refs(r, ref, r["removed"], "removed")
        findings += _dangling_map_refs(r, ref, r["added"], "added")
        findings += _light_absent_map(r, ref)
        findings += _orphan_removal(r, ref, load_table)

    order = {HIGH: 0, MEDIUM: 1}
    findings.sort(key=lambda f: (order.get(f["confidence"], 9), f["table"], str(f["record"])))
    return findings, ref
