---
name: wow-query
description: Answer ad-hoc questions about World of Warcraft Forever client data by querying out/<build>/wow.db. Use for any question about spells, items, creatures, zones, lighting, hotfixes, or what changed between builds - e.g. "what does Thunder Clap scale with", "which items only exist as hotfixes", "is this row retail contamination", "what tables reference Map". Covers the schema's prefixes, join paths, and the rules that stop a query returning a confident wrong answer.
---

# Querying the Forever client data

`out/<build>/wow.db` is a SQLite file built from the extracted CSVs by
`scripts/build_db.py`. Query it with:

```bash
python scripts/query.py "SELECT ID, Name_lang FROM SpellName LIMIT 5"
python scripts/query.py --tables spell        # list tables matching 'spell'
python scripts/query.py --schema SpellEffect  # columns + which are indexed
python scripts/query.py -f q.sql --json
```

The connection is **read-only**; a write attempt errors rather than succeeding.
The CSVs under `out/<build>/db2*/` remain the source of truth — `wow.db` is a
query surface, rebuildable in ~20s.

---

## The three prefixes

| Prefix | Source | Meaning |
|---|---|---|
| *(none)* | `db2_hotfixed/` | **live** — shipped client plus Blizzard's hotfixes |
| `plain_` | `db2/` | **as shipped** in the client |
| `gt_` | `gametables/` | tab-separated GameTables — **not DB2s** |

This is the single most important thing about the schema. "What changed live"
is a join between the first two:

```sql
SELECT h.ID, h.Display_lang
FROM ItemSparse h LEFT JOIN plain_ItemSparse p USING (ID)
WHERE p.ID IS NULL;        -- rows that exist ONLY as live hotfix data
```

**`gt_` is not cosmetic.** `SpellScaling` exists as *both* a DB2 and a
GameTable, and they are different things. The DB2 ships 204-empty in this
build (so there is no `SpellScaling` table at all), while `gt_SpellScaling`
holds the per-level curves. An unprefixed load would collide the moment the
DB2 stopped being empty. When someone asks about scaling, check `gt_` —
the DB2 name being absent does not mean the data is absent.

---

## Join paths

### Spells

**`Spell` has no name column.** Names live in `SpellName.Name_lang`, keyed on
the same ID. `Spell` carries only `NameSubtext_lang` (the rank), plus
description text.

```
SpellName.ID  ──┬── Spell.ID                     rank + description
                ├── SpellMisc.SpellID            icon, range, cast time, attributes
                ├── SpellEffect.SpellID          one row per EffectIndex
                ├── SpellLevels.SpellID          BaseLevel / SpellLevel
                ├── SpellPower.SpellID           mana cost
                ├── SpellCategories.SpellID      category, mechanic
                ├── SpellCooldowns.SpellID       recovery times
                └── SkillLineAbility.Spell       which class/skill can learn it
                       └── SkillLine.ID          DisplayName_lang
```

`SkillLineAbility.SupercedesSpell` chains ranks. `SpellEffect` has **multiple
rows per spell** — always constrain on `EffectIndex` or aggregate, or you will
silently double-count.

### Items

```
Item.ID ──┬── ItemSparse.ID        Display_lang, stats, quality, ilvl
          ├── ItemSearchName.ID    a thinner name table; some items have only this
          └── ItemEffect.SpellID   the spell an item casts
Item.IconFileDataID -> an FDID, not a table. Render via /casc/blp2png.
```

Many items have an `Item` row but no `ItemSparse` row — that is normal, not
corruption. `ItemSparse` ships incomplete and arrives by hotfix.

### Creatures, quests, zones

```
Creature.ID          Name_lang, Title_lang        (178 rows — small in this build)
QuestV2.ID           NO name column in this build. Quest text lives in the
                     client's WDB cache, not a DB2. Do not invent a join.
AreaTable.ID         AreaName_lang, ContinentID -> Map.ID, ParentAreaID -> AreaTable.ID
Map.ID               MapName_lang, Directory
```

### Lighting

```
Light.ID ── ContinentID -> Map.ID
         └─ LightParamsID_0 .. LightParamsID_7 -> LightParams.ID
LightParams.ID ── LightData.LightParamID
```

`LightParams` has no map of its own. Its location comes from the `Light` rows
that reference it — which is why the contamination rule for it is a two-hop
join.

---

## Rules

These are rules, not suggestions. Each one exists because ignoring it produced
a wrong answer that looked right.

### 1. Resolve foreign keys through declared relations, never by matching raw values

Scanning every column for a value finds real references and numeric collisions
in the same pass with nothing to separate them. Searching for spell `6343`
by value returns `WMOMinimapTexture.ID`, `TaxiPathNode.ID` and
`UiTextureAtlasMember.ID` — those tables simply have a row whose own ID is
6343. Measured: ~47 "references" against a true count of 17.

Get the real list from WTL:

```bash
curl -s "http://localhost:5080/dbc/relations/Spell::ID"   # 157 columns
```

or use `contamination.inbound_references()`, which does this properly.

### 2. An FK search must cover the array-suffixed forms

Array columns are flattened: `LightParamsID[0]` becomes `LightParamsID_0`. The
suffix count varies by column — `Light` has `LightParamsID_0` through `_7`. A
query that checks only the unsuffixed name finds nothing, and a "no results"
answer here is indistinguishable from a real absence.

```sql
-- wrong: no such column
WHERE LightParamsID = 453
-- right
WHERE LightParamsID_0=453 OR LightParamsID_1=453 OR ... OR LightParamsID_7=453
```

Check with `--schema` before writing the WHERE clause.

### 3. Never assert meaning for a column marked unverified

`GET /dbc/header/<Table>` returns `unverifieds`. Report those columns' **raw
values only** — never a decoded name, a colour, or an interpretation.
`LightData.Field_1_60_1_69876_055` moving `0 → 13533183` is reportable;
"it became lavender" is not, and was recorded as a near-miss in CLAUDE.md
finding #4.

Use `enrich.Enricher.unverified(table)` rather than re-deriving this.

### 4. Enum decoding is context-gated

`Item::SubclassID` has **21 mappings gated on `Item::ClassID`**. The same raw
`0` means *Miscellaneous* when `ClassID` is 4 and *Axe* when it is 2. Decoding
without the gate column produces a confident wrong label.

`enrich.decode(table, column, value, context)` handles this and returns
`needs_context` rather than guessing when the gate is missing.

### 5. Pass `build=` to the meta routes

`GET /dbc/meta/getMappings?build=<version>`. Without it, columns whose enum
was re-versioned return **both** variants with the retail one first — a
decoder taking the first match labels every Forever weather row with retail
names. Measured: 2 of 606 mappings collide unfiltered, 0 with `build=` set.

### 6. Check the base rate before calling a pattern a signal

Three proposed detectors were rejected on exactly this, after measurement:

| Proposed rule | Base rate | Verdict |
|---|---|---|
| ID in a "modern retail range" | 160 of 233 `Achievement` rows | rejected |
| Item has no `ItemSparse` row | 8,286 of 31,675 | rejected as a trigger |
| Row is unreferenced | 63.5% of spells, **identical** for classic and modern IDs | rejected |

Before reporting "N rows look wrong", compute how many rows look that way in
total. A pattern matching two thirds of a table is a property of the table.

### 7. "No data in this build" is a valid and common answer

**550 of 1161 tables are empty** in 1.60.1.69913 — a 204 from WTL, and no CSV
at all, so no table in `wow.db`. `SpellScaling`, `SpecializationSpells` and
`AreaTriggerBox` are all absent for this reason. A missing table means the
build does not ship that data; it does not mean the extraction failed. Say so
plainly rather than hunting for a substitute.

### 8. Route ID resolution through `enrich.py`

Do not re-derive label columns, FK targets or item lookups:

```python
import enrich
e = enrich.get("1.60.1.69913")
e.label("Achievement", 9275)   # 'Warlord Zaela kills (Upper Blackrock Spire)'
e.item(720)                    # dual-routed: tooltip for shipped, join for hotfix-only
e.unverified("LightData")      # set of columns that must not be interpreted
e.decode("Item", "SubclassID", 0, {"ClassID": "4"})
```

`e.item()` matters in particular: `/dbc/tooltip/item/` is non-hotfixed and
returns `"Unknown Item"` for the 4,218 hotfix-only rows, with a 200.

---

## Worked examples

### 1. What does Thunder Clap scale with?

Exercises the spell join chain, a GameTable, and a distribution check.

```bash
python scripts/query.py "
SELECT n.ID, s.NameSubtext_lang AS rank, l.BaseLevel, e.EffectBasePointsF AS base,
       e.EffectRealPointsPerLevel AS perlvl, e.EffectBonusCoefficient AS coef
FROM SpellName n
JOIN Spell s        ON s.ID = n.ID
JOIN SpellLevels l  ON l.SpellID = n.ID
JOIN SpellEffect e  ON e.SpellID = n.ID AND e.EffectIndex = 0
WHERE n.Name_lang = 'Thunder Clap' AND s.NameSubtext_lang LIKE 'Rank%' AND n.ID < 100000
ORDER BY l.BaseLevel"
```

```
ID     rank    BaseLevel  base   perlvl  coef
6343   Rank 1  6          10.0   0.0     0.0
8198   Rank 2  18         23.0   0.0     0.0
...
11581  Rank 6  58         103.0  0.0     0.0
```

Flat per-rank values, no per-level scaling, no coefficient. Two follow-ups
before concluding anything:

```bash
# Is the coefficient column dead in this build, or is this spell's zero real?
python scripts/query.py "
SELECT CASE WHEN EffectBonusCoefficient <> 0 THEN 'nonzero' ELSE 'zero' END AS coef,
       COUNT(*) FROM SpellEffect GROUP BY 1"
#   nonzero 11398 / zero 31051  -> the mechanism is live; this zero is meaningful

# The DB2 SpellScaling is 204-empty. Check the GameTable.
python scripts/query.py "SELECT Level, Warrior, Item FROM gt_SpellScaling WHERE Level IN (1,60)"
#   Warrior 0 at every level; Item populated -> class scaling is unpopulated
```

**Answer:** the data states flat per-rank damage with no scaling expressed, and
the zeros are meaningful because the mechanisms are in use elsewhere. Whether
the client applies an attack-power coefficient in code is outside what these
tables can answer — say that rather than implying the spell does not scale.

### 2. Which items exist only as live hotfix data?

```bash
python scripts/query.py "
SELECT h.ID, h.Display_lang AS name, h.OverallQualityID AS q, h.ItemLevel AS ilvl
FROM ItemSparse h
LEFT JOIN plain_ItemSparse p USING (ID)
WHERE p.ID IS NULL AND h.Display_lang LIKE '%Grand Marshal%'
ORDER BY h.ItemLevel DESC LIMIT 6"
```

```
234565  Grand Marshal's Claymore       4  80
234566  Grand Marshal's Sunderer       4  80
...
```

4,218 rows in total. Note `q` is `OverallQualityID` — a raw enum. Decode it
through `enrich`, do not hardcode a mapping.

### 3. Is this lighting row retail contamination?

The naive query, and why it is not the answer:

```bash
python scripts/query.py "
SELECT COUNT(*) FROM Light l LEFT JOIN Map m ON m.ID = l.ContinentID WHERE m.ID IS NULL"
#   135
```

**135 `Light` rows reference maps absent from this build.** Referencing an
absent map is therefore common in `Light`, and on its own it is not a signal —
rule 6. The real rule narrows to `LightParams` whose *only* referencing rows
sit on absent maps:

```bash
python scripts/query.py "
WITH refs AS (
  SELECT lp.value AS param, l.ContinentID AS map
  FROM Light l
  JOIN json_each(json_array(l.LightParamsID_0,l.LightParamsID_1,l.LightParamsID_2,
                            l.LightParamsID_3,l.LightParamsID_4,l.LightParamsID_5,
                            l.LightParamsID_6,l.LightParamsID_7)) lp
  WHERE lp.value <> 0)
SELECT param, COUNT(*) AS refs,
       SUM(map NOT IN (SELECT ID FROM Map)) AS on_absent
FROM refs GROUP BY param HAVING refs = on_absent AND refs > 0"
```

Note the `json_each` trick: it is how you search all eight array-suffixed
columns without writing eight `OR`s (rule 2).

That returns **44** `LightParams` — still not a finding. `contamination.py`
reports exactly **one** (`LightParams` 453), because `light_absent_map`
operates on *changed* rows in a diff, not on the standing population. The
third narrowing is "and a hotfix touched it".

So the funnel is **135 → 44 → 1**, and only the last number is a finding. Each
narrowing is doing real work; quoting any of the earlier two as evidence would
overstate the result by one to two orders of magnitude. Cross-check against
`contamination.py` rather than reimplementing it, and remember its verdict may
be `not_scanned` — a zero from rules that could not read the data is not a
clean result.

---

## Before answering

- Did the query cover array-suffixed columns? (rule 2)
- Is any column in the output marked unverified? (rule 3)
- Is any enum context-gated? (rule 4)
- What is the base rate for the pattern being reported? (rule 6)
- Is a missing table actually "not in this build"? (rule 7)
- For "what changed live", is the join between the unprefixed and `plain_`
  tables, rather than one of them alone?
