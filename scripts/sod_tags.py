#!/usr/bin/env python3
"""Tag Season of Discovery content sitting in a Forever snapshot.

This is a SNAPSHOT-WIDE ANNOTATION, not a contamination rule, and the
difference is deliberate. `contamination.py` is diff-scoped: it looks only at
rows added or removed between two builds, emits independent per-rule findings,
never merges them into a per-row verdict, has no allowlist, and is not
queryable from wow.db. SoD content is not changing between builds -- it is
simply present in every snapshot, left over from the 1.15 client Forever was
cut from. So this module tags the standing population, merges its evidence
into one tier per spell, honours a manual allowlist, and materialises into
wow.db as `sod_tags` / `sod_tags_coverage`. Do not move it into
`contamination.scan()`.

Tagging, never deletion. Nothing here removes a row or changes what any other
query returns; a query excludes SoD content only if it joins `sod_tags`.

Tiers, strongest first:

    sod_rune            full engraving chain. High confidence, likely cut.
    sod_book_candidate  taught by a learn item with no ItemSparse row. Medium,
                        NOT proof of cut.
    sod_ported          a SoD rune/book ability whose same-name trainer
                        sibling is absent from the SoD-era 1.15 client -- i.e.
                        Forever re-added the ability as a baseline spell
                        (Mutilate 399956 <-> 1241582). NOT cut. The sibling is
                        `forever_sibling_id` and stays untagged.
    sod_variant         a SoD-specific ID of a spell that also exists as a
                        normal trainer spell. "SoD version", NOT "cut ability";
                        the base spell stays untagged.
    sod_flag            weak signal only. Reported, never treated as cut.

`sod_manual.json` adds hand-audited tags after detection (source_rule
'manual') for spells no structural signal reaches; `sod_allowlist.json` marks
detected tags live_in_forever. Both are applied after every step has run.

A tag that a live Forever talent tree grants is marked live_in_forever too,
structurally (live_source 'trait_tree'), after the allowlist. The tier is
kept: the spell IS SoD-derived, it just is not cut. Measured at 70170: 38
tagged spells are nodes on live trees -- 27 sod_rune, 8 sod_ported, 2
sod_book_candidate, 1 sod_flag (Heating Up, Fingers of Frost, Maelstrom
Weapon, Divine Aegis, Pandemic, ...). Which trees are live is
spell_reach.live_trait_trees(), shared so the two never disagree.

Likewise for a trainer row Forever added or changed (live_source
'forever_trainer'): AcquireMethod 0 on a non-Engraving skill line, with
SpellLevels, that the SoD reference's SkillLineAbility lacks. A row identical
to SoD's is not evidence -- SoD runes carry them (Aspect of the Viper,
Shadowfiend, Redirect). Measured at 70170, 12 qualify: Fire Nova
408341-408345, Victory Rush 402927, Hammer of the Righteous 407632, and five
spells already sod_ported (Mutilate 399956 among them), which corroborates
that rule independently.

Class attribution comes from the chain ORIGIN, not the tagged spell: the
engrave spell for a rune chain, the taught spell for a book, the parent for a
propagated or override edge. Each origin's class is SkillLineAbility.ClassMask
first, SpellClassSet second -- Exorcist 415076 carries SpellClassSet 5
(the warlock family) but ClassMask 2 (Paladin).

Bias toward under-tagging: a false "cut" on a live Forever spell is worse than
a missed SoD spell, so every ambiguous case takes the weaker tier.

Detection, measured on 1.60.1.70009:

  1 rune_chain   SpellName 'Engrave %' -> SpellEffect Effect=54, MiscValue_0
                 = SpellItemEnchantment.ID -> Effect_N=3, EffectArg_N = the
                 equip ("wrapper") spell -> that spell's EffectAura=332 effects,
                 EffectBasePointsF = the granted ability. 270 chains.
                 EffectArg is a declared FK to Spell. MiscValue_0 and
                 EffectBasePointsF are effect-typed slots with no DBD relation,
                 so both hops are gated on the exact Effect/EffectAura value.
  2 book_set     ItemEffect TriggerType=6 -> ItemXItemEffect -> an item with no
                 live ItemSparse row, teaching a spell with SpellClassSet > 0.
                 Items of ItemClass 9 (Recipe) are excluded: every one of them
                 is a vanilla-style class book teaching a ranked base spell
                 (Shadow Bolt 1088, Purify 1152, ...). Every SoD book is 0/8.
  3 propagation  SpellEffect.EffectTriggerSpell (declared) out of a tagged
                 spell, one hop at a time, child inherits the parent's tier.
                 Plus one constrained effect-typed edge, seal_dummy_bp
                 (source_rule 'seal_dummy_bp'): an EffectAura=4 effect whose
                 EffectBasePointsF names an existing spell of the same
                 SpellClassSet, that is (a) not a trainer spell (no
                 AcquireMethod-0 SkillLineAbility row) and (b) shares a
                 SkillLine with the source IF it has any SkillLineAbility row.
                 That is how a seal names its Judgement: Seal of Martyrdom
                 407798 -> Judgement of Martyrdom 407803. Measured over all 54
                 classed aura-4 same-family landings at 70009 (35 seal ->
                 own Judgement, 19 value collisions such as Blizzard 10 from
                 Enlightenment's bp 10):
                     same family only        35/35 kept, 19/19 collisions
                     (a)                     35/35 kept,  0/19
                     (a) + (b) strict        32/35 kept,  0/19
                     (a) + (b) conditional   35/35 kept,  0/19   <- in use
                 Strict (b) drops 21183, 1311650 and 1311655, Judgements with
                 no SkillLineAbility row at all.
                 A child also referenced by an UNTAGGED spell through a
                 declared cross-spell column, or by Talent, is shared with
                 live content: the edge is blocked and the child left
                 untagged. Forbearance 25771 and Dummy Trigger 18350 are the
                 measured cases.
                 Never through SpellClassMask / EffectSpellClassMask.
  4 variant      (a) an EffectAura=332 override on a tagged spell whose
                 MiscValue_0 (the spell being replaced) has the same name as
                 the replacement and is an untagged trainer spell -- Exorcist
                 415076 overrides Exorcism 879..10314 with 415068..415073.
                 (b) same Name_lang + SpellClassSet as an untagged trainer spell
                 (SkillLineAbility AcquireMethod=0 plus a SpellLevels row),
                 where the SoD side is tagged by 1-3 or carries label 3096/3100.
                 A (b) pair whose SoD side was tagged by 1-3 and whose sibling is
                 absent from the newest extracted 1.15 build is sod_ported, not
                 sod_variant. That is a presence check against a real build,
                 not an ID range.
  5 labels       3071 corroborates sod_rune on engrave spells. 3096 / 3100 are
                 mixed (rune abilities AND book spells) -> sod_flag on their own.

Deliberately NOT signals: spell ID ranges, EffectAura=332 alone (510 spells,
most not reached by any rune), AcquireMethod=3 alone, raw value scans, and
"unreferenced" / "no display data" alone. See CLAUDE.md.

Usage:
    python scripts/sod_tags.py                      # report for the newest build
    python scripts/sod_tags.py --build 1.60.1.70009
    python scripts/sod_tags.py --spell 407798       # one spell's tag and chain
"""

import argparse
import csv
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone

import config
import spell_reach

RUNE, BOOK, VARIANT, FLAG = "sod_rune", "sod_book_candidate", "sod_variant", "sod_flag"
PORTED = "sod_ported"
TIERS = (RUNE, BOOK, PORTED, VARIANT, FLAG)
RANK = {RUNE: 5, BOOK: 4, PORTED: 3, VARIANT: 2, FLAG: 1}
# Tiers whose spells are treated as cut. sod_ported, sod_variant and sod_flag
# never are.
CUT_TIERS = (RUNE, BOOK)

EFFECT_ENCHANT_ITEM = 54        # SpellEffect.Effect: engrave applies an enchant
ENCHANT_EQUIP_SPELL = 3         # SpellItemEnchantment.Effect_N: equip spell
AURA_OVERRIDE = 332             # EffectAura: override action spell
AURA_DUMMY = 4                  # EffectAura: dummy; seals name their Judgement here
TRIGGER_LEARN = 6               # ItemEffect.TriggerType: on learn
ITEM_CLASS_RECIPE = 9

LABEL_ENGRAVE = 3071
LABELS_MIXED = (3096, 3100)

# Columns through which one spell's row names ANOTHER spell. Every one is
# declared in GET /dbc/relations/Spell::ID at 1.60.1.70009 (157 columns in
# all); the owner column says which spell the row belongs to. Used only by the
# propagation guard: a spell referenced from an untagged owner is shared with
# live content. Resolving these instead of scanning values is rule 1.
SHARED_REFS = (
    ("SpellEffect", "EffectTriggerSpell", "SpellID"),
    ("SpellAuraRestrictions", "CasterAuraSpell", "SpellID"),
    ("SpellAuraRestrictions", "ExcludeCasterAuraSpell", "SpellID"),
    ("SpellAuraRestrictions", "ExcludeTargetAuraSpell", "SpellID"),
    ("SpellClassOptions", "ModalNextSpell", "SpellID"),
    ("SpellCooldowns", "AuraSpellID", "SpellID"),
    ("SpellPower", "RequiredAuraSpellID", "SpellID"),
    ("SpellLearnSpell", "LearnSpellID", "SpellID"),
    ("SpellLearnSpell", "OverridesSpellID", "SpellID"),
    ("SkillLineAbility", "SupercedesSpell", "Spell"),
)
# Any Talent reference means a live talent, whoever else points at it.
TALENT_REFS = ("SpellID", "OverridesSpellID", "RequiredSpellID")

STEPS = ("rune_chain", "book_set", "propagation", "variant", "labels", "trait_tree",
         "forever_trainer")
STEP_TABLES = {
    "rune_chain": ("SpellName", "SpellEffect", "SpellItemEnchantment"),
    "book_set": ("ItemEffect", "ItemXItemEffect", "ItemSparse", "SpellClassOptions", "Item"),
    "propagation": ("SpellEffect",),
    "variant": ("SpellName", "SpellClassOptions", "SkillLineAbility", "SpellLevels"),
    "labels": ("SpellLabel",),
    # Not a tagging step: it finds which tagged spells live talent trees grant,
    # and detect() marks them live_in_forever after the allowlist.
    "trait_tree": spell_reach.ROOT_TABLES["talent_tree"],
    # Not a tagging step either: tagged spells whose trainer row Forever
    # added or changed relative to the SoD reference. Applied with trait_tree.
    "forever_trainer": ("SkillLineAbility", "SpellLevels"),
}
# Tables whose absence weakens a step without stopping it. Recorded in
# coverage so a thinner guard is visible rather than silent.
OPTIONAL_TABLES = {
    "propagation": tuple(sorted({t for t, _c, _o in SHARED_REFS} | {"Talent"})),
    "variant": ("Spell",),
}


# --- reading ----------------------------------------------------------------


def _table_rows(conn):
    """{table: row count} for every table in the database."""
    out = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        try:
            out[name] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        except sqlite3.Error:
            out[name] = 0
    return out


def _columns(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


class _Data:
    """Everything detection reads, pulled into dicts once.

    Detection runs against wow.db (read-only), against build_db's temporary
    database mid-build, or against an in-memory load of the CSVs, so it must
    not create indexes or temp tables. SpellEffect is ~42k rows; holding it in
    memory is cheaper than asking SQLite the same questions per spell.
    """

    def __init__(self, conn, reference=None):
        self.conn = conn
        self.rows = _table_rows(conn)
        self.reference_build, self.reference_ids = reference or (None, set())
        self.reference_sla = None  # {spell: {(skillline, acquire)}}; detect() fills it

        def have(t):
            return self.rows.get(t, 0) > 0

        self.have = have
        q = conn.execute

        self.name = {i: n for i, n in q("SELECT ID, Name_lang FROM SpellName")} \
            if have("SpellName") else {}
        self.rank = {}
        if have("Spell") and "NameSubtext_lang" in _columns(conn, "Spell"):
            self.rank = {i: (s or "") for i, s in q("SELECT ID, NameSubtext_lang FROM Spell")}

        self.effects = {}          # spell -> [(idx, effect, aura, misc0, basepoints, trigger)]
        self.triggered_by = {}     # spell -> {owner spells}
        if have("SpellEffect"):
            for sid, idx, eff, aura, misc, bp, trig in q(
                    "SELECT SpellID, EffectIndex, Effect, EffectAura, EffectMiscValue_0, "
                    "EffectBasePointsF, EffectTriggerSpell FROM SpellEffect"):
                trig = _int(trig)
                self.effects.setdefault(sid, []).append(
                    (idx, _int(eff), _int(aura), _int(misc), _int(bp), trig))
                if trig:
                    self.triggered_by.setdefault(trig, set()).add(sid)

        self.enchant = {}          # enchant -> [(slot, effect, arg)]
        if have("SpellItemEnchantment"):
            for r in q("SELECT ID, Effect_0, EffectArg_0, Effect_1, EffectArg_1, "
                       "Effect_2, EffectArg_2 FROM SpellItemEnchantment"):
                self.enchant[r[0]] = [(s, _int(r[1 + 2 * s]), _int(r[2 + 2 * s])) for s in range(3)]

        self.class_set = {}
        if have("SpellClassOptions"):
            for sid, cs in q("SELECT SpellID, SpellClassSet FROM SpellClassOptions"):
                if _int(cs):
                    self.class_set[sid] = _int(cs)

        self.labels = {}
        if have("SpellLabel"):
            for sid, lab in q("SELECT SpellID, LabelID FROM SpellLabel"):
                self.labels.setdefault(sid, set()).add(lab)

        self.sla = {}              # spell -> [(acquire, skillline, classmask)]
        if have("SkillLineAbility"):
            for sp, acq, sl, cm in q("SELECT Spell, AcquireMethod, SkillLine, ClassMask "
                                     "FROM SkillLineAbility"):
                self.sla.setdefault(sp, []).append((_int(acq), sl, _int(cm)))

        self.levels = {r[0] for r in q("SELECT DISTINCT SpellID FROM SpellLevels")} \
            if have("SpellLevels") else set()

        self.classes = {}          # SpellClassSet -> class name
        self.class_by_id = {}      # ChrClasses.ID -> class name
        if have("ChrClasses"):
            cols = _columns(conn, "ChrClasses")
            if "SpellClassSet" in cols:
                for cid, nm, cs in q("SELECT ID, Name_lang, SpellClassSet FROM ChrClasses"):
                    self.class_by_id[cid] = nm
                    if _int(cs):
                        self.classes[_int(cs)] = nm

        # Name -> spells, for the variant pairing.
        self.by_name = {}
        for sid, nm in self.name.items():
            self.by_name.setdefault(nm, []).append(sid)

    def is_trainer(self, sid):
        """A normal trainer spell: AcquireMethod 0 on some skill line, and levels.

        Not proof of being live: rune abilities are AcquireMethod 0 too
        (Mutilate 399956, Hammer of the Righteous 407632). It is used only for
        the BASE side of a pair, which must additionally be untagged.
        """
        return sid in self.levels and any(a == 0 for a, _s, _c in self.sla.get(sid, ()))

    def label(self, sid):
        n = self.name.get(sid)
        return f"{sid} '{n}'" if n is not None else f"{sid} (absent from SpellName)"

    def origin_class(self, sid):
        """Class of a chain origin: SkillLineAbility.ClassMask, then SpellClassSet.

        ClassMask comes first because it names the class that learns the
        spell. SpellClassSet is the spell family, which is not always the same
        thing: Exorcist 415076 is family 5 (warlock) with ClassMask 2 (Paladin).
        Only a single-class mask counts (bit n-1 = ChrClasses.ID n).
        """
        for _a, _s, cm in self.sla.get(sid, ()):
            if cm and cm & (cm - 1) == 0:
                return self.class_by_id.get(cm.bit_length(), f"ClassMask {cm}")
        cs = self.class_set.get(sid)
        if cs:
            return self.classes.get(cs, f"SpellClassSet {cs}")
        return None


# --- tagging ----------------------------------------------------------------


class _Tags:
    def __init__(self, data):
        self.d = data
        self.rows = {}
        self.edges = []            # propagation log: (parent, child, via, outcome)

    def add(self, sid, tier, rule, reason, target_missing=False, pin=False, pair=None,
            cls=None, role=None, sibling=None, origin=None):
        """Merge one piece of evidence. Strongest tier wins unless pinned.

        A pinned row (a variant with a known base, or a spell shown to be
        shared with live content) keeps its tier; later evidence is still
        appended to the reason chain so nothing is lost from the audit.
        """
        r = self.rows.get(sid)
        if r is None:
            r = self.rows[sid] = {
                "spell_id": sid, "tier": tier, "source_rule": rule, "reasons": [],
                "target_missing": bool(target_missing), "pinned": pin,
                "pair_base": pair, "flags": set(), "origin_tier": tier,
                "class": cls, "role": role, "sibling": sibling,
                "origins": set(),
            }
        elif pin and not r["pinned"]:
            r.update(tier=tier, source_rule=rule, pinned=True, pair_base=pair,
                     sibling=sibling)
        elif not r["pinned"] and RANK[tier] > RANK[r["tier"]]:
            r.update(tier=tier, source_rule=rule)
            r["origin_tier"] = tier
        r["target_missing"] = r["target_missing"] or bool(target_missing)
        # The first origin to reach a spell names its class; later evidence
        # only fills a gap.
        if r["class"] is None:
            r["class"] = cls
        if r["role"] is None:
            r["role"] = role
        if origin is not None:
            r["origins"].add(origin)
        if reason not in r["reasons"]:
            r["reasons"].append(reason)
        return r

    def tier(self, sid):
        r = self.rows.get(sid)
        return r["tier"] if r else None

    def strong(self, sid):
        """Tagged by something other than a weak flag."""
        t = self.tier(sid)
        return t is not None and t != FLAG


def _coverage_entry(data, step):
    need = STEP_TABLES[step]
    tables = {t: data.rows.get(t, 0) for t in need}
    missing = [t for t, n in tables.items() if n == 0]
    opt = {t: data.rows.get(t, 0) for t in OPTIONAL_TABLES.get(step, ())}
    return {
        "step": step,
        "status": "not_scanned" if missing else "scanned",
        "tables": tables,
        "optional_tables": opt,
        "missing": missing,
        "optional_missing": [t for t, n in opt.items() if n == 0],
        "hits": 0,
        "note": "",
    }


def _rune_chain(data, tags, cov, stats):
    engraves = sorted(sid for sid, n in data.name.items() if n.startswith("Engrave "))
    stats["engrave_named"] = len(engraves)
    stats["effect54_rows"] = sum(1 for es in data.effects.values() for e in es
                                 if e[1] == EFFECT_ENCHANT_ITEM)
    stats["aura332_spells"] = sum(1 for es in data.effects.values()
                                  if any(e[2] == AURA_OVERRIDE for e in es))
    stats["aura332_effects"] = sum(1 for es in data.effects.values() for e in es
                                   if e[2] == AURA_OVERRIDE)
    chains, no54, wrappers_reached, a332_reached = 0, [], set(), set()
    stats["chain_rows"] = []

    for eng in engraves:
        cls = data.origin_class(eng)
        enchants = [e[3] for e in data.effects.get(eng, ()) if e[1] == EFFECT_ENCHANT_ITEM]
        if not enchants:
            no54.append(eng)
            tags.add(eng, FLAG, "rune_chain",
                     f"named '{data.name[eng]}' but has no SpellEffect Effect=54, so no "
                     f"engraving chain; name alone is not evidence", cls=cls, role="engrave")
            continue
        for ench in enchants:
            chains += 1
            head = (f"engrave {data.label(eng)} -[SpellEffect Effect=54 MiscValue_0]-> "
                    f"SpellItemEnchantment {ench}")
            corrob = LABEL_ENGRAVE in data.labels.get(eng, ())
            tags.add(eng, RUNE, "rune_chain", head + (
                f"; SpellLabel {LABEL_ENGRAVE} corroborates" if corrob else
                f"; no SpellLabel {LABEL_ENGRAVE}"), cls=cls, role="engrave")
            if corrob:
                tags.rows[eng]["flags"].add(f"label_{LABEL_ENGRAVE}")
            slots = data.enchant.get(ench)
            if slots is None:
                tags.rows[eng]["reasons"].append(
                    f"SpellItemEnchantment {ench} absent: chain stops at the enchant")
                continue
            for slot, eff, wrapper in slots:
                if eff != ENCHANT_EQUIP_SPELL or not wrapper:
                    continue
                wrappers_reached.add(wrapper)
                w_missing = wrapper not in data.name
                w_reason = f"{head} -[Effect_{slot}=3 EffectArg_{slot}]-> wrapper {data.label(wrapper)}"
                tags.add(wrapper, RUNE, "rune_chain", w_reason, target_missing=w_missing,
                         cls=cls, role="wrapper", origin=eng)
                overrides = [e for e in data.effects.get(wrapper, ()) if e[2] == AURA_OVERRIDE]
                if overrides:
                    a332_reached.add(wrapper)
                for idx, _eff, _aura, base, target, _trig in overrides:
                    if not target:
                        continue
                    t_reason = (f"{w_reason} -[SpellEffect[{idx}] EffectAura=332 "
                                f"EffectBasePointsF]-> ability {data.label(target)}"
                                f" (replaces {data.label(base)})")
                    tags.add(target, RUNE, "rune_chain", t_reason,
                             target_missing=target not in data.name,
                             cls=cls, role="ability", origin=eng)
                    stats["chain_rows"].append((eng, ench, wrapper, target, base))

    stats["chains"] = chains
    stats["engrave_no_effect54"] = no54
    stats["wrappers_reached"] = len(wrappers_reached)
    stats["wrappers_with_332"] = len(a332_reached)
    cov["hits"] = sum(1 for r in tags.rows.values() if r["source_rule"] == "rune_chain"
                      and r["tier"] == RUNE)
    cov["note"] = (f"{chains} chain(s) from {len(engraves)} 'Engrave %' spell(s); "
                   f"{len(no54)} named but without Effect=54")


def _book_set(data, tags, cov, stats):
    q = data.conn.execute
    sparse = {r[0] for r in q("SELECT ID FROM ItemSparse")}
    item_class = {i: (_int(c), _int(s)) for i, c, s in
                  q("SELECT ID, ClassID, SubclassID FROM Item")}
    learn = {}                     # item -> {spell}
    for item, spell in q(
            "SELECT DISTINCT x.ItemID, e.SpellID FROM ItemEffect e "
            "JOIN ItemXItemEffect x ON x.ItemEffectID = e.ID "
            f"WHERE e.TriggerType = {TRIGGER_LEARN}"):
        learn.setdefault(item, set()).add(spell)

    stats["learn_items"] = len(learn)
    no_sparse = {i: s for i, s in learn.items() if i not in sparse}
    stats["learn_items_no_sparse"] = len(no_sparse)
    excluded, candidates = [], 0

    for item in sorted(no_sparse):
        spells = sorted(no_sparse[item])
        classed = [s for s in spells if data.class_set.get(s)]
        cls = item_class.get(item)
        taught = ", ".join(data.label(s) + (f" {data.rank[s]}" if data.rank.get(s) else "")
                           for s in (classed or spells))
        if not classed:
            excluded.append((item, "no taught spell carries SpellClassSet > 0", taught))
            continue
        if cls and cls[0] == ITEM_CLASS_RECIPE:
            excluded.append((item, f"Item ClassID {cls[0]}/{cls[1]} (Recipe): vanilla-style "
                                   f"class book, not a SoD learn item", taught))
            continue
        for s in classed:
            candidates += 1
            tags.add(s, BOOK, "book_set",
                     f"learn item {item} (Item {cls[0]}/{cls[1] if cls else '?'}"
                     f"{'' if cls else ', no Item row'}; no live ItemSparse row) "
                     f"-[ItemXItemEffect -> ItemEffect TriggerType=6 SpellID]-> "
                     f"{data.label(s)} SpellClassSet {data.class_set[s]}",
                     target_missing=s not in data.name,
                     cls=data.origin_class(s), role="book spell", origin=item)

    stats["book_excluded"] = excluded
    stats["book_candidates"] = candidates
    cov["hits"] = candidates
    cov["note"] = (f"{len(no_sparse)} of {len(learn)} learn items lack a live ItemSparse "
                   f"row; {len(excluded)} excluded")


def _shared_owners(data, sid, tags, cache):
    """Declared references to `sid` from owners that are not tagged."""
    if sid not in cache:
        refs = []
        for table, col, owner in SHARED_REFS:
            if not data.have(table) or col not in _columns(data.conn, table):
                continue
            for (o,) in data.conn.execute(
                    f'SELECT DISTINCT "{owner}" FROM "{table}" WHERE "{col}" = ?', (sid,)):
                refs.append((f"{table}.{col}", o))
        tal = []
        if data.have("Talent"):
            tcols = _columns(data.conn, "Talent")
            for col in TALENT_REFS:
                if col in tcols:
                    for (tid,) in data.conn.execute(
                            f'SELECT ID FROM Talent WHERE "{col}" = ?', (sid,)):
                        tal.append((f"Talent.{col}", tid))
        cache[sid] = (refs, tal)
    refs, tal = cache[sid]
    return [(c, o) for c, o in refs if not tags.strong(o) and o != sid], tal


def _edges_from(data, parent):
    """(EffectIndex, kind, child, column) for every propagation edge out of `parent`.

    `trigger` is the declared EffectTriggerSpell FK. `seal_dummy_bp` is the
    one effect-typed edge allowed -- how a seal names its Judgement -- and
    only under seal_dummy_ok(). See the module docstring for its validation.
    """
    for idx, _e, aura, _m, bp, trig in data.effects.get(parent, ()):
        if trig:
            yield idx, "trigger", trig, "EffectTriggerSpell"
        if aura == AURA_DUMMY and bp and seal_dummy_ok(data, parent, bp):
            yield idx, "seal_dummy_bp", bp, "EffectAura=4 EffectBasePointsF"


def _same_family(data, source, target):
    cs = data.class_set.get(source)
    return bool(cs) and target in data.name and data.class_set.get(target) == cs


def seal_dummy_ok(data, source, target):
    """The seal_dummy_bp edge: an aura-4 base-points value naming a real Judgement.

    All three must hold:
      - the target exists and shares the source's SpellClassSet;
      - (a) the target is not a trainer spell: no SkillLineAbility row with
        AcquireMethod 0. The 19 value collisions (Blizzard 10, Charge 100, ...)
        all fail this;
      - (b) if the target has ANY SkillLineAbility row, one shares a SkillLine
        with the source. Required only when a row exists: three real
        Judgements (21183, 1311650, 1311655) have none.
    """
    if not _same_family(data, source, target):
        return False
    rows = data.sla.get(target, ())
    if any(acq == 0 for acq, _sl, _cm in rows):
        return False
    if rows:
        return bool({sl for _a, sl, _c in rows} & {sl for _a, sl, _c in data.sla.get(source, ())})
    return True


def seal_dummy_landings(data):
    """Every classed EffectAura=4 effect whose base points name a same-family spell.

    The population seal_dummy_ok() was validated against, kept so the report
    can show it: [(spell, EffectIndex, target, edge_allowed)].
    """
    out = []
    for sid, es in data.effects.items():
        for idx, _e, aura, _m, bp, _t in es:
            if aura == AURA_DUMMY and bp and _same_family(data, sid, bp):
                out.append((sid, idx, bp, seal_dummy_ok(data, sid, bp)))
    return sorted(out)


def _propagate(data, tags, cov, stats):
    cache = {}
    frontier = sorted(s for s, r in tags.rows.items() if r["tier"] != FLAG)
    hops, demoted, blocked = 0, [], {}
    stats["trigger_rows"] = sum(1 for es in data.effects.values() for e in es if e[5])
    while frontier:
        hops += 1
        nxt = []
        for parent in frontier:
            ptier = tags.tier(parent)
            if ptier == FLAG:
                continue
            for idx, kind, child, via_col in _edges_from(data, parent):
                if child == parent:
                    continue
                if tags.strong(child):
                    tags.edges.append((parent, child, idx, "already tagged", kind))
                    continue
                via = (f"{data.label(parent)} [{ptier}] -[SpellEffect[{idx}] "
                       f"{via_col}]-> {data.label(child)} (hop {hops}, {kind})")
                untagged, talents = _shared_owners(data, child, tags, cache)
                if untagged or talents:
                    # Shared with live content: the edge is blocked and the
                    # child stays UNTAGGED. Even sod_flag would claim a SoD
                    # signal on Forbearance, which has none of its own.
                    shown = (talents + untagged)[:4]
                    more = len(talents) + len(untagged) - len(shown)
                    blocked[child] = (
                        via + "; blocked: also referenced by "
                        + ", ".join(f"{c} <- {data.label(o) if not c.startswith('Talent') else o}"
                                    for c, o in shown)
                        + (f" and {more} more" if more > 0 else ""))
                    tags.edges.append((parent, child, idx, "blocked: shared", kind))
                    demoted.append(child)
                    continue
                tags.add(child, ptier, "seal_dummy_bp" if kind == "seal_dummy_bp"
                         else "propagation", via,
                         target_missing=child not in data.name,
                         cls=tags.rows[parent]["class"], role="propagated")
                tags.edges.append((parent, child, idx, f"inherits {ptier}", kind))
                nxt.append(child)
        frontier = sorted(set(nxt))

    stats["propagation_hops"] = hops
    stats["propagation_demoted"] = sorted(set(demoted))
    stats["propagation_blocked"] = blocked
    inherited = [e for e in tags.edges if e[3].startswith("inherits")]
    cov["hits"] = len(inherited)
    by_kind = {}
    for e in inherited:
        by_kind[e[4]] = by_kind.get(e[4], 0) + 1
    stats["propagation_by_kind"] = by_kind
    stats["seal_dummy_rows"] = sum(1 for es in data.effects.values() for e in es
                                   if e[2] == AURA_DUMMY and e[4])
    cov["note"] = (f"{len(tags.edges)} edge(s) examined over {hops} hop(s) "
                   f"({', '.join(f'{k} {n}' for k, n in sorted(by_kind.items()))} inherited); "
                   f"{len(set(demoted))} shared target(s) blocked, left untagged")


def _best_base(data, sod_id, bases):
    """Pick the base whose rank string matches, else the lowest ID."""
    rank = data.rank.get(sod_id, "")
    same = [b for b in bases if rank and data.rank.get(b, "") == rank]
    return min(same or bases)


def _variants(data, tags, cov, stats):
    pairs, ported = [], []
    ref_ok = bool(data.reference_ids)
    cov["optional_tables"][f"reference SpellName ({data.reference_build or 'none'})"] =         len(data.reference_ids)
    if not ref_ok:
        cov["optional_missing"].append("1.15 reference SpellName")

    def trainer_bases(sid):
        nm, cs = data.name.get(sid), data.class_set.get(sid)
        if nm is None or not cs:
            return []
        return sorted(b for b in data.by_name.get(nm, ())
                      if b != sid and data.class_set.get(b) == cs
                      and data.is_trainer(b) and not tags.strong(b))

    # (a) overrides from tagged spells: the base side is named explicitly.
    stats["override_same_name"] = 0
    for owner in sorted(s for s, r in list(tags.rows.items()) if r["tier"] != FLAG):
        for idx, _e, aura, base, repl, _t in data.effects.get(owner, ()):
            if aura != AURA_OVERRIDE or not base or not repl or repl == base:
                continue
            if data.name.get(base) is None or data.name.get(base) != data.name.get(repl):
                continue
            stats["override_same_name"] += 1
            reason = (f"{data.label(owner)} [{tags.tier(owner)}] -[SpellEffect[{idx}] "
                      f"EffectAura=332 MiscValue_0 -> EffectBasePointsF]-> overrides "
                      f"{data.label(base)} {data.rank.get(base, '')}".rstrip()
                      + f" with {data.label(repl)}")
            if data.is_trainer(base) and not tags.strong(base):
                tags.add(repl, VARIANT, "variant", reason + "; base is a trainer spell",
                         pin=True, pair=base, cls=tags.rows[owner]["class"], role="override")
                pairs.append((repl, base, "override"))
            elif not tags.strong(repl):
                tags.add(repl, FLAG, "variant",
                         reason + "; base is not a trainer spell, so the pair is ambiguous",
                         cls=tags.rows[owner]["class"], role="override")

    # (b) name pairs, for spells tagged by 1-3 or carrying a mixed label.
    label_hits = sorted(s for s, labs in data.labels.items() if labs & set(LABELS_MIXED))
    stats["label_mixed_spells"] = len(label_hits)
    candidates = sorted(set(s for s, r in tags.rows.items()
                            if r["tier"] != FLAG and not r["pinned"]) | set(label_hits))
    for sid in candidates:
        if tags.rows.get(sid, {}).get("pinned"):
            continue
        bases = trainer_bases(sid)
        if not bases:
            continue
        base = _best_base(data, sid, bases)
        tagged = tags.strong(sid)
        if tagged:
            why = f"tagged {tags.tier(sid)} by {tags.rows[sid]['source_rule']}"
        else:
            labs = sorted(data.labels.get(sid, set()) & set(LABELS_MIXED))
            why = "SpellLabel " + "/".join(map(str, labs))
        text = (f"{data.label(sid)} {data.rank.get(sid, '')}".rstrip()
                + f" ({why}) shares Name_lang and SpellClassSet {data.class_set[sid]} with "
                f"untagged trainer spell {data.label(base)} {data.rank.get(base, '')}".rstrip()
                + (f"; other base ranks: {', '.join(map(str, [b for b in bases if b != base]))}"
                   if len(bases) > 1 else ""))
        cls = tags.rows[sid]["class"] if sid in tags.rows else data.origin_class(sid)
        # Ported: a rune/book ability whose trainer sibling did not exist in
        # the SoD-era client, so Forever added it. Checked by presence in a
        # real 1.15 build, never by the sibling's ID range.
        if tagged and ref_ok and base not in data.reference_ids:
            tags.add(sid, PORTED, "variant",
                     text + f"; the sibling is absent from the SoD-era client "
                     f"{data.reference_build}, so Forever re-added the ability",
                     pin=True, sibling=base, cls=cls, role="ported")
            ported.append((sid, base))
            continue
        if tagged and ref_ok:
            text += f"; the sibling exists in the SoD-era client {data.reference_build}"
        tags.add(sid, VARIANT, "variant", text, pin=True, pair=base, cls=cls, role="name pair")
        pairs.append((sid, base, "name+" + ("tag" if tagged else "label")))

    stats["variant_pairs"] = pairs
    stats["ported_pairs"] = ported
    cov["hits"] = len(pairs) + len(ported)
    cov["note"] = (f"{stats['override_same_name']} same-name override(s) out of tagged "
                   f"spells; {len(pairs)} variant pair(s), {len(ported)} ported"
                   + ("" if ref_ok else "; PORTED CHECK NOT SCANNED: no 1.15 reference "
                      "build extracted, so every name pair stays sod_variant"))


def _labels(data, tags, cov, stats):
    counts = {lab: 0 for lab in (LABEL_ENGRAVE,) + LABELS_MIXED}
    flagged = 0
    for sid, labs in sorted(data.labels.items()):
        for lab in counts:
            if lab in labs:
                counts[lab] += 1
        mixed = sorted(labs & set(LABELS_MIXED))
        engrave = LABEL_ENGRAVE in labs
        if sid in tags.rows:
            for lab in mixed:
                tags.rows[sid]["flags"].add(f"label_{lab}")
            continue
        if not (mixed or engrave):
            continue
        which = "/".join(map(str, ([LABEL_ENGRAVE] if engrave else []) + mixed))
        tags.add(sid, FLAG, "labels",
                 f"SpellLabel {which} on {data.label(sid)}, and no chain, book or "
                 f"variant pair -- a label alone is never promoted",
                 cls=data.origin_class(sid), role="label")
        tags.rows[sid]["flags"].update(f"label_{l}" for l in labs
                                       if l in counts)
        flagged += 1
    stats["label_counts"] = counts
    stats["label_on_engrave"] = sum(1 for s, labs in data.labels.items()
                                    if LABEL_ENGRAVE in labs
                                    and data.name.get(s, "").startswith("Engrave "))
    cov["hits"] = flagged
    cov["note"] = f"{flagged} spell(s) flagged on label evidence alone"


def _trait_tree(data, tags, cov, stats):
    """Collect {spell: (tree, reason)} for spells on live talent trees.

    Marks nothing here: manual tags are added after the steps run, and they
    must be marked too. detect() applies this after the allowlist.
    """
    trees = spell_reach.live_trait_trees(data.conn)
    stats["live_trait_trees"] = trees
    stats["trait_tree_spells"] = {s: why for s, (_t, why) in
                                  spell_reach.trait_tree_spells(data.conn, trees).items()}
    cov["note"] = f"{len(trees)} live tree(s): {', '.join(map(str, sorted(trees)))}"


def _forever_trainer(data, tags, cov, stats):
    """Collect tagged spells whose trainer row is Forever's, not SoD's.

    A trainer row (AcquireMethod 0 on a skill line other than Engraving, plus
    SpellLevels) is NOT evidence on its own: SoD rune abilities carry them
    (Aspect of the Viper 415423, Shadowfiend 401977, Redirect 438040 -- all
    with the identical row in 1.15.9). A row that is absent from the SoD
    reference, or differs from it, is Forever's doing. Measured at 70170:
    Fire Nova 408341-408345 (SoD: AcquireMethod 3 on Enhancement; Forever:
    AcquireMethod 0 on Elemental Combat, and Fire Nova Totem 1535/11315 are
    gone from SpellName), Victory Rush 402927 (3 -> 0), Hammer of the
    Righteous 407632 (no SoD row). Marks nothing here; detect() applies it.
    """
    ref = data.reference_sla
    stats["forever_trainer_spells"] = {}
    if ref is None:
        cov["status"] = "not_scanned"
        cov["missing"].append("1.15 reference SkillLineAbility")
        cov["note"] = "NOT SCANNED: no SoD reference SkillLineAbility extracted"
        return
    for sid in tags.rows:
        if sid not in data.levels:
            continue
        rows = [(sl, acq) for acq, sl, _cm in data.sla.get(sid, ())
                if acq == 0 and sl != spell_reach.ENGRAVING_SKILL_LINE]
        new = [(sl, acq) for sl, acq in rows if (sl, acq) not in ref.get(sid, set())]
        if new:
            was = sorted(ref.get(sid, set()))
            stats["forever_trainer_spells"][sid] = (
                f"trainer row SkillLine {new[0][0]} AcquireMethod 0, not in SoD reference "
                f"{data.reference_build} (there: "
                + (", ".join(f"SkillLine {a} AcquireMethod {b}" for a, b in was) or "no row") + ")")
    cov["note"] = f"compared against {data.reference_build} SkillLineAbility"


def load_allowlist(path=None):
    path = path or config.SOD_ALLOWLIST
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {int(e["spell_id"]): e for e in blob.get("entries", [])}


def load_manual(path=None):
    """sod_manual.json: {spell_id: {tier, reason, source, date}}. Same shape as the allowlist."""
    return load_allowlist(path or config.SOD_MANUAL)


def load_reference():
    """(build, {spell IDs}) from the newest extracted SoD-era client, or (None, set()).

    Reads SpellName's ID column straight from the CSV: the reference build
    is not Forever, so it has no business going through the Forever build
    filter or into a wow.db.
    """
    pat = re.compile(config.SOD_REFERENCE_VERSION_PATTERN)
    best = None
    if config.OUT_DIR.is_dir():
        for d in config.OUT_DIR.iterdir():
            parts = d.name.rsplit(".", 1)
            if d.is_dir() and pat.match(d.name) and len(parts) == 2 and parts[1].isdigit():
                for sub in ("db2_hotfixed", "db2"):
                    if (d / sub / "SpellName.csv").exists():
                        if best is None or int(parts[1]) > best[0]:
                            best = (int(parts[1]), d.name, d / sub / "SpellName.csv")
                        break
    if best is None:
        return None, set()
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    with open(best[2], encoding="utf-8", errors="replace", newline="") as fh:
        rows = csv.reader(fh)
        header = next(rows)
        i = header.index("ID")
        return best[1], {int(r[i]) for r in rows if len(r) > i and r[i].isdigit()}


def load_reference_sla(build):
    """{spell: {(SkillLine, AcquireMethod)}} from the SoD reference's CSV, or None."""
    if not build:
        return None
    for sub in ("db2_hotfixed", "db2"):
        path = config.OUT_DIR / build / sub / "SkillLineAbility.csv"
        if path.exists():
            out = {}
            with open(path, encoding="utf-8", errors="replace", newline="") as fh:
                for r in csv.DictReader(fh):
                    out.setdefault(_int(r.get("Spell")), set()).add(
                        (_int(r.get("SkillLine")), _int(r.get("AcquireMethod"))))
            return out
    return None


def _apply_manual(data, tags, manual, stats):
    """Hand-audited tags for spells no structural signal reaches.

    Never overrides detection: a manual entry for a spell that is already
    tagged is reported as a conflict and the detected tag stands.
    """
    stats["manual_applied"], stats["manual_conflicts"] = [], []
    for sid, e in sorted(manual.items()):
        tier = e.get("tier")
        if tier not in TIERS:
            stats["manual_conflicts"].append((sid, f"unknown tier {tier!r}"))
            continue
        if sid in tags.rows:
            stats["manual_conflicts"].append(
                (sid, f"already tagged {tags.tier(sid)} by {tags.rows[sid]['source_rule']}; "
                      f"manual {tier} not applied"))
            continue
        tags.add(sid, tier, "manual",
                 f"manual: {e.get('reason', '')} (source: {e.get('source', '?')}, "
                 f"{e.get('date', '?')})", target_missing=sid not in data.name,
                 pin=True, cls=data.origin_class(sid), role="manual")
        stats["manual_applied"].append(sid)


def detect(conn, build=None, allowlist=None, manual=None, reference=None):
    """Run every step over one database connection.

    Read-only: nothing is written to `conn`. Returns
    {"build", "tags": {spell_id: row}, "coverage": [...], "stats": {...}}.
    Each row carries tier, source_rule, reasons (the audit chain in order),
    target_missing, pair_base, flags, live_in_forever and allowlist_reason.
    """
    data = _Data(conn, load_reference() if reference is None else reference)
    data.reference_sla = load_reference_sla(data.reference_build)
    tags = _Tags(data)
    stats, coverage = {}, []
    runners = {"rune_chain": _rune_chain, "book_set": _book_set,
               "propagation": _propagate, "variant": _variants, "labels": _labels,
               "trait_tree": _trait_tree, "forever_trainer": _forever_trainer}
    for step in STEPS:
        cov = _coverage_entry(data, step)
        coverage.append(cov)
        if cov["status"] == "not_scanned":
            cov["note"] = ("NOT SCANNED: " + ", ".join(cov["missing"])
                           + " absent or empty in this build")
            continue
        runners[step](data, tags, cov, stats)

    _apply_manual(data, tags, load_manual() if manual is None else manual, stats)

    allow = load_allowlist() if allowlist is None else allowlist
    stats["allowlist_untagged"] = []
    for sid, entry in allow.items():
        r = tags.rows.get(sid)
        if r is None:
            stats["allowlist_untagged"].append(sid)
            continue
        r["live_in_forever"] = True
        r["live_source"] = "allowlist"
        r["allowlist_reason"] = entry.get("reason", "")

    # Live talent trees: structural, after the allowlist so a hand entry keeps
    # its own reason. The tier stays; the spell is SoD-derived but not cut.
    # The same for trainer rows Forever added or changed since SoD.
    for step, source, verb in (("trait_tree", "trait_tree", "granted by "),
                               ("forever_trainer", "forever_trainer", "")):
        stats[f"{step}_live"], newly = [], 0
        for sid, why in sorted(stats.get(f"{step}_spells", {}).items()):
            r = tags.rows.get(sid)
            if r is None:
                continue
            r["reasons"].append(f"live: {verb}{why}")
            if not r.get("live_in_forever"):
                r["live_in_forever"] = True
                r["live_source"] = source
                newly += 1
            stats[f"{step}_live"].append(sid)
        for c in coverage:
            if c["step"] == step and c["status"] == "scanned":
                c["hits"] = newly
                c["note"] += (f"; {len(stats[f'{step}_live'])} tagged spell(s) qualify, "
                              f"{newly} newly marked live_in_forever")

    for r in tags.rows.values():
        r["origins"] = sorted(r["origins"])
        r.setdefault("live_in_forever", False)
        r.setdefault("live_source", None)
        r.setdefault("allowlist_reason", None)
        if r["class"] is None:
            r["class"] = data.origin_class(r["spell_id"])
        r["name"] = data.name.get(r["spell_id"])
        r["flags"] = sorted(r["flags"])

    return {"build": build, "tags": tags.rows, "edges": tags.edges,
            "coverage": coverage, "stats": stats, "data": data}


def tag_for(result, spell_id):
    """The enrich-style view of one spell: {tier, reasons, flags, live_in_forever, ...}.

    `tier` None means untagged -- but only if every step was scanned, which is
    what `not_scanned` is for. An untagged spell in a build where the rune
    chain could not run is unmeasured, not clean.
    """
    r = result["tags"].get(int(spell_id))
    not_scanned = [c["step"] for c in result["coverage"] if c["status"] != "scanned"]
    if r is None:
        return {"spell_id": int(spell_id), "tier": None, "reasons": [], "flags": [],
                "live_in_forever": None, "not_scanned": not_scanned}
    return {
        "spell_id": r["spell_id"], "name": r["name"], "tier": r["tier"],
        "reasons": list(r["reasons"]), "flags": list(r["flags"]),
        "live_in_forever": r["live_in_forever"], "live_source": r["live_source"],
        "source_rule": r["source_rule"],
        "target_missing": r["target_missing"], "pair_base": r["pair_base"],
        "forever_sibling": r["sibling"], "role": r["role"],
        "allowlist_reason": r["allowlist_reason"], "class": r["class"],
        "not_scanned": not_scanned,
    }


def materialize(conn, result, build):
    """Write sod_tags and sod_tags_coverage into `conn` (a writable database)."""
    conn.execute("DROP TABLE IF EXISTS sod_tags")
    conn.execute("""CREATE TABLE sod_tags (
        spell_id         INTEGER PRIMARY KEY,
        tier             TEXT NOT NULL,
        reason_chain     TEXT NOT NULL,   -- JSON array, evidence in the order found
        source_rule      TEXT NOT NULL,   -- step that set the tier
        target_missing   INTEGER NOT NULL,-- 1: the ID has no SpellName row
        live_in_forever  INTEGER NOT NULL,-- 1: live in Forever; never part of a cut view
        allowlist_reason TEXT,
        live_source      TEXT,            -- allowlist / trait_tree; NULL when not live
        pair_base_id     INTEGER,         -- sod_variant: the untagged base spell
        forever_sibling_id INTEGER,       -- sod_ported: the untagged Forever trainer spell
        class_name       TEXT,            -- class of the chain origin
        role             TEXT,            -- engrave / wrapper / ability / book spell / ...
        flags            TEXT,            -- JSON array
        build            TEXT NOT NULL)""")
    conn.executemany(
        "INSERT INTO sod_tags VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["spell_id"], r["tier"], json.dumps(r["reasons"], ensure_ascii=False),
          r["source_rule"], int(r["target_missing"]), int(r["live_in_forever"]),
          r["allowlist_reason"], r["live_source"], r["pair_base"], r["sibling"], r["class"], r["role"],
          json.dumps(r["flags"]), build)
         for r in sorted(result["tags"].values(), key=lambda r: r["spell_id"])])
    conn.execute("CREATE INDEX ix_sod_tags_tier ON sod_tags (tier)")

    conn.execute("DROP TABLE IF EXISTS sod_tags_coverage")
    conn.execute("""CREATE TABLE sod_tags_coverage (
        step TEXT PRIMARY KEY, status TEXT NOT NULL, source_tables TEXT NOT NULL,
        missing TEXT NOT NULL, hits INTEGER NOT NULL, note TEXT, build TEXT NOT NULL)""")
    conn.executemany(
        "INSERT INTO sod_tags_coverage VALUES (?,?,?,?,?,?,?)",
        [(c["step"], c["status"], json.dumps({**c["tables"], **c["optional_tables"]}),
          json.dumps(c["missing"] + c["optional_missing"]), c["hits"], c["note"], build)
         for c in result["coverage"]])


def detect_from_csv(out_dir, build=None, allowlist=None):
    """Detection over the CSVs alone, for callers without a wow.db (enrich.py)."""
    import build_db  # lazy: build_db imports this module

    needed = set()
    for ts in list(STEP_TABLES.values()) + list(OPTIONAL_TABLES.values()):
        needed.update(ts)
    needed.add("ChrClasses")
    conn = sqlite3.connect(":memory:")
    for t in sorted(needed):
        path = out_dir / "db2_hotfixed" / f"{t}.csv"
        if path.exists():
            rows, _n = build_db.load_one(conn, t, path)
            if rows == 0:
                conn.execute(f'DROP TABLE IF EXISTS "{t}"')
    return detect(conn, build, allowlist)


# --- report -----------------------------------------------------------------


def render_report(result):
    d, tags, st = result["data"], result["tags"], result["stats"]
    rows = list(tags.values())
    L = [f"# SoD tags — {result['build']}", "",
         f"Generated {datetime.now(timezone.utc).replace(microsecond=0).isoformat()}. "
         "A snapshot-wide annotation, not a contamination finding. "
         "`sod_rune` and `sod_book_candidate` form the cut view; "
         "`sod_ported`, `sod_variant` and `sod_flag` never do. Class is the class "
         "of the chain origin.", ""]

    L += ["## Coverage", "", "| Step | Status | Hits | Source tables | Note |",
          "|---|---|--:|---|---|"]
    for c in result["coverage"]:
        status = "scanned" if c["status"] == "scanned" else "**NOT SCANNED**"
        tbls = ", ".join(f"{t} {n:,}" for t, n in {**c["tables"], **c["optional_tables"]}.items())
        extra = (" Optional tables missing: " + ", ".join(c["optional_missing"]) + "."
                 if c["optional_missing"] else "")
        L.append(f"| {c['step']} | {status} | {c['hits'] if c['status'] == 'scanned' else '—'} "
                 f"| {tbls} | {c['note']}{extra} |")
    L.append("")

    L += ["## Tiers", "", "| Tier | Spells | target_missing | live_in_forever |",
          "|---|--:|--:|--:|"]
    for t in TIERS:
        rs = [r for r in rows if r["tier"] == t]
        L.append(f"| `{t}` | {len(rs)} | {sum(r['target_missing'] for r in rs)} | "
                 f"{sum(r['live_in_forever'] for r in rs)} |")
    L.append(f"| **total** | {len(rows)} | | |")
    L.append("")

    classes = sorted({r["class"] or "(no class)" for r in rows})
    L += ["## By class", "", "| Class | " + " | ".join(f"`{t}`" for t in TIERS) + " |",
          "|---|" + "--:|" * len(TIERS)]
    for cl in classes:
        L.append(f"| {cl} | " + " | ".join(
            str(sum(1 for r in rows if (r["class"] or "(no class)") == cl and r["tier"] == t))
            for t in TIERS) + " |")
    L.append("")

    lc = st.get("label_counts", {})
    total_spells = len(d.name)
    L += ["## Signals and base rates", "",
          "Rule 6: each hit count next to how often the pattern occurs at all.", "",
          "| Signal | Hits | Base rate |", "|---|--:|---|"]
    if "chains" in st:
        L += [f"| 'Engrave %' + Effect=54 chain | {st['chains']} | {st['engrave_named']} "
              f"'Engrave %' names of {total_spells:,} SpellName rows; {st['effect54_rows']} "
              f"Effect=54 rows in SpellEffect |",
              f"| wrappers reached with EffectAura=332 | {st['wrappers_with_332']} of "
              f"{st['wrappers_reached']} wrappers | {st['aura332_spells']} spells "
              f"({st['aura332_effects']} effects) carry aura 332 in all |"]
    if "learn_items" in st:
        L.append(f"| learn item, no ItemSparse, classed spell | {st['book_candidates']} "
                 f"spell(s) | {st['learn_items_no_sparse']} of {st['learn_items']:,} "
                 f"TriggerType=6 items lack a live ItemSparse row |")
    if "trigger_rows" in st:
        bk = st.get("propagation_by_kind", {})
        L.append(f"| EffectTriggerSpell out of a tagged spell | {bk.get('trigger', 0)} inherited, "
                 f"{len(st['propagation_demoted'])} blocked | {st['trigger_rows']:,} "
                 f"SpellEffect rows carry a trigger spell |")
        sd = seal_dummy_landings(d)
        L.append(f"| seal_dummy_bp (aura 4, same family, not trainer, SkillLine if any) | "
                 f"{bk.get('seal_dummy_bp', 0)} inherited | {sum(1 for x in sd if x[3])} of "
                 f"{len(sd)} classed aura-4 same-family landings pass the edge; the rest are "
                 f"value collisions (Blizzard 10, Charge 100); {st['seal_dummy_rows']:,} "
                 f"aura-4 effects carry a nonzero EffectBasePointsF |")
    if "variant_pairs" in st:
        L.append(f"| variant pairs | {len(st['variant_pairs'])} | "
                 f"{st['override_same_name']} same-name aura-332 overrides out of tagged "
                 f"spells; {st['label_mixed_spells']} spells carry 3096/3100 |")
    if lc:
        L.append(f"| SpellLabel {LABEL_ENGRAVE} | {st['label_on_engrave']} on engrave spells "
                 f"| {lc[LABEL_ENGRAVE]} spells in all |")
        for lab in LABELS_MIXED:
            L.append(f"| SpellLabel {lab} | flag only | {lc[lab]} spells in all |")
    L.append("")

    missing = sorted((r for r in rows if r["target_missing"]), key=lambda r: r["spell_id"])
    L += [f"## target_missing ({len(missing)})", "",
          "IDs the chain points at that have no SpellName row. Tagged anyway.", ""]
    for r in missing:
        L.append(f"- `{r['spell_id']}` `{r['tier']}` — {r['reasons'][0]}")
    L.append("")

    if st.get("engrave_no_effect54"):
        L += ["## 'Engrave %' spells with no chain", ""]
        for s in st["engrave_no_effect54"]:
            L.append(f"- {d.label(s)} → `sod_flag`")
        L.append("")

    ported = st.get("ported_pairs", [])
    L += [f"## sod_ported ({len(ported)})", "",
          "SoD rune/book abilities whose same-name trainer sibling is absent from "
          f"the SoD-era client {d.reference_build or '(none extracted)'}: Forever "
          "re-added the ability. Not cut.", ""]
    by_cls = {}
    for sod, sib in ported:
        by_cls.setdefault(tags[sod]["class"] or "(no class)", []).append((sod, sib))
    for cl in sorted(by_cls):
        L += [f"### {cl} ({len(by_cls[cl])})", "", "| SoD ID | Name | Tagged by | Forever sibling |",
              "|--:|---|---|--:|"]
        for sod, sib in sorted(by_cls[cl], key=lambda p: (d.name.get(p[0], ""), p[0])):
            L.append(f"| {sod} | {d.name.get(sod)} | "
                     f"{'book' if any('learn item' in x for x in tags[sod]['reasons']) else 'rune chain'} "
                     f"| {sib} |")
        L.append("")

    # Paladin rune abilities with no Forever sibling: per engrave, the chain's
    # wrapper and ability nodes, when none of them is sod_ported.
    pal = sorted((r for r in rows if r["role"] == "engrave" and r["class"] == "Paladin"
                  and r["tier"] == RUNE), key=lambda r: r["name"] or "")
    lines = []
    for eng in pal:
        nodes = [r for r in rows if eng["spell_id"] in r["origins"]]
        if any(n["tier"] == PORTED for n in nodes):
            continue
        shown = ", ".join(f"{n['spell_id']} {n['name'] or '(absent)'} `{n['tier']}`"
                          for n in sorted(nodes, key=lambda n: n["spell_id"]))
        lines.append(f"| {eng['spell_id']} | {eng['name']} | {shown} |")
    L += [f"## Paladin rune abilities with no Forever sibling ({len(lines)} of {len(pal)} engraves)",
          "", "| Engrave | Name | Chain nodes |", "|--:|---|---|", *lines, ""]

    man = st.get("manual_applied", [])
    L += [f"## Manual tags ({len(man)})", ""]
    for sid in man:
        r = tags[sid]
        L.append(f"- `{sid}` {r['name']} → `{r['tier']}` — {r['reasons'][0]}")
    for sid, why in st.get("manual_conflicts", []):
        L.append(f"- `{sid}` **not applied**: {why}")
    L.append("")

    pairs = st.get("variant_pairs", [])
    L += [f"## sod_variant pairs ({len(pairs)})", "", "| SoD ID | Name | Base ID | Base rank | How |",
          "|--:|---|--:|---|---|"]
    for sod, base, how in sorted(pairs, key=lambda p: (d.name.get(p[0], ""), p[0])):
        L.append(f"| {sod} | {d.name.get(sod)} {d.rank.get(sod, '')} | {base} | "
                 f"{d.rank.get(base, '')} | {how} |")
    L.append("")

    for source, title, blurb in (
            ("trait_tree", "Live talent-tree spells",
             "Tagged, and granted by a live Forever talent tree."),
            ("forever_trainer", "Forever trainer spells",
             "Tagged, and carrying a trainer row the SoD reference does not have: "
             "Forever made the ability baseline.")):
        hit = [r for r in rows if r["live_source"] == source]
        L += [f"## {title} ({len(hit)})", "",
              blurb + " `live_in_forever` is set structurally. The tier is kept: "
              "SoD-derived, not cut.", ""]
        for r in sorted(hit, key=lambda r: (r["class"] or "", r["name"] or "", r["spell_id"])):
            why = next((x for x in r["reasons"] if x.startswith("live: ")), "")
            L.append(f"- `{r['spell_id']}` {r['name']} ({r['class'] or 'no class'}) — "
                     f"`{r['tier']}`; {why[6:]}")
        L.append("")

    allow = [r for r in rows if r["live_source"] == "allowlist"]
    L += [f"## Allowlist ({len(allow)})", ""]
    for r in allow:
        L.append(f"- `{r['spell_id']}` {r['name']} — detected `{r['tier']}`; "
                 f"{r['allowlist_reason']}")
    for s in st.get("allowlist_untagged", []):
        L.append(f"- `{s}` {d.name.get(s)} — **allowlisted but not tagged by any step**")
    L.append("")

    blk = st.get("propagation_blocked", {})
    if blk:
        L += ["## Propagation blocked (shared with live content, left untagged)", ""]
        for s in sorted(blk):
            L.append(f"- {d.label(s)} — {blk[s]}")
        L.append("")

    exc = st.get("book_excluded", [])
    L += [f"## Excluded learn items ({len(exc)})", "",
          "Learn items with no live ItemSparse row that were NOT tagged, and why.", ""]
    by_reason = {}
    for item, why, taught in exc:
        by_reason.setdefault(why.split(":")[0], []).append((item, why, taught))
    for key in sorted(by_reason, key=lambda k: -len(by_reason[k])):
        L += [f"### {key} ({len(by_reason[key])})", "", "| Item | Reason | Teaches |",
              "|--:|---|---|"]
        for item, why, taught in by_reason[key]:
            L.append(f"| {item} | {why} | {taught} |")
        L.append("")

    L += ["## Every tag", "", "| Spell | Name | Tier | Rule | Class | Flags | Chain |",
          "|--:|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (RANK[r["tier"]] * -1, r["spell_id"])):
        L.append(f"| {r['spell_id']} | {r['name'] or '—'} | `{r['tier']}` | {r['source_rule']} | "
                 f"{r['class'] or ''} | {' '.join(r['flags'])} | "
                 + " ‖ ".join(x.replace("|", "\\|") for x in r["reasons"]) + " |")
    L.append("")
    return "\n".join(L)


def _newest_db():
    best = None
    if config.OUT_DIR.is_dir():
        for d in config.OUT_DIR.iterdir():
            parts = d.name.rsplit(".", 1)
            if (d.is_dir() and len(parts) == 2 and parts[1].isdigit()
                    and config.is_forever_build(d.name, int(parts[1]), warn=False)
                    and (d / "wow.db").exists()):
                if best is None or int(parts[1]) > best[0]:
                    best = (int(parts[1]), d.name)
    return best[1] if best else None


def open_ro(build):
    path = config.build_out_dir(build) / "wow.db"
    uri = "file:" + str(path).replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", help="defaults to the newest build with a wow.db")
    ap.add_argument("--spell", type=int, help="print one spell's tag as JSON")
    ap.add_argument("--out", help="report path; default reports/sod_tags_<build>.md")
    args = ap.parse_args(argv)

    build = args.build or _newest_db()
    if not build:
        print(f"no wow.db under {config.OUT_DIR}; run build_db.py first", file=sys.stderr)
        return 2
    result = detect(open_ro(build), build)

    if args.spell:
        print(json.dumps(tag_for(result, args.spell), indent=2, ensure_ascii=False))
        return 0

    out = args.out or (config.REPORTS_DIR / f"sod_tags_{build}.md")
    config.REPORTS_DIR.mkdir(exist_ok=True)
    from pathlib import Path
    Path(out).write_text(render_report(result), encoding="utf-8")
    counts = {t: sum(1 for r in result["tags"].values() if r["tier"] == t) for t in TIERS}
    print(f"build {build}: " + ", ".join(f"{t} {n}" for t, n in counts.items()))
    for c in result["coverage"]:
        print(f"  {c['step']:<12} {c['status']:<12} {c['note']}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
