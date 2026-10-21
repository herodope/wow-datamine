# Retail contamination

How `scripts/contamination.py` flags retail-era rows in Forever tables, which
rules exist and which were measured and rejected. Read this before changing a
rule or judging a finding.

`wow_classic_beta` is a recycled product code and Forever shares tooling with
retail, so retail-era rows leak into tables that should hold only Classic
content. They get pruned over time. **Track them per build: a new one appearing
is a signal, and one disappearing tells you Blizzard noticed.**

`scripts/contamination.py` runs in three places, all through `scan()`:
`diff_hotfixes.py` (the hotfix report), `diff_builds.py` (the build diff) and
`render_patchnotes.py` (every HTML mode, including `--since` waves).
`diff_builds.py` hands it the **shipped** `db2/` tables; the other two hand it
the **live** overlay first. The rules' reference data, and the orphan base rate
below, differ by variant accordingly. Rules, ranked by how much they actually
discriminate:

| Rule | Confidence | What it catches |
|---|---|---|
| `dangling_map_ref` | **MEDIUM** (added rows) / HIGH (removed rows) | A row references a Map ID absent from this build. Since 1.60.1.70009 Blizzard also withholds `Map` rows for unreleased **Forever** dungeons (Shaper's Terrace, Alcaz Prison, Hyjal Summit and Barrow Deeps boss statistics hit it), so an added-row hit is a lead, not a retail verdict |
| `withheld_map_ref` | LOW | The same, but the absent map has `MapDifficulty` rows in this build, so the map exists and only its definition is withheld. For watching, not a contamination verdict |
| `light_absent_map` | HIGH | A `LightParams` ID whose only referencing `Light` rows sit on absent maps |
| `orphan_removal` | MEDIUM | Any removal of **≥ 1** `Item` row with no `ItemSparse`/`ItemSearchName` row. No threshold; the finding states the count, and the reader judges whether it was coordinated (push 112078 pulled 75 at once) |

**Appearing vs leaving.** Both reports split findings in two. Rows that were
**added** (or values that are newly referenced) are *appearing*. Rows that
were **removed** (or `LightParams` values being **replaced**) are *leaving*:
contamination being cleaned out, which is a fix, not a new finding.

**An absent map does not mean retail.** Blizzard withholds Forever's own `Map`
rows too, so the two cases look identical to this rule. Judge each hit by
name and context. Treat a MEDIUM `dangling_map_ref` as a lead to check, not a
verdict.

**Three rules deliberately not implemented**, because measurement showed they
do not discriminate in this build:

- **ID falls in a "modern retail range".** `Achievement` IDs run 627–64159 with
  160 of 233 rows above 61000, so an ID-range test flags most of the table. ID
  ranges are supporting evidence only, never a trigger.
- **Item row has no ItemSparse/ItemSearchName data.** Measured at
  **1.60.1.70009**: **12,594 of 31,818** `Item` rows (39.6%) in the shipped
  `db2/`, **8,257 of 31,818** (26.0%) live. `ItemSparse` ships incomplete and
  arrives by hotfix, so orphanhood alone would flag a quarter to two fifths of
  the table. The rule fires only on orphans being **removed**, from one row
  up, and reports how many.
- **The row is unreferenced.** Measured across four ways of deciding which FK
  columns count as the entity's own definition tables, using the 461xxx
  Thunder Clap ladder (defined but unconsumed) as the test case:

  | Framing | Flags | Separates the two ladders? |
  |---|---|---|
  | coverage ≥ 50% of spells = definition | 10.4% | **no** — 461xxx scores 7 each |
  | table name starts with `Spell` | 63.9% | yes |
  | hand-picked player-reachability columns | 64.2% | yes |
  | `SkillLineAbility` membership alone | 80.3% | yes |

  Every framing that discriminates flags **two thirds to four fifths** of the
  table — worse than the orphan test it would generalise. The one with a
  tolerable rate does not discriminate at all, and the coverage distribution
  has no natural break to pin a threshold to: it runs smoothly from 99.95%
  down to 0.00%.

  The decisive measurement is that the rate is **flat across ID ranges**:
  **63.5%** of classic-era spells (ID < 100k) are unreferenced and **63.5%** of
  modern-ID spells (≥ 400k) are too. Being unreferenced carries no information
  about whether a row is retail-era. Most spells in any build are NPC
  abilities, triggered effects, item procs and internal auras that nothing is
  supposed to reference — unreferenced is the **normal** state.

  What is informative is a **paired** comparison: two rows with the same name
  and rank where one is fully wired and the other is not. That is a judgement
  about a specific pair, not a population test, and it does not reduce to a
  confidence level. `contamination.inbound_references()` exposes the
  measurement — declared-FK counts split into definition and external — and
  deliberately assigns no confidence and produces no findings.

Known cases and their per-build status are in the local findings record
(`config.FINDINGS_DOC`). Check removed `Item` stubs by their exact IDs, never
by counting `ClassID` 4 / `SubclassID` 0 orphans: there are **1,128** such rows
in 1.60.1.69913, and orphanhood is normal here.
