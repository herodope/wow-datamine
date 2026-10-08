#!/usr/bin/env python3
"""The player edition of a build diff: class -> spec -> spell, with verdicts.

`render_patchnotes.py` has always answered "which rows moved". A player asks
"what happened to my class", and foreverchanges.pro answers that well: class
by class, spell by spell, Buff / Nerf / Change, the number before and after,
and a grey line saying where the change came from. This module builds that
reading from the two builds' `wow.db` files, so the renderer only lays it out.

Four states per field, not two. A build diff compares client to client, but
players never see the client alone: they see client plus hotfixes. So every
field is read in all four states and the change is judged on what players
had before (old live) against what they have now (new live):

    oc  old client   plain_ tables of the older build
    ol  old live     unprefixed tables of the older build
    nc  new client   plain_ tables of the newer build
    nl  new live     unprefixed tables of the newer build

    ol != nl, nl == nc   CLIENT     shipped in the new client
    ol != nl, nl != nc   HOTFIX     live hotfix on the new build
    ol == nl, oc != nc   FOLDED     an old hotfix baked into the client: the
                                    client diff shows it, the game does not

FOLDED is the 70291 lesson. The 70170 warrior hotfix pass (112347) arrived in
the 70291 client byte for byte, and a client-to-client diff reported 16
player-facing spells as new that players had had for a week.

When the new build's overlay was never measured (`db2_hotfixed` identical to
`db2`, because nobody could log in yet) HOTFIX cannot occur and the model says
so with `overlay_measured = False`. That is a gap, not a clean result.

Talent values live in `TraitDefinitionEffectPoints` -> `CurvePoint`, not in
the spell tables. Redoubt 6 -> 4% per rank at 70170 changed only there, and
the old spell-change view never attributed it to the spell. They are read
here as one more field per talent, "value per rank".

Buff / Nerf is inferred only for fields whose direction is unambiguous: a
value's magnitude, a cost, a cooldown, a cast time, a range, a duration, the
level a spell is learned at. Everything else is "Change". A column WoWDBDefs
marks unverified is never given a direction or a decoded value.

Only player-facing spells are listed: `spell_reach.reachable = 1` and not a
cut-tier SoD tag. Everything else stays in the renderer's data appendix.

Stdlib only, read-only against both databases.
"""

import sqlite3

import config

CLIENT, HOTFIX, FOLDED = "client", "hotfix", "folded"
BUFF, NERF, CHANGE, NEW, REMOVED, TOOLTIP = "buff", "nerf", "change", "new", "removed", "tooltip"
VERDICT_ORDER = (NEW, BUFF, NERF, CHANGE, REMOVED, TOOLTIP)
VERDICT_TITLES = {NEW: "New", BUFF: "Buff", NERF: "Nerf", CHANGE: "Change",
                  REMOVED: "Removed", TOOLTIP: "Tooltip only"}

CUT_TIERS = ("sod_rune", "sod_book_candidate")

# Spell tables compared, and the columns that together with SpellID key a row.
# A column absent from a table is simply not used.
SPELL_TABLES = (
    ("SpellEffect", ("EffectIndex", "DifficultyID")),
    ("SpellMisc", ("DifficultyID",)),
    ("SpellLevels", ("DifficultyID",)),
    ("SpellPower", ("OrderIndex",)),
    ("SpellCooldowns", ("DifficultyID",)),
    ("SpellCategories", ("DifficultyID",)),
    ("SpellAuraOptions", ("DifficultyID",)),
    ("SpellClassOptions", ()),
    ("SpellInterrupts", ("DifficultyID",)),
    ("SpellCastingRequirements", ()),
    ("SpellTargetRestrictions", ("DifficultyID",)),
    ("SpellShapeshift", ()),
    ("SpellEquippedItems", ()),
    ("SpellAuraRestrictions", ("DifficultyID",)),
)
TEXT_COLUMNS = {("Spell", "Description_lang"): "Tooltip",
                ("Spell", "AuraDescription_lang"): "Buff tooltip",
                ("Spell", "NameSubtext_lang"): "Rank text"}

# Cosmetic: listed, never a verdict on their own beyond "Change".
COSMETIC = {"SpellIconFileDataID", "ActiveIconFileDataID", "SpellVisualScript",
            "ActiveSpellVisualScript"}

# column -> (label, direction). +1: bigger is better for the player,
# -1: smaller is better, "mag": bigger magnitude is better, 0: no direction.
FIELDS = {
    "EffectBasePointsF": ("Value", "mag"),
    "EffectRealPointsPerLevel": ("Value per level", "mag"),
    "EffectBonusCoefficient": ("Coefficient", "mag"),
    "EffectChainTargets": ("Targets", 1),
    "EffectChainAmplitude": ("Chain multiplier", 1),
    "EffectAuraPeriod": ("Tick interval", 0),
    "EffectAura": ("Aura type", 0),
    "Effect": ("Effect type", 0),
    "EffectTriggerSpell": ("Triggered spell", 0),
    "EffectRadiusIndex_0": ("Radius", 1),
    "EffectRadiusIndex_1": ("Max radius", 1),
    "EffectMiscValue_0": ("Misc value", 0),
    "EffectMiscValue_1": ("Misc value 2", 0),
    "DurationIndex": ("Duration", 1),
    "RangeIndex": ("Range", 1),
    "CastingTimeIndex": ("Cast time", -1),
    "ManaCost": ("Mana cost", -1),
    "ManaCostPerLevel": ("Mana cost per level", -1),
    "PowerCostPct": ("Cost (% of base)", -1),
    "RecoveryTime": ("Cooldown", -1),
    "CategoryRecoveryTime": ("Shared cooldown", -1),
    "StartRecoveryTime": ("Global cooldown", -1),
    "BaseLevel": ("Learned at level", -1),
    "SpellLevel": ("Spell level", 0),
    "MaxLevel": ("Max level", 0),
    "ProcChance": ("Proc chance", 1),
    "ProcCharges": ("Charges", 1),
    "CumulativeAura": ("Max stacks", 1),
    "ProcCategoryRecovery": ("Proc cooldown", -1),
    "DiminishType": ("Diminishing returns", 0),
    "SpellIconFileDataID": ("Icon", 0),
    "talent_value": ("Value per rank", "mag"),
}

# Index columns shown through their lookup table, per state.
LOOKUPS = {
    "DurationIndex": ("SpellDuration", "Duration", "ms"),
    "RangeIndex": ("SpellRange", "RangeMax_0", "yd"),
    "CastingTimeIndex": ("SpellCastTimes", "Base", "ms"),
    "EffectRadiusIndex_0": ("SpellRadius", "Radius", "yd"),
    "EffectRadiusIndex_1": ("SpellRadius", "Radius", "yd"),
}
MS_COLUMNS = {"RecoveryTime", "CategoryRecoveryTime", "StartRecoveryTime",
              "ProcCategoryRecovery", "EffectAuraPeriod"}

# Pet skill lines carry no ClassMask. Demons belong to warlocks, the rest to
# hunters.
WARLOCK_PETS = {188, 189, 204, 205, 206, 207, 2887, 2898}
CLASS_ORDER = ("Warrior", "Paladin", "Hunter", "Rogue", "Priest", "Shaman",
               "Mage", "Warlock", "Druid", "Races", "Professions & other",
               "Items")


def _open(build):
    path = config.build_out_dir(build) / "wow.db"
    if not path.exists():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info([{table}])")]


def _load(conn, table, key_cols):
    """{(spell, *key): {col: value}} -- the first row wins on a key collision."""
    if table not in _tables(conn):
        return {}
    cols = _columns(conn, table)
    spell_col = "ID" if table.endswith("Spell") and "SpellID" not in cols else "SpellID"
    keys = [spell_col] + [k for k in key_cols if k in cols]
    out = {}
    for row in conn.execute(f"SELECT * FROM [{table}]"):
        d = dict(zip(cols, row))
        k = tuple(d[c] for c in keys)
        if k not in out:
            out[k] = {c: v for c, v in d.items() if c not in ("ID",) and c not in keys}
    return out


def _talent_values(conn, prefix):
    """{(spell, effect_index): "4/8/12"} from trait effect points and curves."""
    need = {f"{prefix}TraitDefinition", f"{prefix}TraitDefinitionEffectPoints",
            f"{prefix}CurvePoint"}
    if not need <= _tables(conn):
        return {}
    q = f"""SELECT d.SpellID, p.EffectIndex, cp.Pos_1
            FROM {prefix}TraitDefinition d
            JOIN {prefix}TraitDefinitionEffectPoints p ON p.TraitDefinitionID = d.ID
            JOIN {prefix}CurvePoint cp ON cp.CurveID = p.CurveID
            WHERE d.SpellID > 0
            ORDER BY d.SpellID, p.EffectIndex, cp.OrderIndex, cp.Pos_0"""
    out = {}
    for spell, idx, v in conn.execute(q):
        out.setdefault((spell, idx), []).append(_num(v))
    return {k: "/".join(_fmt_num(x) for x in vals) for k, vals in out.items()}


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return int(f) if f.is_integer() else round(f, 4)


def _fmt_num(v):
    v = _num(v)
    return f"{v:g}" if isinstance(v, float) else str(v)


def _fmt_ms(ms):
    ms = _num(ms)
    if not isinstance(ms, (int, float)):
        return str(ms)
    if ms == 0:
        return "none"
    s = ms / 1000
    if s >= 120 and s % 60 == 0:
        return f"{int(s // 60)} min"
    return f"{s:g} sec"


class _Lookup:
    """Resolves index columns per database, so each side reads its own build."""

    def __init__(self, conn):
        self.conn, self.cache = conn, {}

    def value(self, column, idx):
        table, col, unit = LOOKUPS[column]
        key = (table, idx)
        if key not in self.cache:
            row = None
            if table in _tables(self.conn):
                row = self.conn.execute(f"SELECT [{col}] FROM [{table}] WHERE ID = ?",
                                        (idx,)).fetchone()
            self.cache[key] = None if row is None else _num(row[0])
        return self.cache[key], unit


def _display(column, value, lookup):
    """(text, comparable number or None) for one side of a change."""
    if value is None:
        return "—", None
    if column in LOOKUPS:
        if not value:
            return "none", 0
        v, unit = lookup.value(column, value)
        if v is None:
            return f"#{value}", None
        if unit == "ms":
            if column == "DurationIndex" and v == -1:
                return "until cancelled", float("inf")
            return _fmt_ms(v), v
        return f"{_fmt_num(v)} yd", v
    if column in MS_COLUMNS:
        return _fmt_ms(value), _num(value)
    if column == "talent_value":
        try:
            parts = [float(x) for x in str(value).split("/") if x]
        except ValueError:
            return str(value), None
        return str(value), (max(abs(x) for x in parts) if parts else None)
    v = _num(value)
    return (_fmt_num(v), v if isinstance(v, (int, float)) else None)


TABLE_NAMES = {"SpellPower": "Cost", "SpellCooldowns": "Cooldown",
               "SpellAuraOptions": "Aura options", "SpellLevels": "Level data",
               "SpellMisc": "Spell data", "SpellInterrupts": "Interrupt rules",
               "SpellShapeshift": "Form requirement", "SpellClassOptions": "Class options",
               "SpellTargetRestrictions": "Target rules",
               "SpellCastingRequirements": "Casting requirements",
               "SpellEquippedItems": "Weapon requirement",
               "SpellAuraRestrictions": "Aura requirement",
               "SpellCategories": "Category"}


def _technical(column):
    """A plain label for raw bitfields, or None. They get no direction and
    never headline a class: "Attributes_3 128 -> 262272" is real but unread."""
    if column.startswith("Attributes_"):
        return f"Spell flags ({column.split('_')[-1]})"
    if "SpellClassMask" in column:
        return "Affected spells (class mask)"
    if "InterruptFlags" in column:
        return "Interrupt rules"
    if column.endswith("Flags") or "Flags_" in column:
        return f"Flags ({column})"
    return None


def _row_summary(table, row):
    """One line for a row that appears or disappears."""
    if row is None:
        return None
    if table == "SpellEffect":
        bits = [f"effect {row.get('Effect')}"]
        if row.get("EffectAura"):
            bits.append(f"aura {row.get('EffectAura')}")
        if row.get("EffectBasePointsF"):
            bits.append(f"value {_fmt_num(row.get('EffectBasePointsF'))}")
        if row.get("EffectTriggerSpell"):
            bits.append(f"triggers {row.get('EffectTriggerSpell')}")
        return ", ".join(bits)
    if table == "SpellPower":
        return f"{_fmt_num(row.get('ManaCost'))} mana" if row.get("ManaCost") else "cost row"
    if table == "SpellCooldowns":
        return f"cooldown {_fmt_ms(row.get('RecoveryTime'))}"
    return "present"


def _direction(column, a, b):
    """+1 better for the player, -1 worse, 0 unknown."""
    rule = FIELDS.get(column, (None, 0))[1]
    if rule == 0 or a is None or b is None or a == b:
        return 0
    if rule == "mag":
        return 1 if abs(b) > abs(a) else -1
    return rule if b > a else -rule


class _Owners:
    """Spell -> (class, spec), from skill lines, talent trees and reach roots."""

    def __init__(self, conn):
        self.conn = conn
        cls = {r[0]: r[1] for r in conn.execute("SELECT ID, Name_lang FROM ChrClasses")}
        self.skill = {}
        for sid, name, cat in conn.execute(
                "SELECT ID, DisplayName_lang, CategoryID FROM SkillLine"):
            masks = {r[0] for r in conn.execute(
                "SELECT ClassMask FROM SkillRaceClassInfo WHERE SkillID = ? AND ClassMask > 0",
                (sid,))}
            owner = None
            if len(masks) == 1:
                m = masks.pop()
                if m & (m - 1) == 0:
                    owner = cls.get(m.bit_length())
            if owner is None and name and name.startswith("Pet - "):
                owner = "Warlock" if sid in WARLOCK_PETS else "Hunter"
                name = "Pets"
            if owner is None and name and "Racial" in name:
                owner, name = "Races", name.replace("Racial - ", "").replace(" Racial", "")
            if owner is None and cat != 7:
                owner = "Professions & other"
            if owner:
                self.skill[sid] = (owner, name or f"Skill {sid}")
        self.by_spell = {}
        for spell, sl in conn.execute("SELECT Spell, SkillLine FROM SkillLineAbility"):
            if sl in self.skill and spell not in self.by_spell:
                self.by_spell[spell] = self.skill[sl]
        # Talent trees: one per class, via its skill line.
        self.tree = {}
        if "SkillLineXTraitTree" in _tables(conn):
            for sl, tree in conn.execute("SELECT SkillLineID, TraitTreeID FROM SkillLineXTraitTree"):
                if sl in self.skill:
                    self.tree[tree] = self.skill[sl][0]
        self.talent = {}
        if {"TraitDefinition", "TraitNodeEntry", "TraitNodeXTraitNodeEntry",
                "TraitNode"} <= _tables(conn):
            for spell, tree in conn.execute(
                    """SELECT d.SpellID, n.TraitTreeID FROM TraitDefinition d
                       JOIN TraitNodeEntry e ON e.TraitDefinitionID = d.ID
                       JOIN TraitNodeXTraitNodeEntry x ON x.TraitNodeEntryID = e.ID
                       JOIN TraitNode n ON n.ID = x.TraitNodeID"""):
                if tree in self.tree:
                    self.talent.setdefault(spell, self.tree[tree])
        self.reach, self.tags = {}, {}
        tabs = _tables(conn)
        if "spell_reach" in tabs:
            for s, ok, kind, root_spell in conn.execute(
                    "SELECT spell_id, reachable, root_kind, root_spell FROM spell_reach"):
                self.reach[s] = (bool(ok), kind, root_spell)
        if "sod_tags" in tabs:
            for s, tier, live in conn.execute(
                    "SELECT spell_id, tier, live_in_forever FROM sod_tags"):
                self.tags[s] = (tier, bool(live))

    def player_facing(self, spell):
        ok = self.reach.get(spell, (False, None, None))[0]
        tier, live = self.tags.get(spell, (None, False))
        return ok and not (tier in CUT_TIERS and not live)

    def owner(self, spell):
        for s in (spell, self.reach.get(spell, (None, None, None))[2]):
            if s is None:
                continue
            if s in self.by_spell:
                cls, spec = self.by_spell[s]
                return cls, spec
            if s in self.talent:
                return self.talent[s], "Talents"
        kind = self.reach.get(spell, (None, None, None))[1]
        return ("Items", "Items") if kind == "item" else ("Professions & other", "Other")


def provenance(oc, ol, nc, nl):
    """(provenance, before, after) for one field, or None when nothing moved.

    Judged on live data: before is what players had (old live), after is
    what they have (new live). Only when the game did not change but the
    client did is it FOLDED, and then the client values are reported.
    """
    if ol == nl and oc == nc:
        return None
    if ol != nl:
        return (CLIENT if nl == nc else HOTFIX), ol, nl
    return FOLDED, oc, nc


def overlay_measured(build):
    """False when db2_hotfixed is the shipped client: the overlay is unknown."""
    import json
    path = config.build_out_dir(build) / "manifest.json"
    try:
        totals = json.loads(path.read_text(encoding="utf-8")).get("totals") or {}
    except (OSError, ValueError):
        return False
    return bool(totals.get("hotfix_delta"))


def build(from_build, to_build, unverified=lambda table: set()):
    """The player-edition model, or {"status": "unavailable", "reason": ...}."""
    old, new = _open(from_build), _open(to_build)
    if old is None or new is None:
        missing = from_build if old is None else to_build
        return {"status": "unavailable", "reason": f"no wow.db for {missing}"}

    states = {"oc": (old, "plain_"), "ol": (old, ""), "nc": (new, "plain_"), "nl": (new, "")}
    look = {"old": _Lookup(old), "new": _Lookup(new)}
    names, ranks, tips = {}, {}, {}
    for conn in (new, old):
        for sid, n in conn.execute("SELECT ID, Name_lang FROM SpellName"):
            names.setdefault(sid, n)
        for sid, r, d in conn.execute("SELECT ID, NameSubtext_lang, Description_lang FROM Spell"):
            ranks.setdefault(sid, r)
            tips.setdefault(sid, d)

    # field changes: spell -> list of change dicts
    changes = {}
    effects = {}

    def record(spell, table, column, key, vals):
        judged = provenance(*(vals[s] for s in ("oc", "ol", "nc", "nl")))
        if judged is None:
            return
        prov, before, after = judged
        if column == "_row":
            what = (f"Effect {key[0] + 1}" if table == "SpellEffect" and key
                    else TABLE_NAMES.get(table, table))
            label = f"{what} {'added' if before is None else 'removed' if after is None else 'changed'}"
            bt, at, bn, an = before or "—", after or "—", None, None
        elif column.lower() in {u.lower() for u in unverified(table)}:
            bt, bn, at, an = str(before), None, str(after), None
            label = f"{column} (unverified)"
        else:
            bt, bn = _display(column, before, look["old"])
            at, an = _display(column, after, look["new"])
            label = (TEXT_COLUMNS.get((table, column)) or _technical(column)
                     or FIELDS.get(column, (column, 0))[0])
        if key and table == "SpellEffect" and column != "_row":
            label = f"Effect {key[0] + 1}: {label}"
        elif column == "talent_value":
            label = f"Talent effect {key[0] + 1}: {label}"
        changes.setdefault(spell, []).append({
            "table": table, "column": column, "label": label, "provenance": prov,
            "before": bt, "after": at,
            "direction": 0 if (column in COSMETIC or (table, column) in TEXT_COLUMNS)
            else _direction(column, bn, an),
            "text": (table, column) in TEXT_COLUMNS,
            "cosmetic": column in COSMETIC,
            "technical": _technical(column),
        })

    for table, key_cols in SPELL_TABLES:
        data = {s: _load(c, p + table, key_cols) for s, (c, p) in states.items()}
        keys = set().union(*(d.keys() for d in data.values()))
        for k in keys:
            rows = {s: data[s].get(k) for s in states}
            if len({repr(sorted(r.items())) if r else None for r in rows.values()}) == 1:
                continue
            # A row that appears or disappears is one change, not one per
            # column: "Effect 2 added: aura 668", never six lines of "— -> 0".
            if (rows["ol"] is None) != (rows["nl"] is None) or \
                    (rows["ol"] is None and rows["nl"] is None):
                record(k[0], table, "_row", k[1:],
                       {s: _row_summary(table, r) for s, r in rows.items()})
                continue
            cols = set().union(*(r.keys() for r in rows.values() if r))
            for col in sorted(cols):
                vals = {s: (rows[s] or {}).get(col) for s in states}
                record(k[0], table, col, k[1:], vals)
        effects[table] = data

    data = {s: _load(c, p + "Spell", ()) for s, (c, p) in states.items()}
    for k in set().union(*(d.keys() for d in data.values())):
        for col in ("Description_lang", "AuraDescription_lang"):
            vals = {s: (data[s].get(k) or {}).get(col) for s in states}
            record(k[0], "Spell", col, (), vals)

    tv = {s: _talent_values(c, p) for s, (c, p) in states.items()}
    for k in set().union(*(d.keys() for d in tv.values())):
        vals = {s: tv[s].get(k) for s in states}
        for s in states:
            # A deleted curve is not a deleted value: the effect falls back to
            # the spell's own points (Improved Slam, 70170).
            if vals[s] is None:
                row = effects["SpellEffect"][s].get((k[0], k[1], 0))
                if row is not None:
                    vals[s] = f"spell value {_fmt_num(row.get('EffectBasePointsF'))}"
        record(k[0], "TraitDefinitionEffectPoints", "talent_value", k[1:], vals)

    # existence, per state: a spell new or gone in game
    exists = {s: {r[0] for r in c.execute(f"SELECT ID FROM {p}SpellName")}
              for s, (c, p) in states.items()}

    own_new, own_old = _Owners(new), _Owners(old)
    entries = {}
    folded = {}
    candidates = set(changes) | (exists["nl"] ^ exists["ol"])
    for spell in candidates:
        gone = spell not in exists["nl"]
        owners = own_old if gone else own_new
        if not owners.player_facing(spell):
            continue
        cls, spec = owners.owner(spell)
        ch = changes.get(spell, [])
        live = [c for c in ch if c["provenance"] != FOLDED]
        if spell in exists["nl"] and spell not in exists["ol"]:
            verdict = NEW
        elif gone and spell in exists["ol"]:
            verdict = REMOVED
        elif not live:
            if ch:
                folded.setdefault(cls, []).append(
                    {"spell_id": spell, "name": names.get(spell) or f"Spell {spell}",
                     "spec": spec, "changes": ch})
            continue
        else:
            dirs = {c["direction"] for c in live if c["direction"]}
            if all(c["text"] for c in live):
                verdict = TOOLTIP
            elif dirs == {1}:
                verdict = BUFF
            elif dirs == {-1}:
                verdict = NERF
            else:
                verdict = CHANGE
        name = names.get(spell) or f"Spell {spell}"
        e = entries.setdefault((cls, spec, name), {
            "class": cls, "spec": spec, "name": name, "spell_ids": [],
            "ranks": [], "verdicts": set(), "provenance": set()})
        e["spell_ids"].append(spell)
        e["verdicts"].add(verdict)
        e.setdefault("tooltip", tips.get(spell) or "")
        e["ranks"].append({"spell_id": spell, "rank": ranks.get(spell) or "",
                           "verdict": verdict, "changes": live})
        e["provenance"] |= {c["provenance"] for c in live}

    classes = {}
    for e in entries.values():
        v = e["verdicts"]
        e["verdict"] = (next(iter(v)) if len(v) == 1 else
                        NEW if NEW in v else REMOVED if v == {REMOVED} else
                        BUFF if v <= {BUFF, TOOLTIP} else
                        NERF if v <= {NERF, TOOLTIP} else CHANGE)
        e["ranks"].sort(key=lambda r: (_rank_no(r["rank"]), r["spell_id"]))
        e["highlight"] = _highlight(e)
        c = classes.setdefault(e["class"], {"name": e["class"], "specs": {}, "counts": {}})
        c["specs"].setdefault(e["spec"], []).append(e)
        c["counts"][e["verdict"]] = c["counts"].get(e["verdict"], 0) + 1
    for c in classes.values():
        for spec in c["specs"].values():
            spec.sort(key=lambda e: (VERDICT_ORDER.index(e["verdict"]), e["name"]))
        c["folded"] = sorted(folded.get(c["name"], []), key=lambda f: f["name"])
        ranked = sorted((e for s in c["specs"].values() for e in s),
                        key=lambda e: -e["highlight"][0])
        c["headline"] = [e for e in ranked if e["highlight"][0] > 0][:2]
    for cls, f in folded.items():
        if cls not in classes:
            classes[cls] = {"name": cls, "specs": {}, "counts": {}, "headline": [],
                            "folded": sorted(f, key=lambda x: x["name"])}

    totals = {}
    for c in classes.values():
        for k, v in c["counts"].items():
            totals[k] = totals.get(k, 0) + v
    ordered = [classes[n] for n in CLASS_ORDER if n in classes] + \
              [c for n, c in sorted(classes.items()) if n not in CLASS_ORDER]
    return {
        "status": "ok",
        "from_build": from_build, "to_build": to_build,
        "overlay_measured": {"from": overlay_measured(from_build),
                             "to": overlay_measured(to_build)},
        "classes": ordered,
        "totals": totals,
        "talent_value_changes": sum(
            1 for e in entries.values() for r in e["ranks"]
            for ch in r["changes"] if ch["column"] == "talent_value"),
        "folded_total": sum(len(v) for v in folded.values()),
    }


def _rank_no(text):
    digits = "".join(ch for ch in (text or "") if ch.isdigit())
    return int(digits) if digits else 0


def _highlight(e):
    """(score, sentence) for the class summary. New and removed first, then
    the largest relative numeric change."""
    if e["verdict"] == NEW:
        return 3.0, "new"
    if e["verdict"] == REMOVED:
        return 2.5, "removed"
    best = (0.0, "")
    for r in e["ranks"]:
        for c in r["changes"]:
            if c["text"] or c["cosmetic"] or c["technical"]:
                continue
            try:
                a, b = float(c["before"].split()[0].split("/")[-1]), \
                    float(c["after"].split()[0].split("/")[-1])
                rel = abs(b - a) / max(abs(a), 1e-9) if a else 1.0
            except (ValueError, IndexError, AttributeError):
                rel = 0.05
            # A change with a known direction always outranks one without:
            # "Aura type 22 -> 674" is a large relative move that says nothing.
            score = (1.0 + min(rel, 2.0)) if c["direction"] else 0.15 * min(rel, 2.0)
            if score > best[0]:
                if c["before"] == "—":
                    text = f"{c['label']}: {c['after']}"
                elif c["after"] == "—":
                    text = f"{c['label']}: {c['before']}"
                else:
                    text = f"{c['label']} {c['before']} → {c['after']}"
                best = (score, text)
    return best
