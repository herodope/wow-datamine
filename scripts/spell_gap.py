#!/usr/bin/env python3
"""Spell power needed for WoW: Forever to match Classic Era damage, per spell.

Every number comes from a client table in one of the two extracted builds.
Nothing is filled in from memory or the web; a value that cannot be resolved
from the tables is written as NULL with a reason, never guessed.

    python scripts/spell_gap.py
    python scripts/spell_gap.py --forever 1.60.1.69913 --classic 1.15.9.69722
    python scripts/spell_gap.py --no-png

THE TWO BUILDS DO NOT EXPRESS DAMAGE THE SAME WAY, and that is the single fact
this script exists to handle.

    Forever 1.60.x   EffectBasePointsF (float) = the midpoint
                     Variance (float)          = (max - min) / midpoint
                     EffectBasePoints / EffectDieSides: COLUMNS DO NOT EXIST

    Classic 1.15.x   EffectBasePoints + EffectDieSides = a roll of
                     [bp+1 .. bp+die], so midpoint = bp + (die+1)/2
                     EffectBasePointsF: 46 of 40,250 rows nonzero -> dead
                     Variance:           0 of 40,250 rows nonzero -> dead

Reading EffectBasePointsF on the Classic build returns 0.0 for essentially
every spell, with no error -- which is why the base rates above are measured
rather than assumed (SKILL.md rule 6). The two encodings are reconciled by
`midpoint()` and nowhere else.

Effects are classified by declared enum values decoded through the DBD meta
mappings, not by spell name:

    Effect 2   SCHOOL_DAMAGE, period 0        -> direct damage
    Effect 6   APPLY_AURA           | aura 3  PERIODIC_DAMAGE  -> DoT
    Effect 27  PERSISTENT_AREA_AURA | aura 53 PERIODIC_LEECH   -> DoT
    Effect 6   APPLY_AURA, aura 23 PERIODIC_TRIGGER_SPELL      -> ticks of
               EffectTriggerSpell, a DECLARED relation to Spell::ID
    Effect 6   APPLY_AURA, aura 226 PERIODIC_DUMMY, alongside
               Effect 179 CREATE_AREATRIGGER                   -> see below

Everything else (MOD_DECREASE_SPEED, SCRIPT_EFFECT, INTERRUPT_CAST, DUMMY,
MECHANIC_IMMUNITY) contributes nothing and is listed in `ignored` so that a
dropped effect is visible rather than silent.

THE AREATRIGGER CHAIN IS NOT RESOLVABLE BY FOREIGN KEY IN THIS BUILD.
Forever's Blizzard, Flamestrike and Rain of Fire carry Effect 179
CREATE_AREATRIGGER whose EffectMiscValue_0 is 41260 / 41271 / 41456.
`AreaTriggerCreateProperties` ships 14 rows in 1.60.1.69913 and contains none
of those IDs, and `/dbc/relations/` declares ZERO inbound references to either
`AreaTrigger::ID` or `AreaTriggerCreateProperties::ID` -- so there is no
declared path from the effect to the damage spell, and matching the raw value
across columns is exactly what SKILL.md rule 1 forbids.

What is left is a name+rank+structure match, and it is reported as such in the
`source` column rather than dressed up as a key lookup. Name+rank alone is NOT
unique (Blizzard Rank 6 matches three spells), so the match also requires the
candidate to carry a direct-damage effect, which narrows each to exactly one:

    Blizzard Rank 6      10187 parent | 27618 Effect 27  | 1279949 Effect 2
    Flamestrike Rank 6   10216 parent |                  | 1279990 Effect 2
    Rain of Fire Rank 4  11678 parent | 460700 Effect 27 | 1282385 Effect 2

The Effect-27 twins are vestigial rows still carrying the UNCHANGED Classic
values (27618 bpF 149 = Classic's 148+1; 460700 bpF 226 = Classic's 225+1),
while the Effect-2 spells carry retuned ones. The parent spells have no
Effect 27 of their own, so the Effect-27 twins cannot be what they tick.
If the narrowing ever returns zero or more than one candidate the spell is
emitted as NULL with the reason, not resolved by picking one.
"""

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

import config

FOREVER_DEFAULT = "1.60.1.69913"
CLASSIC_DEFAULT = "1.15.9.69722"

GCD_MS = 1500          # cast times floor here; an instant is a GCD, not 0s
SUSPECT_RATIO = 0.35   # forever_dmg / classic_dmg below this is not a tuning change

# Effect / EffectAura enum values, decoded via enrich.decode() against the DBD
# meta mappings -- see the module docstring. Named here so the classifier reads
# as intent rather than as magic numbers.
EFF_SCHOOL_DAMAGE = 2
EFF_DUMMY = 3
EFF_APPLY_AURA = 6
EFF_PERSISTENT_AREA_AURA = 27
EFF_CREATE_AREATRIGGER = 179

AURA_PERIODIC_DAMAGE = 3
AURA_PERIODIC_TRIGGER_SPELL = 23
AURA_PERIODIC_LEECH = 53
AURA_PERIODIC_DUMMY = 226

PERIODIC_DAMAGE_AURAS = {AURA_PERIODIC_DAMAGE, AURA_PERIODIC_LEECH}

CLASSES = ["Mage", "Priest", "Warlock", "Druid", "Shaman"]

# Level 60, highest trainer rank, no AQ book ranks. The same spell ID is used
# in both builds -- these are not re-derived per build, so a rank that moved
# would show up as a SpellLevels mismatch in the notes rather than silently
# comparing two different spells.
SPELLS = [
    ("Mage", "Fireball", 10151),
    ("Mage", "Frostbolt", 10181),
    ("Mage", "Fire Blast", 10199),
    ("Mage", "Scorch", 10207),
    ("Mage", "Pyroblast", 18809),
    ("Mage", "Blast Wave", 13021),
    ("Mage", "Flamestrike", 10216),
    ("Mage", "Arcane Missiles", 10212),
    ("Mage", "Arcane Explosion", 10202),
    ("Mage", "Blizzard", 10187),
    ("Mage", "Cone of Cold", 10161),
    ("Priest", "SW:Pain", 10894),
    ("Priest", "Mind Blast", 10947),
    ("Priest", "Mind Flay", 18807),
    ("Priest", "Devouring Plague", 19280),
    ("Warlock", "Shadow Bolt", 11661),
    ("Warlock", "Immolate", 11668),
    ("Warlock", "Corruption", 11672),
    ("Warlock", "Curse of Agony", 11713),
    ("Warlock", "Drain Life", 11700),
    ("Warlock", "Siphon Life", 18881),
    ("Warlock", "Searing Pain", 17923),
    ("Warlock", "Soul Fire", 17924),
    ("Warlock", "Rain of Fire", 11678),
    ("Warlock", "Hellfire", 11684),
    ("Druid", "Wrath", 9912),
    ("Druid", "Moonfire", 9835),
    ("Druid", "Starfire", 9876),
    ("Shaman", "Lightning Bolt", 15208),
    ("Shaman", "Chain Lightning", 10605),
    ("Shaman", "Earth Shock", 10414),
    ("Shaman", "Flame Shock", 29228),
    ("Shaman", "Frost Shock", 10473),
]

CLASS_COLORS = {"Mage": "#3fc7eb", "Priest": "#d0d5de", "Warlock": "#8788ee",
                "Druid": "#ff7c0a", "Shaman": "#0070dd"}


def log(msg=""):
    print(msg, file=sys.stderr, flush=True)


# --- the two builds ---------------------------------------------------------


class Build:
    """One extracted build's wow.db, plus which damage encoding it uses."""

    def __init__(self, version):
        self.version = version
        path = config.build_out_dir(version) / "wow.db"
        if not path.exists():
            log(f"no {path}")
            log(f"  ingest it first:  python scripts/add_build.py {version} ...")
            raise SystemExit(2)
        self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cols = {r[1] for r in self.conn.execute('PRAGMA table_info("SpellEffect")')}
        # Presence of EffectDieSides IS the encoding test. Sniffing on the
        # version string would break the moment either line renumbers.
        self.rolled = "EffectDieSides" in cols and "EffectBasePoints" in cols
        self.encoding = ("EffectBasePoints + (EffectDieSides+1)/2" if self.rolled
                         else "EffectBasePointsF")

    def q(self, sql, args=()):
        return self.conn.execute(sql, args).fetchall()

    def effects(self, spell_id):
        dmg = ("EffectBasePoints, EffectDieSides" if self.rolled
               else "EffectBasePointsF, NULL")
        return self.q(f"""
            SELECT EffectIndex, Effect, EffectAura, {dmg}, EffectBonusCoefficient,
                   EffectAuraPeriod, EffectRealPointsPerLevel, EffectTriggerSpell,
                   EffectMiscValue_0
            FROM SpellEffect WHERE SpellID=? ORDER BY EffectIndex""", (spell_id,))

    def misc(self, spell_id):
        """(cast_ms, duration_ms) via the DECLARED relations
        SpellMisc::CastingTimeIndex -> SpellCastTimes::ID and
        SpellMisc::DurationIndex -> SpellDuration::ID."""
        r = self.q("""
            SELECT ct.Base, d.Duration
            FROM SpellMisc m
            LEFT JOIN SpellCastTimes ct ON ct.ID = m.CastingTimeIndex
            LEFT JOIN SpellDuration  d  ON d.ID  = m.DurationIndex
            WHERE m.SpellID=?""", (spell_id,))
        return (r[0][0], r[0][1]) if r else (None, None)

    def name_rank(self, spell_id):
        r = self.q("""SELECT n.Name_lang, s.NameSubtext_lang
                      FROM SpellName n LEFT JOIN Spell s ON s.ID=n.ID
                      WHERE n.ID=?""", (spell_id,))
        return (r[0][0], r[0][1]) if r else (None, None)

    def midpoint(self, base, die):
        """The expected roll. Classic rolls [bp+1 .. bp+die]; Forever ships the
        midpoint outright, so the two reconcile to the same quantity here."""
        if self.rolled:
            return (base or 0) + ((die or 0) + 1) / 2.0
        return float(base or 0)

    def variance(self, spell_id, index):
        if self.rolled:
            return None
        r = self.q("SELECT Variance FROM SpellEffect WHERE SpellID=? AND EffectIndex=?",
                   (spell_id, index))
        return r[0][0] if r else None


# --- resolving the tick spell behind an area trigger ------------------------


def resolve_areatrigger_tick(build, parent_id):
    """(spell_id, note) or (None, reason). See the module docstring -- this is a
    name+rank+structure match, NOT a foreign key, because none is declared."""
    name, rank = build.name_rank(parent_id)
    if not name:
        return None, f"spell {parent_id} has no SpellName row"
    rows = build.q("""
        SELECT n.ID FROM SpellName n LEFT JOIN Spell s ON s.ID=n.ID
        WHERE n.Name_lang=? AND IFNULL(s.NameSubtext_lang,'')=? AND n.ID<>?""",
        (name, rank or "", parent_id))
    candidates = []
    for (sid,) in rows:
        # The tick is the one that actually deals direct damage. The Effect-27
        # twins are excluded here, which is what makes the match unique.
        if any(e[1] == EFF_SCHOOL_DAMAGE and not e[6] for e in build.effects(sid)):
            candidates.append(sid)
    if len(candidates) == 1:
        return candidates[0], f"name+rank+direct-damage match on '{name}' {rank}"
    if not candidates:
        return None, f"no direct-damage spell named '{name}' {rank} besides {parent_id}"
    return None, (f"ambiguous: {len(candidates)} direct-damage spells named "
                  f"'{name}' {rank} ({','.join(str(c) for c in candidates)})")


# --- one spell in one build -------------------------------------------------


def measure(build, spell_id):
    """Total damage, total coefficient and cast time for one spell.

    Returns a dict. `parts` records every contributing effect and `ignored`
    every skipped one, so nothing disappears without being accounted for.
    """
    cast_ms, dur_ms = build.misc(spell_id)
    effs = build.effects(spell_id)
    out = {"spell_id": spell_id, "cast_ms": cast_ms, "duration_ms": dur_ms,
           "dmg": 0.0, "coef": 0.0, "parts": [], "ignored": [], "notes": [],
           "perlvl": {}, "sources": set()}
    if not effs:
        out["error"] = f"no SpellEffect rows for {spell_id}"
        return out
    out["sources"].add(f"SpellEffect[{spell_id}]")
    out["sources"].add(f"SpellMisc+SpellCastTimes[{spell_id}]")

    has_areatrigger = any(e[1] == EFF_CREATE_AREATRIGGER for e in effs)
    # Hellfire is the one spell carrying BOTH a PERIODIC_TRIGGER_SPELL and a
    # PERIODIC_DAMAGE of the same period and value -- the outgoing AoE and the
    # caster's self-damage. The tables give them identical ImplicitTarget
    # values, and ImplicitTarget has no enum mapping (rule 3), so they are not
    # distinguishable here. Counting both would double damage AND coefficient,
    # which leaves sp_needed unchanged but misreports the components, so the
    # mirrored PERIODIC_DAMAGE is dropped and the ambiguity is recorded.
    trig_periods = {e[6] for e in effs
                    if e[1] == EFF_APPLY_AURA and e[2] == AURA_PERIODIC_TRIGGER_SPELL}

    for idx, eff, aura, base, die, coef, period, perlvl, trig, misc0 in effs:
        mid = build.midpoint(base, die)
        if perlvl:
            out["perlvl"][idx] = perlvl

        def ticks():
            if not dur_ms or not period:
                return None
            return dur_ms / period

        # direct damage
        if eff == EFF_SCHOOL_DAMAGE and not period:
            out["dmg"] += mid
            out["coef"] += coef or 0.0
            out["parts"].append(f"i{idx} direct {mid:g}x1 coef {coef or 0:g}")
            continue

        # damage over time, inline on this spell
        if (eff in (EFF_APPLY_AURA, EFF_PERSISTENT_AREA_AURA)
                and aura in PERIODIC_DAMAGE_AURAS and period):
            n = ticks()
            if n is None:
                out["ignored"].append(f"i{idx} periodic aura {aura} but no duration")
                continue
            # The mirror test compares this tick against what the TRIGGERED
            # spell deals, not against the trigger effect's own base points --
            # those are 0 (Forever) / -1 (Classic) and would never match.
            if (aura == AURA_PERIODIC_DAMAGE and period in trig_periods
                    and any(abs(sum(build.midpoint(s[3], s[4])
                                    for s in build.effects(e[8])
                                    if s[1] == EFF_SCHOOL_DAMAGE and not s[6]) - mid) < 1e-6
                            for e in effs
                            if e[1] == EFF_APPLY_AURA
                            and e[2] == AURA_PERIODIC_TRIGGER_SPELL and e[8])):
                out["ignored"].append(
                    f"i{idx} PERIODIC_DAMAGE {mid:g} mirrors the PERIODIC_TRIGGER_SPELL "
                    f"tick; tables do not distinguish self- from target-damage")
                out["notes"].append("self-damage/AoE mirror not separable from tables")
                continue
            out["dmg"] += mid * n
            out["coef"] += (coef or 0.0) * n
            out["parts"].append(
                f"i{idx} dot {mid:g}x{n:g} (dur {dur_ms}/per {period}) coef {coef or 0:g}")
            out["sources"].add(f"SpellDuration[{spell_id}]")
            continue

        # ticks of a triggered spell -- DECLARED relation
        if eff == EFF_APPLY_AURA and aura == AURA_PERIODIC_TRIGGER_SPELL and trig:
            n = ticks()
            if n is None:
                out["ignored"].append(f"i{idx} PERIODIC_TRIGGER_SPELL but no duration")
                continue
            sub = [e for e in build.effects(trig)
                   if e[1] == EFF_SCHOOL_DAMAGE and not e[6]]
            if not sub:
                out["ignored"].append(f"i{idx} trigger {trig} has no direct-damage effect")
                continue
            smid = sum(build.midpoint(e[3], e[4]) for e in sub)
            scoef = sum(e[5] or 0.0 for e in sub)
            out["dmg"] += smid * n
            out["coef"] += scoef * n
            out["parts"].append(
                f"i{idx} trigger {trig} {smid:g}x{n:g} coef {scoef:g}")
            out["sources"].add(f"SpellEffect[{trig}] via EffectTriggerSpell")
            out["sources"].add(f"SpellDuration[{spell_id}]")
            continue

        # ticks delivered by an area trigger -- NOT a declared relation
        if (eff == EFF_APPLY_AURA and aura == AURA_PERIODIC_DUMMY
                and period and has_areatrigger):
            n = ticks()
            tick_id, why = resolve_areatrigger_tick(build, spell_id)
            if tick_id is None or n is None:
                out["ignored"].append(f"i{idx} PERIODIC_DUMMY unresolved: {why}")
                out["unresolved"] = why
                continue
            sub = [e for e in build.effects(tick_id)
                   if e[1] == EFF_SCHOOL_DAMAGE and not e[6]]
            smid = sum(build.midpoint(e[3], e[4]) for e in sub)
            scoef = sum(e[5] or 0.0 for e in sub)
            out["dmg"] += smid * n
            out["coef"] += scoef * n
            out["parts"].append(
                f"i{idx} areatrigger tick {tick_id} {smid:g}x{n:g} coef {scoef:g}")
            out["sources"].add(f"SpellEffect[{tick_id}] ({why}, NOT a declared FK)")
            out["notes"].append(f"areatrigger tick {tick_id} matched by name+rank, no FK")
            continue

        out["ignored"].append(
            f"i{idx} eff {eff} aura {aura} base {mid:g} coef {coef or 0:g} (no damage class)")

    out["cast_eff"] = max(cast_ms or 0, GCD_MS)
    return out


# --- the comparison ---------------------------------------------------------


def compare(fv, cl):
    rows = []
    for klass, name, sid in SPELLS:
        F, C = measure(fv, sid), measure(cl, sid)
        row = {"class": klass, "spell": name, "spell_id": sid,
               "forever_dmg": None, "forever_coef": None, "forever_cast": None,
               "classic_dmg": None, "classic_coef": None, "classic_cast": None,
               "sp_needed": None, "suspect": "", "diffs": [], "null_reason": "",
               "source": ""}

        src = (f"F:{'; '.join(sorted(F['sources']))} @{fv.version} ({fv.encoding})"
               f" || C:{'; '.join(sorted(C['sources']))} @{cl.version} ({cl.encoding})")
        row["source"] = src

        problems = [x.get("error") or x.get("unresolved") for x in (F, C)]
        problems = [p for p in problems if p]
        if problems or not F["dmg"] or not C["dmg"]:
            row["null_reason"] = "; ".join(problems) or "no damage effect resolved"
            rows.append(row)
            continue

        row["forever_dmg"] = round(F["dmg"], 2)
        row["forever_coef"] = round(F["coef"], 4)
        row["forever_cast"] = F["cast_eff"] / 1000.0
        row["classic_dmg"] = round(C["dmg"], 2)
        row["classic_coef"] = round(C["coef"], 4)
        row["classic_cast"] = C["cast_eff"] / 1000.0

        if not F["coef"]:
            row["null_reason"] = ("Forever coefficient is 0, so no amount of spell "
                                  "power closes the gap")
        else:
            scaled = C["dmg"] * (F["cast_eff"] / C["cast_eff"])
            row["sp_needed"] = round(max(0.0, (scaled - F["dmg"]) / F["coef"]), 1)

        if F["dmg"] / C["dmg"] < SUSPECT_RATIO:
            row["suspect"] = "yes"
            row["diffs"].append(
                f"forever/classic damage {F['dmg'] / C['dmg']:.3f} < {SUSPECT_RATIO}")

        # Step 3.8 -- anything the two builds disagree about, reported whether
        # or not it changes the answer.
        if abs(F["coef"] - C["coef"]) > 1e-6:
            row["diffs"].append(f"coef {F['coef']:.4g} vs {C['coef']:.4g}")
        if F["cast_eff"] != C["cast_eff"]:
            row["diffs"].append(f"cast {F['cast_eff']}ms vs {C['cast_eff']}ms")
        if (F["duration_ms"] or 0) != (C["duration_ms"] or 0):
            row["diffs"].append(f"duration {F['duration_ms']} vs {C['duration_ms']}")
        # Step 3.2 -- EffectRealPointsPerLevel is ignored in the maths on both
        # sides; a disagreement is still reported.
        for idx in sorted(set(F["perlvl"]) | set(C["perlvl"])):
            a, b = F["perlvl"].get(idx, 0.0), C["perlvl"].get(idx, 0.0)
            if abs(a - b) > 1e-6:
                row["diffs"].append(f"perlvl i{idx} {a:g} vs {b:g} (ignored)")
        for n in F["notes"] + C["notes"]:
            if n not in row["diffs"]:
                row["diffs"].append(n)

        row["_F"], row["_C"] = F, C
        rows.append(row)
    return rows


def variance_check(fv, cl, rows):
    """Step 3.1. Classic ships NO Variance (0 of 40,250 rows nonzero), so the
    check cannot be run inside that build. It can be run ACROSS the builds:
    Classic's roll gives a real range, and if Forever's Variance is
    (max-min)/midpoint the two must agree."""
    out = []
    for r in rows:
        if r["sp_needed"] is None and not r["forever_dmg"]:
            continue
        sid = r["spell_id"]
        for idx, eff, aura, base, die, coef, period, perlvl, trig, m0 in cl.effects(sid):
            if eff != EFF_SCHOOL_DAMAGE or period or not die:
                continue
            mid = cl.midpoint(base, die)
            if not mid:
                continue
            observed = (die - 1) / mid          # (max - min) / midpoint
            fvar = fv.variance(sid, idx)
            if fvar is None:
                continue
            out.append({"spell": r["spell"], "spell_id": sid, "effect_index": idx,
                        "classic_range": f"{base + 1}-{base + die}",
                        "classic_midpoint": mid,
                        "classic_range_over_midpoint": round(observed, 6),
                        "forever_variance": round(fvar, 6),
                        "agree": abs(observed - fvar) < 5e-4})
    return out


# --- outputs ----------------------------------------------------------------


def write_csv(rows, path):
    cols = ["class", "spell", "spell_id", "forever_dmg", "forever_coef", "forever_cast",
            "classic_dmg", "classic_coef", "classic_cast", "sp_needed", "suspect",
            "notes", "source"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            notes = "; ".join(r["diffs"])
            if r["null_reason"]:
                notes = (notes + "; " if notes else "") + "NULL: " + r["null_reason"]
            w.writerow([r["class"], r["spell"], r["spell_id"],
                        "NULL" if r["forever_dmg"] is None else r["forever_dmg"],
                        "NULL" if r["forever_coef"] is None else r["forever_coef"],
                        "NULL" if r["forever_cast"] is None else r["forever_cast"],
                        "NULL" if r["classic_dmg"] is None else r["classic_dmg"],
                        "NULL" if r["classic_coef"] is None else r["classic_coef"],
                        "NULL" if r["classic_cast"] is None else r["classic_cast"],
                        "NULL" if r["sp_needed"] is None else r["sp_needed"],
                        r["suspect"], notes, r["source"]])
    return path


def class_means(rows):
    """Suspect spells are excluded from the mean (step 3.7)."""
    means = {}
    for k in CLASSES:
        vals = [r["sp_needed"] for r in rows
                if r["class"] == k and r["sp_needed"] is not None and not r["suspect"]]
        means[k] = sum(vals) / len(vals) if vals else None
    return means


def write_png(rows, means, path, fv, cl):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    BG, PANEL, INK, MUTED, GRID = "#14161a", "#14161a", "#e8eaed", "#9aa0a6", "#2a2e35"

    plotted = [r for r in rows if r["sp_needed"] is not None]
    nulls = [r for r in rows if r["sp_needed"] is None]

    order, labels, values, colors, hatches, suspects = [], [], [], [], [], []
    yticks, ylabels, class_spans = [], [], []
    y = 0.0
    for k in CLASSES:
        grp = sorted([r for r in plotted if r["class"] == k],
                     key=lambda r: r["sp_needed"])
        if not grp:
            continue
        start = y
        for r in grp:
            order.append(r)
            values.append(r["sp_needed"])
            colors.append(CLASS_COLORS[k])
            hatches.append("///" if r["suspect"] else None)
            suspects.append(bool(r["suspect"]))
            lbl = r["spell"] + (" *" if r["suspect"] else "")
            yticks.append(y)
            ylabels.append(lbl)
            y += 1
        class_spans.append((k, start, y - 1))
        y += 1.2   # a gap between class groups, not a drawn separator

    height = max(7.0, 0.263 * y + 2.9)
    fig, ax = plt.subplots(figsize=(13.6, height), facecolor=BG)
    ax.set_facecolor(PANEL)

    # bars: thin, flat, one hue per class, hatched where suspect
    bars = ax.barh(yticks, values, height=0.62, color=colors, edgecolor=BG,
                   linewidth=1.2, zorder=3)
    for b, h in zip(bars, hatches):
        if h:
            b.set_hatch(h)
            b.set_edgecolor(BG)
            b.set_alpha(0.55)

    # dashed class means -- the one place a dash is meaningful (a reference
    # level, not a grid). Gridlines stay solid hairlines.
    for k, lo, hi in class_spans:
        m = means.get(k)
        if m is None:
            continue
        ax.plot([m, m], [lo - 0.6, hi + 0.6], linestyle=(0, (5, 4)), linewidth=1.6,
                color=CLASS_COLORS[k], alpha=0.95, zorder=5)
        ax.annotate(f"{k} mean {m:.0f}", xy=(m, lo - 0.72),
                    xytext=(4, 0), textcoords="offset points",
                    color=CLASS_COLORS[k], fontsize=8.5, fontweight="bold",
                    ha="left", va="center", zorder=6)

    # value at each bar end; the axis carries the scale, these carry the read
    span = max(values) if values else 1
    for yy, v, sus in zip(yticks, values, suspects):
        ax.annotate(f"{v:.0f}", xy=(v, yy), xytext=(6, 0), textcoords="offset points",
                    va="center", ha="left", fontsize=8.5,
                    color=MUTED if sus else INK, zorder=6)

    ax.set_yticks(yticks)
    ax.set_yticklabels(ylabels, fontsize=9.5, color=INK)
    ax.invert_yaxis()
    ax.set_xlim(0, span * 1.16)
    ax.set_xlabel("Spell power needed for Forever to match Classic Era damage",
                  fontsize=10, color=MUTED, labelpad=10)
    ax.tick_params(axis="x", colors=MUTED, labelsize=9)
    ax.tick_params(axis="y", length=0)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)   # solid hairline
    ax.yaxis.grid(False)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.spines["bottom"].set_linewidth(0.8)

    # class name beside its group -- identity never rests on hue alone
    for k, lo, hi in class_spans:
        ax.annotate(k.upper(), xy=(0.018, (lo + hi) / 2),
                    xycoords=("figure fraction", "data"),
                    color=CLASS_COLORS[k], fontsize=11, fontweight="bold",
                    va="center", ha="left", zorder=6,
                    annotation_clip=False)

    title = "Forever vs Classic Era: the spell power gap"
    sub = (
        f"Every value read from client tables in {fv.version} (Forever) and "
        f"{cl.version} (Classic Era). No values from memory or the web.\n"
        f"Level 60, highest trainer rank. SP = (classic_dmg x forever_cast / classic_cast "
        f"- forever_dmg) / forever_coef, floored at 0; cast times floored at the 1.5s GCD.\n"
        f"Damage is the effect midpoint: Forever SpellEffect.EffectBasePointsF, "
        f"Classic EffectBasePoints + (EffectDieSides+1)/2 - that build ships no "
        f"EffectBasePointsF. DoTs are per-tick x (SpellDuration / EffectAuraPeriod).\n"
        f"EffectRealPointsPerLevel ignored in both. Hatched bars marked * have "
        f"forever/classic damage < {SUSPECT_RATIO} and are excluded from the dashed class mean."
    )
    fig.suptitle(title, x=0.012, y=0.988, ha="left", fontsize=17,
                 fontweight="bold", color=INK)
    fig.text(0.012, 0.955, sub, ha="left", va="top", fontsize=8.4, color=MUTED,
             linespacing=1.6)

    legend = [Patch(facecolor=CLASS_COLORS[k], edgecolor=BG, label=k)
              for k, _, _ in class_spans]
    legend.append(Patch(facecolor="#7a7f87", edgecolor=BG, hatch="///", alpha=0.55,
                        label=f"suspect (< {SUSPECT_RATIO} of Classic damage)"))
    leg = fig.legend(handles=legend, loc="upper left", bbox_to_anchor=(0.012, 0.886),
                     frameon=False, fontsize=9, ncol=6, handlelength=1.5,
                     handleheight=0.9, columnspacing=1.6)
    for t in leg.get_texts():
        t.set_color(INK)

    if nulls:
        fig.text(0.012, 0.012,
                 "NULL (not plotted): " + "; ".join(
                     f"{r['spell']} - {r['null_reason']}" for r in nulls),
                 ha="left", va="bottom", fontsize=8, color="#e0a030")

    fig.subplots_adjust(left=0.215, right=0.975, top=0.846,
                        bottom=0.085 if nulls else 0.062)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160, facecolor=BG)
    plt.close(fig)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--forever", default=FOREVER_DEFAULT)
    ap.add_argument("--classic", default=CLASSIC_DEFAULT)
    ap.add_argument("--out", default=str(config.OUT_DIR))
    ap.add_argument("--no-png", action="store_true")
    args = ap.parse_args(argv)

    fv, cl = Build(args.forever), Build(args.classic)
    log(f"forever {fv.version}: damage from {fv.encoding}")
    log(f"classic {cl.version}: damage from {cl.encoding}")

    rows = compare(fv, cl)
    means = class_means(rows)
    vcheck = variance_check(fv, cl, rows)

    out = Path(args.out)
    csv_path = write_csv(rows, out / "spell_gap.csv")
    log(f"\n  {csv_path}")

    detail = {"forever": fv.version, "classic": cl.version,
              "encoding": {"forever": fv.encoding, "classic": cl.encoding},
              "gcd_ms": GCD_MS, "suspect_ratio": SUSPECT_RATIO,
              "class_means_excluding_suspect": means,
              "variance_check": vcheck,
              "spells": [{k: v for k, v in r.items() if not k.startswith("_")}
                         for r in rows],
              "components": {r["spell"]: {"forever": r["_F"]["parts"] if "_F" in r else [],
                                          "forever_ignored": r["_F"]["ignored"] if "_F" in r else [],
                                          "classic": r["_C"]["parts"] if "_C" in r else [],
                                          "classic_ignored": r["_C"]["ignored"] if "_C" in r else []}
                             for r in rows}}
    (out / "spell_gap_detail.json").write_text(
        json.dumps(detail, indent=1, default=str), encoding="utf-8")
    log(f"  {out / 'spell_gap_detail.json'}")

    if not args.no_png:
        png = write_png(rows, means, out / "spell_gap.png", fv, cl)
        log(f"  {png}")

    for k, m in means.items():
        n = sum(1 for r in rows if r["class"] == k and r["sp_needed"] is not None
                and not r["suspect"])
        log(f"    {k:8} mean {('%.1f' % m) if m is not None else 'NULL':>7}  (n={n})")
    agree = sum(1 for v in vcheck if v["agree"])
    log(f"\n  variance check: {agree}/{len(vcheck)} spells agree that Forever's "
        f"Variance == Classic's range/midpoint")
    return 0


if __name__ == "__main__":
    sys.exit(main())
