# Season of Discovery tags and player reachability

How `scripts/sod_tags.py` tags SoD-derived spells and how
`scripts/spell_reach.py` decides whether a player can get a spell. Read this
before touching either script or writing a SoD or reachability claim.

Forever was cut from a 1.15 client that carried Season of Discovery, and SoD
content is still in every snapshot. `scripts/sod_tags.py` tags it, and
`build_db.py` materialises the result into `wow.db` as `sod_tags` and
`sod_tags_coverage`. The report is `reports/sod_tags_<build>.md`. From Python,
use `enrich.Enricher.sod_tag(id)`.

> **This is deliberately NOT a contamination rule. Do not move it into
> `contamination.scan()`.** The retail filter is diff-scoped: it reads only
> rows added or removed between two builds, emits independent per-rule
> findings, never merges them into a per-row verdict, has no allowlist, and is
> not queryable from `wow.db`. SoD content is not changing between builds. It
> is simply present. So this is a **snapshot-wide annotation**: one merged tier
> per spell, a manual allowlist, and a table you can join. Each design suits
> its question. "Fixing" one to match the other breaks it.

**Tagging, never deletion.** Nothing is removed and no existing query changes
behaviour. A query excludes SoD content only if it joins `sod_tags` (see rule
9 in the wow-query skill). The bias is toward under-tagging. A false "cut" on
a live Forever spell is worse than a missed SoD spell, so ambiguous cases take
the weaker tier.

| Tier | Meaning | Cut view? | 70009 | 70170 |
|---|---|---|--:|--:|
| `sod_rune` | full engraving chain (unless `live_in_forever`) | yes | 829 | 828 |
| `sod_book_candidate` | taught by a learn item with no `ItemSparse` row. Not proof | yes | 20 | 20 |
| `sod_ported` | SoD ability whose same-name trainer sibling is absent from the SoD-era client, so Forever re-added it | no | 23 | 24 |
| `sod_variant` | SoD ID of a spell whose base version is an untagged trainer spell | no | 94 | 94 |
| `sod_flag` | weak signal only | no | 87 | 87 |

## Detection (counts measured on 1.60.1.70009)

1. **Rune chain.** Each hop is gated on its exact Effect/EffectAura value:
   - `SpellName 'Engrave %'`, via `SpellEffect` Effect=54 and `MiscValue_0`,
     leads to `SpellItemEnchantment`.
   - The enchant's `Effect_N`=3 and `EffectArg_N` lead to the wrapper spell.
   - The wrapper's `EffectAura`=332 and `EffectBasePointsF` lead to the
     granted ability.

   Only `EffectArg` is a declared FK. **270 chains from 271 names.** Cutty's
   Rune 401488 has no Effect=54 and gets `sod_flag`. 13 chain IDs have no
   `SpellName` row (`target_missing`), including Crusader Strike 407676 and
   Avenger's Shield 407669. Four of the 13 are wrappers, not abilities.
2. **Book set.** `ItemEffect` TriggerType=6 leads, via `ItemXItemEffect`, to
   an item with no live `ItemSparse` row that teaches a spell with
   SpellClassSet > 0.
   - 343 of 3,314 learn items lack `ItemSparse`, and 321 of those are
     excluded.
   - 312 teach nothing with a class set.
   - 9 are `Item` ClassID 9 (Recipe): vanilla class books teaching ranked base
     spells (Shadow Bolt 1088, Purify 1152). Every SoD book is 0/8.
3. **Propagation.** Via `EffectTriggerSpell` only (declared), one hop at a
   time; the child inherits the parent's tier.
   - A child referenced by an untagged spell through a declared cross-spell
     column, or by `Talent`, is shared with live content. The edge is blocked
     and the child stays untagged. Forbearance 25771 and Dummy Trigger 18350
     are the measured cases.
   - Never propagate through `SpellClassMask` / `EffectSpellClassMask`:
     talents hit base spells too.
   - One effect-typed edge, **`seal_dummy_bp`** (`source_rule =
     'seal_dummy_bp'`, tier inherited). It is how a seal names its Judgement:
     an `EffectAura`=4 effect whose `EffectBasePointsF` is an existing spell
     of the same SpellClassSet. Two more conditions apply:
     - (a) the target is not a trainer spell: it has no AcquireMethod-0
       `SkillLineAbility` row.
     - (b) **if** the target has any `SkillLineAbility` row, one shares a
       SkillLine with the source.

     It reaches Judgement of Martyrdom 407803 from Seal of Martyrdom 407798.
     Measured over all 54 classed aura-4 same-family landings at 70009: 35
     are a seal naming its own Judgement, and 19 are value collisions.

     | Condition | Judgements kept | Collisions through |
     |---|--:|--:|
     | same family only | 35/35 | **19/19** (Blizzard 10 tagged `sod_rune` from Enlightenment 412324) |
     | (a) alone | 35/35 | 0/19 |
     | (a) + (b) strict | 32/35 | 0/19 |
     | **(a) + (b) conditional, in use** | **35/35** | **0/19** |

     Strict (b) loses 21082 → 21183 (Crusader), 1311649 → 1311650 and
     1311656 → 1311655 (Fury). Those Judgements have no `SkillLineAbility`
     row at all, which is why (b) applies only when a row exists.
     `test_sod_tags.py` re-checks both numbers on every run.
4. **Variants.** Two routes:
   - (a) **Aura 332 is an override.** `MiscValue_0` names the spell replaced,
     `EffectBasePointsF` the replacement. So the base of a pair is named, not
     guessed. Exorcist 415076 overrides Exorcism 879…10314 with
     415068…415073. Rune targets that override a same-name trainer spell
     (Fire Blast, Renew, Raptor Strike ranks) are variants, not runes.
   - (b) Same `Name_lang` and SpellClassSet as an untagged trainer spell
     (AcquireMethod 0 plus `SpellLevels`), where the SoD side is tagged by
     steps 1–3 or carries label 3096/3100. When the SoD side was tagged and
     the sibling is **absent from the newest extracted 1.15 build**
     (`config.SOD_REFERENCE_VERSION_PATTERN`; 1.15.9.69722 here), the tier is
     `sod_ported` with `forever_sibling_id`.

   The ported rule is a presence check against a real build, not an ID range.
   It splits 23 ported (Mutilate 399956 ↔ 1241582, Penance, Riptide, …) from
   3 variants whose siblings are classic (Raptor Strike 415335 ↔ 14260).
5. **Labels.** 3071 corroborates engraves (237 of 238 uses). 3096 (430
   spells) and 3100 (214) are mixed and give `sod_flag` on their own, never
   promotion.

**Class is the chain origin's**: the engrave for a rune chain, the taught
spell for a book, the parent for an edge. It is taken from
`SkillLineAbility.ClassMask` first and SpellClassSet second. SpellClassSet is
unreliable on SoD spells. Exorcist 415076 is family 5 (warlock) with
ClassMask 2 (Paladin), and a SpellClassSet-first attribution puts 150 runes
under Mage against ~85 for every other class.

**Not signals**, each for a measured reason:
- **Spell ID ranges.** 400k–460k collides with retail DF/TWW IDs, and >1M
  holds Forever-native spells.
- **EffectAura=332 alone.** 510 spells carry it; the rune chain reaches 264
  wrappers.
- **AcquireMethod=3 alone.** Every Judgement of X has it.
- **Raw value scans, "unreferenced", or "no display data".**
- **Aura-4 base points without the `seal_dummy_bp` conditions.** On
  their own they are raw value matches (see the table under step 3).
- **An `ItemSparse` row, as evidence an item is live.** Measured at 70170:
  280 items whose own `ItemEffect` spell is SoD-tagged still have one. Among
  them are "Rune of Shadowstep" 210979, "Spell Notes: Brain Freeze" 208853,
  and the scrambled "Spell Notes: TENGI RONEERA" puzzle items.
- **A `SkillLineAbility` row, as evidence a spell is live.** 438 SLA rows
  point at tagged spells. 270 of them sit on skill line **2851
  Engraving**, which is the SoD rune system itself. AcquireMethod 3 means
  "learned via another spell" (205 untagged rows, such as Tiger's Fury and
  Judgement of Light), not "live".

**Known gap.** Hammer of Wrath 429151 has no label, trigger, override or
inbound declared reference. It is in `sod_manual.json` as `sod_flag`, because
its `BonusCoefficientFromAP` of 0.15 is SoD's; the base ranks have 0.

**Two hand-kept files**, both `{spell_id, reason, source, date}`, applied
after detection:
- `sod_allowlist.json` keeps the detected tier but sets
  `live_in_forever = 1`, which removes the spell from every cut view. It is
  seeded with Enhanced Blessings 435984, folded into baseline blessings in
  Forever.
- `sod_manual.json` adds a `tier` field. Its tags carry `source_rule =
  'manual'` and never override a detected tag; a clash is reported instead.

`live_in_forever` is also set **structurally**, after the allowlist. The
`live_source` column says which route set it: `allowlist`, `trait_tree` (a
live talent tree grants the spell) or `forever_trainer` (Forever added or
changed its trainer row relative to the SoD reference). Steps 6 and 7,
`trait_tree` and `forever_trainer`, tag nothing. They collect the evidence
that `detect()` applies. See **Player reachability** below.

**Coverage.** `sod_tags_coverage` records per step whether its source tables
exist and are non-empty in the build (rule 7 of the skill). A step that could
not run reads **NOT SCANNED** in the report and in the table, never zero.
`_build_info.sod_tags` records `ok` or the error. If detection fails,
`build_db.py` logs it loudly and still writes the rest of the database, and a
missing `sod_tags` table is then an error, not "no SoD content".
`scripts/test_sod_tags.py` pins the measured IDs.

**Tagging is not reachability.** A tag answers "is this SoD-derived?". A patch
note needs "can a player get this?", which `spell_reach` answers. Forever
talent trees are live and grant spells that carry a SoD rune tag, so those
tags are false "cut" positives unless `trait_tree` marks them live.

## Player reachability (`spell_reach`)

`scripts/spell_reach.py` answers "can a player get this spell?". `build_db.py`
runs it after `sod_tags` and materialises **`spell_reach`**: one row per
`SpellName` ID with `reachable`, `root_kind`, `root_id`, `root_spell`,
`via_spell`, `via_edge` and `depth`. An absent row means "not a spell in this
build", never "not examined". **`spell_reach_coverage`** records which root
sources could be read, and `_build_info.spell_reach` records `ok` or the
error.

- **Roots:**
  - `skill_line`: AcquireMethod 0/1/2, excluding 2851 Engraving.
  - `class_passive`: AcquireMethod 3, a single-class `ClassMask`, on a skill
    line that is not a class line (`CategoryID` <> 7). `root_id` is the skill
    line. It matches 1 spell at 70291, Rule of Rage 1322574 on 95 Defense,
    and 0 at 70170. AM 3 with a single-class mask on **any** line matches 262
    at 70291: effect copies plus Tiger's Fury, which was removed from the
    game. Do not widen it without measuring.
  - `talent_tree`: `TraitDefinition` SpellID/VisibleSpellID on a live tree.
  - `talent`: `Talent.SpellRank_0..8`. `SpellID` is 0 on all 432 rows.
  - `item`: an `ItemSparse` item that is not a SoD item.
- **Edges:** trigger, enchant and aura-332 override.
- **Live trees** (`spell_reach.live_trait_trees`, shared with `sod_tags`): a
  tree is live if `SkillLineXTraitTree` links it, or if its TraitSystem is one
  no linked tree uses. At 70170 that is the 9 class trees plus the Legacy
  trees 1187/1188/1189, which `LegacyConsts` in the client's API
  documentation names. Tree 1118 also counts: it is on the Legacy system 45
  but has no nodes. Trees 1058, 1066, 1081 and 1083 do not count.
- **SoD items** are excluded as roots when their effect spell, or anything it
  reaches, is tagged `sod_rune`/`sod_book_candidate`/`sod_flag` and not live.
  322 at 70170, including the second-hop scrambled Spell Notes.
- **Measured at 70170:** 9,070 of 31,744 spells reachable from 9,539 roots.
- **`spell_reach.load(build)`** computes reachability on the fly, read-only,
  for a database built before the table existed. Use it while `mcp_server.py`
  holds that build's `wow.db` open. It also applies the talent-tree rescue to
  that database's older `sod_tags`. It does **not** apply `forever_trainer`
  there, so a removed spell in such a build can still show as SoD.
- **`scripts/test_spell_reach.py`** pins the measured IDs.

**Unreachable does not mean SoD.** NPC spells live server-side and are never
reachable from client data. It means "not verifiable as player-facing".
`diff_builds.py` and `render_patchnotes.py` partition spell changes into
**player-facing**, **Season of Discovery (cut tiers)** and **unreachable /
server-side**, ahead of the per-table detail; unreachable spells get their own
section, never dropped.

**`forever_trainer`** (a `sod_tags` step):
- **The rule.** A tagged spell with a trainer row (AcquireMethod 0,
  non-Engraving, with `SpellLevels`) that the SoD reference's
  `SkillLineAbility` lacks is marked `live_in_forever` with
  `live_source = 'forever_trainer'`. Example: Fire Nova 408341–408345, the
  Shaman trainer spell in Forever (AcquireMethod 0 on 375 Elemental Combat),
  was AcquireMethod 3 on 373 Enhancement in 1.15.9.
- **Not evidence:** a row identical to SoD's. Aspect of the Viper 415423,
  Shadowfiend 401977, Redirect 438040, Totemic Projection 437009 and Heart of
  the Lion 409580 carry the same AcquireMethod-0 row in 1.15.9, so they stay
  cut.
- `load_reference_sla()` reads the reference's `SkillLineAbility.csv`.
  Without it the step reads **NOT SCANNED**.

**Known limitations:**
- `ItemSparse` carries test and GM items, so their spells read as
  player-facing. Area Death (TEST) 265 is reachable via item 5417.
- Forever-ID clones of SoD spells (same name as a tagged spell, on no skill
  line or tree) are invisible to the tagger; they show as unreachable.
- SoD items are not tagged as items; there is no `sod_item_tags`.
- The 1.15 reference build (`out/1.15.9.69722/`) has no `ItemSparse` or
  `ItemEffect`, so "was this item in the SoD client?" cannot be asked.
