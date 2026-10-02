#!/usr/bin/env python3
"""Player reachability: can a player get this spell at all?

`sod_tags` answers "is this spell Season of Discovery content?". A patch note
needs a different question answered first: "is this spell on any path a
player can take?". 70170 showed the gap. Only 4 of its 967 changed spells
carried a SoD tag, yet the summary written from that diff reported Starfall
1300361, Renew 1289450 and a dozen other spells as class changes. Starfall's
tooltip points at a spell that does not exist in the client, and none of them
is on any skill line, talent tree or live item.

A spell is REACHABLE when a chain of edges leads to it from a root.

Roots (the places a player gets a spell from):

    skill_line   SkillLineAbility, AcquireMethod 0/1/2, except skill line
                 2851 Engraving. Engraving is the SoD rune system itself:
                 270 tagged rows sit on it. AcquireMethod 3 means "learned via
                 another spell" (Tiger's Fury, Judgement of Light) and arrives
                 through the edges instead.
    talent_tree  TraitDefinition SpellID / VisibleSpellID on a node of a LIVE
                 trait tree (see live_trait_trees). OverridesSpellID is the
                 spell being replaced, so it is not granted.
    talent       Talent.SpellRank_0..8, the classic talent table. sod_tags
                 already treats any Talent reference as live content.
    item         an ItemEffect spell of an item with an ItemSparse row, unless
                 the item is a SoD item (see sod_items). An ItemSparse row is
                 not evidence an item is live: 280 items whose own effect spell
                 is SoD-tagged still have one at 70170 ("Rune of Shadowstep",
                 the scrambled "Spell Notes" puzzle items).

Edges (how one reachable spell reaches another):

    trigger      SpellEffect.EffectTriggerSpell (a declared FK)
    enchant      an enchant effect (Effect 53/54/92/156) whose MiscValue_0 is a
                 SpellItemEnchantment, through Effect_N 1/3/7 (proc, equip,
                 use) to that slot's EffectArg_N
    override     EffectAura 332: EffectBasePointsF names the replacement

UNREACHABLE does not mean SoD, and it does not mean cut. Creature spells live
server-side and no client table leads to them; Holy Forgefire 1322218 is
reachable only because Verigan's Fist 6953 carries it. It means "not
verifiable from client data as something a player gets".

Reads the live (unprefixed, hotfixed) tables: talent trees move by hotfix
(push 112347 at 70170). Read-only against the connection it is given, so it
can run over wow.db, over build_db's database mid-build, or in memory.

Usage:
    python scripts/spell_reach.py                     # summary for the newest build
    python scripts/spell_reach.py --build 1.60.1.70170
    python scripts/spell_reach.py --spell 1300361     # one spell's chain
"""

import argparse
import json
import sqlite3
import sys

import config

ENGRAVING_SKILL_LINE = 2851
ROOT_ACQUIRE_METHODS = (0, 1, 2)
ENCHANT_EFFECTS = (53, 54, 92, 156)     # SpellEffect.Effect: enchant item variants
ENCHANT_SPELL_SLOTS = (1, 3, 7)         # SpellItemEnchantment.Effect_N: proc, equip, use
AURA_OVERRIDE = 332
TALENT_RANKS = tuple(f"SpellRank_{i}" for i in range(9))

# Tiers whose spells mark an item as SoD when the item reaches them. Mirrors
# sod_tags.CUT_TIERS plus sod_flag: an item whose effect lands on a flagged
# spell is still not evidence of live content. Kept as strings so this module
# does not import sod_tags (sod_tags imports this one).
SOD_ITEM_TIERS = ("sod_rune", "sod_book_candidate", "sod_flag")

ROOT_TABLES = {
    "skill_line": ("SkillLineAbility",),
    "talent_tree": ("TraitDefinition", "TraitNodeEntry", "TraitNodeXTraitNodeEntry",
                    "TraitNode", "TraitTree", "SkillLineXTraitTree"),
    "talent": ("Talent",),
    "item": ("ItemEffect", "ItemXItemEffect", "ItemSparse"),
}
EDGE_TABLES = ("SpellEffect",)
OPTIONAL_EDGE_TABLES = ("SpellItemEnchantment",)


def _rows(conn):
    out = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        try:
            out[name] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        except sqlite3.Error:
            out[name] = 0
    return out


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def have_rows(conn, table):
    try:
        return conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is not None
    except sqlite3.Error:
        return False


# --- talent trees -------------------------------------------------------------


def live_trait_trees(conn):
    """{tree_id: reason} for the trait trees a player can actually spend in.

    A class tree is live when SkillLineXTraitTree links it to a skill line.
    Measured at 70170, nine are: 1082 Shaman, 1089 Druid, 1091 Hunter, 1100
    Paladin, 1111 Rogue, 1112 Mage, 1114 Priest, 1116 Warlock, 1117 Warrior,
    all on TraitSystem 10. Trees 1066 and 1083 sit on that same system with no
    link, and 1058 and 1081 have no system: drafts, not live.

    A tree on a trait system that no linked tree uses is a different system
    and has no skill line to link to. The Legacy trees are the case:
    1187/1188/1189 on system 45, named by LegacyConsts
    LEGACY_TREE_PROFESSIONS_ID / _ADVENTURE_ID / _PROGRESSION_ID in the
    client's own API documentation. 1118 is on system 45 too, with no nodes
    at 70170: counted live, grants nothing.

    Shared with sod_tags, which marks a tagged spell on one of these trees as
    live_in_forever.
    """
    rows = _rows(conn)
    if not rows.get("TraitTree"):
        return {}
    linked = {}
    if rows.get("SkillLineXTraitTree"):
        for tree, skill in conn.execute("SELECT TraitTreeID, SkillLineID FROM SkillLineXTraitTree"):
            linked.setdefault(tree, skill)
    system = {t: _int(s) for t, s in conn.execute("SELECT ID, TraitSystemID FROM TraitTree")}
    class_systems = {system.get(t) for t in linked if system.get(t)}
    live = {t: f"talent tree {t} (SkillLine {s})" for t, s in linked.items()}
    for t, s in system.items():
        if t not in live and s and s not in class_systems:
            live[t] = f"trait tree {t} (TraitSystem {s})"
    return live


def trait_tree_spells(conn, trees=None):
    """{spell_id: (tree_id, reason)} for spells granted by a node of a live tree."""
    trees = live_trait_trees(conn) if trees is None else trees
    if not trees:
        return {}
    out = {}
    q = """SELECT td.SpellID, td.VisibleSpellID, tn.TraitTreeID
           FROM TraitDefinition td
           JOIN TraitNodeEntry te ON te.TraitDefinitionID = td.ID
           JOIN TraitNodeXTraitNodeEntry x ON x.TraitNodeEntryID = te.ID
           JOIN TraitNode tn ON tn.ID = x.TraitNodeID"""
    for spell, visible, tree in conn.execute(q):
        if tree not in trees:
            continue
        for s in (_int(spell), _int(visible)):
            if s > 0 and s not in out:
                out[s] = (tree, trees[tree])
    return out


# --- the graph ----------------------------------------------------------------


class _Graph:
    def __init__(self, conn):
        self.edges = {}            # spell -> [(child, kind)]
        enchant = {}
        if _rows(conn).get("SpellItemEnchantment"):
            for r in conn.execute("SELECT ID, Effect_0, EffectArg_0, Effect_1, EffectArg_1, "
                                  "Effect_2, EffectArg_2 FROM SpellItemEnchantment"):
                enchant[r[0]] = [_int(r[2 + 2 * s]) for s in range(3)
                                 if _int(r[1 + 2 * s]) in ENCHANT_SPELL_SLOTS and _int(r[2 + 2 * s]) > 0]
        for sid, eff, aura, trig, misc, bp in conn.execute(
                "SELECT SpellID, Effect, EffectAura, EffectTriggerSpell, EffectMiscValue_0, "
                "EffectBasePointsF FROM SpellEffect"):
            kids = self.edges.setdefault(sid, [])
            if _int(trig) > 0:
                kids.append((_int(trig), "trigger"))
            if _int(eff) in ENCHANT_EFFECTS:
                for k in enchant.get(_int(misc), ()):
                    kids.append((k, "enchant"))
            if _int(aura) == AURA_OVERRIDE and _int(bp) > 0:
                kids.append((_int(bp), "override"))

    def closure(self, starts):
        seen, todo = set(starts), list(starts)
        while todo:
            s = todo.pop()
            for k, _kind in self.edges.get(s, ()):
                if k not in seen:
                    seen.add(k)
                    todo.append(k)
        return seen


def sod_items(conn, graph, tags):
    """{item_id: reason} for items that hand out SoD content.

    An item is SoD when any spell its ItemEffect rows carry, or anything that
    spell reaches through the graph, is tagged in SOD_ITEM_TIERS and not
    live_in_forever. The second hop matters: the scrambled "Spell Notes" items
    carry an untagged spell that triggers a rune.

    `tags` is {spell_id: (tier, live_in_forever)}. sod_tags blocks propagation
    into spells shared with live content, so a tagged spell is SoD-specific
    and landing on one is not a value collision.
    """
    bad = {s for s, (tier, live) in tags.items() if tier in SOD_ITEM_TIERS and not live}
    by_item = {}
    for spell, item in conn.execute(
            "SELECT e.SpellID, x.ItemID FROM ItemEffect e "
            "JOIN ItemXItemEffect x ON x.ItemEffectID = e.ID"):
        if _int(spell) > 0:
            by_item.setdefault(item, set()).add(_int(spell))
    out = {}
    for item, spells in by_item.items():
        direct = spells & bad
        if direct:
            out[item] = f"effect spell {min(direct)} is SoD-tagged"
            continue
        hit = graph.closure(spells) & bad
        if hit:
            out[item] = f"effect spell reaches SoD-tagged {min(hit)}"
    return out


def compute(conn, tags=None, build=None):
    """Run reachability over one connection. Read-only.

    `tags` is {spell_id: (tier, live_in_forever)} from sod_tags; without it the
    SoD-item exclusion cannot run, and coverage says so.

    Returns {"build", "reach": {spell: row}, "coverage": [...], "stats": {...}}.
    Each reach row is {root_kind, root_id, root_spell, via_spell, via_edge, depth}.
    """
    rows = _rows(conn)
    coverage = []
    missing_edges = [t for t in EDGE_TABLES if not rows.get(t)]
    if missing_edges:
        raise RuntimeError("cannot compute reachability: "
                           + ", ".join(missing_edges) + " absent or empty")
    graph = _Graph(conn)
    stats = {"enchant_edges": "scanned" if rows.get("SpellItemEnchantment") else "not_scanned"}

    roots = {}                 # spell -> (kind, root_id)

    def cov(kind, extra_missing=()):
        missing = [t for t in ROOT_TABLES[kind] if not rows.get(t)] + list(extra_missing)
        entry = {"root": kind, "status": "not_scanned" if missing else "scanned",
                 "missing": missing, "hits": 0, "note": ""}
        coverage.append(entry)
        return entry

    def add(spell, kind, root_id, entry):
        if spell > 0 and spell not in roots:
            roots[spell] = (kind, root_id)
            entry["hits"] += 1

    c = cov("skill_line")
    if c["status"] == "scanned":
        for spell, acq, skill in conn.execute(
                "SELECT Spell, AcquireMethod, SkillLine FROM SkillLineAbility"):
            if _int(acq) in ROOT_ACQUIRE_METHODS and _int(skill) != ENGRAVING_SKILL_LINE:
                add(_int(spell), "skill_line", _int(skill), c)
        c["note"] = (f"AcquireMethod {'/'.join(map(str, ROOT_ACQUIRE_METHODS))}, "
                     f"skill line {ENGRAVING_SKILL_LINE} (Engraving) excluded")

    c = cov("talent_tree")
    if c["status"] == "scanned":
        trees = live_trait_trees(conn)
        for spell, (tree, _reason) in trait_tree_spells(conn, trees).items():
            add(spell, "talent_tree", tree, c)
        stats["live_trait_trees"] = sorted(trees)
        c["note"] = f"{len(trees)} live tree(s): {', '.join(map(str, sorted(trees)))}"

    c = cov("talent")
    if c["status"] == "scanned":
        cols = {r[1] for r in conn.execute('PRAGMA table_info("Talent")')}
        for col in (x for x in TALENT_RANKS if x in cols):
            for tid, spell in conn.execute(f'SELECT ID, "{col}" FROM Talent'):
                add(_int(spell), "talent", tid, c)

    c = cov("item", [] if tags is not None else ["sod_tags (SoD-item exclusion)"])
    if all(rows.get(t) for t in ROOT_TABLES["item"]):
        excluded = sod_items(conn, graph, tags) if tags is not None else {}
        stats["sod_items_excluded"] = len(excluded)
        stats["sod_items_sample"] = sorted(excluded)[:20]
        live_items = {r[0] for r in conn.execute("SELECT ID FROM ItemSparse")}
        for spell, item in conn.execute(
                "SELECT e.SpellID, x.ItemID FROM ItemEffect e "
                "JOIN ItemXItemEffect x ON x.ItemEffectID = e.ID"):
            if item in live_items and item not in excluded:
                add(_int(spell), "item", item, c)
        c["note"] = f"{len(excluded)} SoD item(s) excluded"
        if tags is None:
            c["note"] = "NO SoD-item exclusion: SoD rune and Spell Notes items count as live"

    # Breadth-first, so the recorded chain to each spell is a shortest one.
    reach = {s: {"root_kind": k, "root_id": r, "root_spell": s, "via_spell": None,
                 "via_edge": None, "depth": 0} for s, (k, r) in roots.items()}
    frontier = sorted(roots)
    while frontier:
        nxt = []
        for s in frontier:
            base = reach[s]
            for k, kind in graph.edges.get(s, ()):
                if k not in reach:
                    reach[k] = {"root_kind": base["root_kind"], "root_id": base["root_id"],
                                "root_spell": base["root_spell"], "via_spell": s,
                                "via_edge": kind, "depth": base["depth"] + 1}
                    nxt.append(k)
        frontier = nxt

    names = {i: n for i, n in conn.execute("SELECT ID, Name_lang FROM SpellName")} \
        if rows.get("SpellName") else {}
    stats["spells"] = len(names)
    stats["roots"] = len(roots)
    stats["reachable"] = sum(1 for s in names if s in reach)
    return {"build": build, "reach": reach, "names": names, "coverage": coverage,
            "stats": stats}


def chain(result, spell_id):
    """[spell, parent, ..., root] for a reachable spell; [] otherwise."""
    out, s = [], spell_id
    while s is not None and s in result["reach"] and len(out) < 32:
        out.append(s)
        s = result["reach"][s]["via_spell"]
    return out


def materialize(conn, result, build):
    """Write spell_reach and spell_reach_coverage into `conn`.

    One row per SpellName ID, reachable or not, so an absent row means "not a
    spell in this build" and never "not examined".
    """
    conn.execute("DROP TABLE IF EXISTS spell_reach")
    conn.execute("""CREATE TABLE spell_reach (
        spell_id    INTEGER PRIMARY KEY,
        reachable   INTEGER NOT NULL,  -- 1: some root leads here
        root_kind   TEXT,              -- skill_line / talent_tree / talent / item
        root_id     INTEGER,           -- SkillLine, TraitTree, Talent or Item ID
        root_spell  INTEGER,           -- the spell the root grants
        via_spell   INTEGER,           -- parent on the shortest chain; NULL at a root
        via_edge    TEXT,              -- trigger / enchant / override
        depth       INTEGER,
        build       TEXT NOT NULL)""")
    reach = result["reach"]
    conn.executemany(
        "INSERT INTO spell_reach VALUES (?,?,?,?,?,?,?,?,?)",
        [(s, int(s in reach),
          *((reach[s]["root_kind"], reach[s]["root_id"], reach[s]["root_spell"],
             reach[s]["via_spell"], reach[s]["via_edge"], reach[s]["depth"])
            if s in reach else (None,) * 6),
          build)
         for s in sorted(result["names"])])
    conn.execute("CREATE INDEX ix_spell_reach_reachable ON spell_reach (reachable)")

    conn.execute("DROP TABLE IF EXISTS spell_reach_coverage")
    conn.execute("""CREATE TABLE spell_reach_coverage (
        root TEXT PRIMARY KEY, status TEXT NOT NULL, missing TEXT NOT NULL,
        hits INTEGER NOT NULL, note TEXT, build TEXT NOT NULL)""")
    conn.executemany("INSERT INTO spell_reach_coverage VALUES (?,?,?,?,?,?)",
                     [(c["root"], c["status"], json.dumps(c["missing"]), c["hits"],
                       c["note"], build) for c in result["coverage"]])


# --- reading a built database -------------------------------------------------


def load(build):
    """{"status", "reach": {spell: (reachable, root_kind, root_id)}, "tags": {...},
    "names": {...}} read from out/<build>/wow.db.

    status is "ok", or "unavailable" with a reason (no database). Callers must
    render "unavailable" as NOT MEASURED, never as "nothing reachable".

    A database built before spell_reach existed is computed on the fly,
    read-only: rebuilding it may be impossible while mcp_server.py holds the
    file open (WinError 5). Its sod_tags predates the talent-tree rule too, so
    that rule is applied here as well. `computed` says which happened.
    """
    db = config.build_out_dir(build) / "wow.db"
    if not db.exists():
        return {"status": "unavailable", "reason": f"no wow.db for {build}"}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        out = {"status": "ok", "build": build, "computed": False}
        out["tags"] = {}
        if "sod_tags" in have:
            out["tags"] = {s: (t, bool(l)) for s, t, l in conn.execute(
                "SELECT spell_id, tier, live_in_forever FROM sod_tags")}
            cols = {r[1] for r in conn.execute('PRAGMA table_info("sod_tags")')}
            if "live_source" not in cols and all(have_rows(conn, t) for t in ROOT_TABLES["talent_tree"]):
                for s in trait_tree_spells(conn):
                    if s in out["tags"]:
                        out["tags"][s] = (out["tags"][s][0], True)
        else:
            out["tags_missing"] = True
        if "spell_reach" in have:
            out["reach"] = {s: (bool(r), k, i) for s, r, k, i in conn.execute(
                "SELECT spell_id, reachable, root_kind, root_id FROM spell_reach")}
            out["not_scanned"] = [r[0] for r in conn.execute(
                "SELECT root FROM spell_reach_coverage WHERE status <> 'scanned'")]
        else:
            res = compute(conn, out["tags"] if "sod_tags" in have else None, build)
            out["computed"] = True
            out["reach"] = {s: (True, r["root_kind"], r["root_id"]) for s, r in res["reach"].items()}
            out["not_scanned"] = [c["root"] for c in res["coverage"] if c["status"] != "scanned"]
        out["names"] = {i: n for i, n in conn.execute("SELECT ID, Name_lang FROM SpellName")}
        return out
    finally:
        conn.close()


# --- classifying a build diff -------------------------------------------------

PLAYER, SOD, UNREACHABLE, UNMEASURED = "player", "sod", "unreachable", "unmeasured"
GROUPS = (PLAYER, SOD, UNREACHABLE, UNMEASURED)
GROUP_TITLES = {
    PLAYER: "Player-facing",
    SOD: "Season of Discovery (cut tiers)",
    UNREACHABLE: "Unreachable / server-side",
    UNMEASURED: "Not measured",
}
CUT_TIERS = ("sod_rune", "sod_book_candidate")


def _spell_column(table, header):
    """The column naming the spell a row belongs to, or None.

    Spell and SpellName are keyed on the spell itself. Other Spell* tables
    carry SpellID. SpellXSpellVisual and friends are presentation, not
    tuning, and are classified as such rather than skipped.
    """
    if not header or not table.startswith("Spell"):
        return None
    if table in ("Spell", "SpellName"):
        return "ID"
    return "SpellID" if "SpellID" in header else None


def _kind(table, header, old, new):
    if table.startswith("SpellXSpellVisual") or table.startswith("SpellVisual"):
        return "visual"
    # Only a Spell / SpellName row appearing or vanishing is a new or removed
    # spell. An added SpellEffect row on an existing spell is a mechanical
    # change to that spell, and labelling it "added" read as "new spell".
    if old is None or new is None:
        if table in ("Spell", "SpellName"):
            return "new spell" if old is None else "removed spell"
        return "mechanical"
    changed = [h for i, h in enumerate(header)
               if (old[i] if i < len(old) else "") != (new[i] if i < len(new) else "")]
    if changed and all(h.endswith("_lang") for h in changed):
        return "text"
    return "mechanical"


def classify_changes(results, from_build, to_build):
    """Partition every spell touched by a build diff into GROUPS.

    `results` is {table: diff_builds.diff_table(...) result} (dict or list).
    Group precedence: a cut-tier SoD tag that is not live_in_forever wins;
    otherwise reachable means player-facing; otherwise unreachable. A spell
    removed in `to_build` is judged by `from_build`'s database, where it still
    exists. Without spell_reach in the relevant database the spell is
    UNMEASURED, never unreachable.
    """
    if isinstance(results, dict):
        results = list(results.values())
    new_db, old_db = load(to_build), load(from_build)

    touched = {}               # spell -> {"kinds": set, "tables": set, "removed": bool}
    for r in results:
        col = _spell_column(r["table"], r.get("header"))
        if col is None or not r.get("rows_comparable", True) and not (r["added"] or r["removed"]):
            continue
        header = r["header"]
        if col not in header:
            continue
        i = header.index(col)
        olds, news = r["old_rows"], r["new_rows"]
        keys = list(r["added"]) + list(r["removed"]) + [k for k, _d in r["changed"]]
        for key in keys:
            o, n = olds.get(key), news.get(key)
            row = n if n is not None else o
            spell = _int(row[i]) if i < len(row) else 0
            if spell <= 0:
                continue
            t = touched.setdefault(spell, {"kinds": set(), "tables": set(), "removed": False})
            t["tables"].add(r["table"])
            if r.get("rows_comparable", True) or o is None or n is None:
                t["kinds"].add(_kind(r["table"], header, o, n))
            else:
                t["kinds"].add("mechanical")
            if r["table"] in ("Spell", "SpellName") and n is None:
                t["removed"] = True

    groups = {g: [] for g in GROUPS}
    for spell, t in touched.items():
        src = old_db if t["removed"] else new_db
        entry = {"spell_id": spell, "kinds": sorted(t["kinds"]), "tables": sorted(t["tables"]),
                 "removed": t["removed"], "tier": None, "live_in_forever": False,
                 "root_kind": None, "root_id": None, "name": None}
        if src["status"] != "ok":
            groups[UNMEASURED].append(entry)
            continue
        entry["name"] = src["names"].get(spell) or new_db.get("names", {}).get(spell)
        tier, live = src["tags"].get(spell, (None, False))
        entry["tier"], entry["live_in_forever"] = tier, live
        reachable, kind, rid = src["reach"].get(spell, (False, None, None))
        entry["root_kind"], entry["root_id"] = kind, rid
        if tier in CUT_TIERS and not live:
            groups[SOD].append(entry)
        elif reachable:
            groups[PLAYER].append(entry)
        else:
            groups[UNREACHABLE].append(entry)

    def order(e):
        return ("mechanical" not in e["kinds"], e["name"] or "~", e["spell_id"])
    for g in groups:
        groups[g].sort(key=order)
    return {
        "status": "ok" if new_db["status"] == "ok" else "unavailable",
        "reason": new_db.get("reason"),
        "old_status": old_db["status"],
        "old_reason": old_db.get("reason"),
        "tags_missing": bool(new_db.get("tags_missing")),
        "not_scanned": new_db.get("not_scanned", []),
        "groups": groups,
        "total": len(touched),
    }


def describe(e):
    """One line of plain text for a classified spell."""
    root = {"skill_line": "skill line", "talent_tree": "talent tree", "talent": "talent",
            "item": "item"}.get(e["root_kind"])
    bits = [", ".join(e["kinds"])]
    if root:
        bits.append(f"{root} {e['root_id']}")
    if e["tier"]:
        bits.append(e["tier"] + (" (live)" if e["live_in_forever"] else ""))
    if e["removed"]:
        bits.append("removed")
    return "; ".join(bits)


def render_markdown(c, limit=60):
    """The partition as a markdown section for diff_builds.py."""
    L = ["## Spell changes by reachability", ""]
    if c["status"] != "ok":
        L += [f"**NOT MEASURED** — {c['reason']}. Spell changes are listed per table "
              "below, unpartitioned.", "", "---", ""]
        return L
    g = c["groups"]
    L += ["Every spell touched by a `Spell*` table, sorted by whether a player can get "
          "it. **Player-facing** means a skill line, live talent tree, talent or live "
          "item leads to it (`spell_reach` in `wow.db`). **Season of Discovery** means "
          "a cut-tier `sod_tags` row that no live talent tree rescues. "
          "**Unreachable** is not proof of anything: creature spells live server-side. "
          "Mechanical changes sort first; `text` means only `_lang` columns moved.", ""]
    L += ["| Group | Spells | Mechanical |", "|---|--:|--:|"]
    for k in GROUPS:
        if g[k] or k != UNMEASURED:
            L.append(f"| {GROUP_TITLES[k]} | {len(g[k]):,} | "
                     f"{sum('mechanical' in e['kinds'] for e in g[k]):,} |")
    L.append("")
    if c["not_scanned"]:
        L += [f"> ⚠️ Root(s) not scanned: {', '.join(c['not_scanned'])}. Spells "
              "reachable only that way are listed as unreachable.", ""]
    if c["tags_missing"]:
        L += ["> ⚠️ `sod_tags` is missing from this database, so nothing is in the "
              "Season of Discovery group.", ""]
    for k in GROUPS:
        rows = g[k]
        if not rows:
            continue
        L += [f"### {GROUP_TITLES[k]} ({len(rows):,})", "",
              "| ID | Name | Change |", "|---|---|---|"]
        for e in rows[:limit]:
            L.append(f"| `{e['spell_id']}` | {e['name'] or '—'} | {describe(e)} |")
        if len(rows) > limit:
            L.append(f"| … | *{len(rows) - limit:,} more* | |")
        L.append("")
    L += ["---", ""]
    return L


# --- CLI ----------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", help="Forever build (default: newest with a wow.db)")
    ap.add_argument("--spell", type=int, action="append", help="show one spell's chain")
    args = ap.parse_args(argv)

    import sod_tags
    build = args.build or sod_tags._newest_db()
    if not build:
        print("no wow.db found under out/", file=sys.stderr)
        return 2
    conn = sod_tags.open_ro(build)
    tags = {s: (t, bool(l)) for s, t, l in conn.execute(
        "SELECT spell_id, tier, live_in_forever FROM sod_tags")}
    result = compute(conn, tags, build)
    st = result["stats"]
    print(f"{build}: {st['reachable']:,} of {st['spells']:,} spells reachable "
          f"from {st['roots']:,} roots; {st.get('sod_items_excluded', 0)} SoD item(s) excluded")
    for c in result["coverage"]:
        print(f"  {c['root']:12} {c['status']:11} {c['hits']:>6,}  {c['note']}"
              + (f"  missing: {', '.join(c['missing'])}" if c["missing"] else ""))
    for s in args.spell or ():
        ch = chain(result, s)
        name = result["names"].get(s)
        if not ch:
            print(f"\n{s} {name!r}: UNREACHABLE")
            continue
        r = result["reach"][s]
        print(f"\n{s} {name!r}: reachable from {r['root_kind']} {r['root_id']}")
        for x in ch:
            rx = result["reach"][x]
            how = f"<- {rx['via_edge']} from {rx['via_spell']}" if rx["via_spell"] else "(root)"
            print(f"  {x:>8} {result['names'].get(x)!r:40} {how}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
