# WoW Forever Datamining

Local pipeline for extracting, parsing, and diffing World of Warcraft: Forever
client data across beta builds.

History and findings: .claude/notes/state-log.md (local only)

---

## Target

| | |
|---|---|
| Game | World of Warcraft: Forever (Classic+) |
| Install root | `config.INSTALL_ROOT` — stock Battle.net path by default; override with the `WOW_INSTALL_ROOT` env var |
| Flavor folder | `_classic_beta_` |
| TACT product code | `wow_classic_beta` |
| CDN path | `tpr/wow` |
| Beta announced | 2026-09-17 — the **public** start |
| First build on CDN | 2026-09-16 18:23 UTC (build 69876) |

Storage is **CASC** (not MPQ). Builds predate the announcement by about a day:
69876 and 69893 were both pushed on 09-16. Do not use 2026-09-17 as a lower
bound when looking for builds — it is a press date, not a data date.

`builds.json` is the build index: `buildConfig` / `cdnConfig` per build. These
hashes are the only way to reach a build after Blizzard rotates it off the live
version list. Capture them every patch day, before anything else. 69876 and
69893 are already off the live list and reachable only through WTL's archive
(`GET /build/list`).

> **A `cdnConfig` rotates independently of the build.** The `buildConfig` is
> the stable identity; treat a changed `cdnConfig` for an unchanged
> `buildConfig` as normal, and keep whichever you captured rather than assuming
> one supersedes the other.

---

## CRITICAL: build filtering

`wow_classic_beta` is a **recycled product code**. It has previously hosted:

| Version line | Product |
|---|---|
| 1.13.x | Classic 2019 beta |
| 1.14.x / 1.15.x | Classic Era / SoD / Anniversary betas |
| 2.5.x | Burning Crusade Classic beta |
| 3.4.x | Wrath Classic beta |
| 4.4.x | Cataclysm Classic beta |
| 5.5.x | Mists of Pandaria Classic beta |
| **1.60.x** | **Forever** — our target |

**The version regex alone is the discriminator.** ✅ measured 2026-09-20 against
`GET /build/list`: the complete set of version lines that have ever lived on
this product code is **1.13, 1.60, 2.5, 3.4, 4.4 and 5.5**. Only 1.60 is
Forever, and nothing else comes near it. A build belongs to Forever if:

```
version matches  ^1\.6\d\.       (tolerant of a future 1.61 content patch)
```

Anything failing that is a **different game**. Discard it quietly. Never diff
across the boundary — comparing 1.60 against 5.5 produces meaningless noise.
`diff_builds.py` must refuse to run if either build fails this filter.

Do **not** filter on date; wago.tools backfills and re-indexes older builds,
and the beta's public start date is a day later than its first build.

### The build-ID gate is a sanity check with a loud failure mode

`FOREVER_MIN_BUILD_ID` (**69876**, the oldest build observed) is *not* how
Forever is identified. It exists to catch one hypothetical: a future 1.6x
build on this recycled product code that is **not** Forever. That has never
happened.

A build matching the version pattern but falling below the gate is a
**near miss**, and near misses are never silent:

| Verdict | `classify_build()` | Behaviour |
|---|---|---|
| `forever` | version matches, id ≥ gate | proceed |
| `near_miss` | version matches, id < gate | **banner on stderr naming the build**, recorded in `config.NEAR_MISSES`, re-listed in `fetch_builds.py`'s summary with its hashes |
| `foreign` | version does not match | dropped with a one-line log |

A near miss and a different game must never be indistinguishable in the
output. **Never raise the threshold.** If a near miss appears, lower the gate
to match reality and capture the build's `buildConfig`/`cdnConfig`
immediately. A near miss on a *higher* ID than anything seen is impossible by
construction.

---

## Architecture decision: WTL is the engine

WTL already has DBCD wired up, WoWDBDefs loaded, TACTSharp embedded, and the
build indexed. It exposes local HTTP endpoints on `localhost:5080`.

**Scripts drive WTL over HTTP rather than reimplementing extraction.** Do not
shell out to TACTTool and re-parse DB2s with DBCD unless WTL's endpoints prove
genuinely insufficient for a specific need. Rebuilding that stack is a large
amount of code that duplicates working, maintained functionality.

When a script needs a WTL route, **read the controllers in
`vendor/wow.tools.local/Controllers/` to find it.** Never guess route shapes.

TACTSharp stays built in `vendor/` as an escape hatch for raw file extraction
and CDN-only builds that WTL will not load.

---

## Prerequisites

| | |
|---|---|
| Python | 3.10+ — `mcp` requires it; the scripts themselves are 3.9-compatible |
| .NET | whatever `vendor/wow.tools.local` needs to build (`dotnet run -c Release`) |
| Python packages | `pip install -r requirements.txt` |

**`requirements.txt` has exactly one entry.** The pipeline is deliberately
stdlib-only — extraction, diffing, enrichment, `build_db.py`, the HTML
renderer and `query.py` use nothing but `urllib`, `sqlite3`, `csv`, `json`,
`hashlib` and `concurrent.futures`. `mcp` is the single third-party import in
the repo, and it is needed only by `scripts/mcp_server.py`.

Keep it that way. It means the pipeline runs on a bare Python with no install
step, and a broken dependency can only ever take out the MCP server — never an
extraction on patch day, which is the one thing that cannot be repeated later
once Blizzard rotates a build off the version list.

---

## Running WTL

```powershell
cd vendor/wow.tools.local
dotnet run -c Release
# http://localhost:5080
```

- **Close WoW and idle Battle.net first** — CASC file locks will cause a partial
  or failed load.
- `config.json` is read from the **working directory**, which is why it must be
  launched from `vendor/wow.tools.local`. The built exe under `bin/Release/`
  needs its own copy beside it. Use `scripts/run-wtl.ps1` and avoid the trap.
- First start downloads definitions and the listfile; expect several minutes and
  3–5 GB RAM.
- It is a **blocking server**. Scripts that depend on it must detect it is not
  running and fail with a clear message rather than hanging.
- ⚠️ The **Manual build** box on the Diff page defaults to product `wow`. It
  must be `wow_classic_beta`. Invalid config hashes crash WTL outright.

---

## WTL API gotchas

Full route reference: [`docs/wtl-api.md`](docs/wtl-api.md). These are the traps
that produce **silently wrong output** rather than an error. All measured
against a live instance on 1.60.1.69913.

- **The hotfix parameter is `useHotfixes=true` on API routes.** The browse page
  URL spells it `hotfixes=` and rewrites it client-side before calling the API.
  Sending `hotfixes=` to an API route binds nothing, returns **200**, and
  silently yields non-hotfixed data — measured at **10,556 rows with
  `useHotfixes=true` vs 6,622 without** on `itemsearchname`. **NEVER use the
  page's spelling in scripts.** This is the failure that would fill
  `db2_hotfixed/` with plain DB2 data and never report a problem.
- **Iterate the build-filtered table list.** `/listfile/db2s` unfiltered returns
  **1342** tables — everything WoWDBDefs defines. Build-filtered
  (`?build=<version>`) returns **1161**. Use the filtered list or ~181 exports
  come back empty.
- **Two distinct DataTables envelope shapes exist.** `/dbc/info` and `/dbc/data`
  carry an `error` key; `/dbc/hotfixes/list`, `/listfile/files` and
  `/build/table` do **not** — reading `d["error"]` on those raises `KeyError`.
  Use `.get("error")` for a single code path.
- **204 and 404 mean different things.** `204 No Content` is
  defined-but-zero-rows in this build (e.g. `modifiedcraftingitem`); `404` is
  not in this build at all. Handle them distinctly — a 204 is not a failure.
  **The two variants of a table can disagree:** a table can return 204 with
  `useHotfixes` off but 200 with rows when on — it exists *only* as live
  hotfix data and was never shipped in the client build. `extract_db2.py`
  records this as `resolution: "hotfix_only"`, distinct from `empty`.
  `TimeEventData` in 1.60.1.69913 is the reference case, and the only one in
  1161 tables — which is exactly why it is easy to miss. Never decide a table
  is empty from the plain request alone.
- **`hotfix_delta` is computed from a content hash, not row counts.** Five
  tables in 1.60.1.69913 — `GlobalStrings`, `Light`, `LightData`,
  `LightDataGlobalVolumeFog`, `LightParams` — have **identical row counts in
  both variants but different values**. A count-based comparison reports no
  change for all five.
- **`/dbc/hotfixes/list` returns zeros when given no query string.** It
  short-circuits on `!Request.QueryString.HasValue`, so a bare request looks
  like "no hotfixes exist". Always pass `?length=N`.
- **Default locale on DB2 routes is `All_WoW`, not `enUS`.** Pass `locale`
  explicitly rather than relying on the default.
- **`type:unk` returns 0 despite 99,348 rows carrying that type.** The `type:`
  search token looks up `Listfile.TypeMap`, which has **no `unk` bucket**, so
  the lookup fails and the search falls through to a plain substring match on
  filenames — matching nothing. The `content_type` column gets `unk` from a
  separate `TryGetValue` fallback. Trusting the search token would report
  nothing to classify when in fact 99,348 files needed it. Read the column,
  never the token.
- **`recordsFiltered == recordsTotal` proves NameMap *membership*, not a
  non-empty name.** `/listfile/files` iterates `Listfile.NameMap`, so its
  unfiltered `recordsFiltered` counts rows that are *in* the map — including
  those whose name is the empty string. At 1.60.1.69913 both counts are
  1,441,771, which reads as "nothing is unnamed", but 19,583 of those rows
  carry an empty filename. **Count empty values in the filename column
  (index 1); do not infer naming coverage from the two totals matching.**
- **WTL exposes no per-file size over HTTP.** `/size/data` only aggregates
  (`results[type] += fileSize`), and every other route works from
  `Listfile.NameMap`, which carries no size. Real per-file sizes live in the
  CASC indices and require parsing `Data/data/*.idx` directly — that is a
  separate piece of work, not an API call. `files.csv` emits an empty `size`
  column for schema stability.
- **`/dbc/updateDefs` is the "Update WoWDBDefs & clear cache" button.** With our
  local `definitionDir` it skips the download half and only does
  reload-and-clear — which is exactly the half needed after `sync_refs.py` has
  refreshed the clone on disk.
- **Always pass `build=` to `/dbc/meta/getMappings`.** Without it, columns
  whose enum was re-versioned return **both** variants and the retail one
  comes first. Measured live on 1.60.1.69913: unfiltered, exactly 2 of 606
  mappings carry colliding values — `Weather::Type` returns 12 entries for
  values 0–5 (retail `0 None, 1 Clear, 2 Rain…` ahead of Classic
  `0 Clear, 1 Rain, 2 Snow…`) and `SpellEffect::Effect` returns 361 with one
  duplicate. A decoder taking the first match on value silently labels every
  Forever weather row with **retail** names. With `build=1.60.1.69913`:
  **0 colliding mappings and 0 empty ENUM/FLAGS mappings** — the filter is
  what selects the Classic-era variant.

  The mechanism constrains what the filter can do. `BuildRange.Contains`
  compares **componentwise**, not lexicographically:
  `major >= min.major && major <= max.major`. Forever is `1.60.1.69913`, so
  `major = 60` fails `<= 12` against the `Vanilla` preset and `expansion = 1`
  fails `>= 2` against every TBC-and-later preset. **A 1.60.x build matches no
  preset range that exists**, so `build=` can only ever *drop* build-tagged
  entries, never select one. That gives the right answer here only because the
  era-specific variants are the tagged ones and the Classic defaults are
  untagged — an authoring convention, not a guarantee. If a future definition
  sync tags the Classic variant instead, the filter would strip it and leave
  the column empty. **Re-run the collision check after every `sync_refs.py`:**
  with `build=` set, no ENUM/FLAGS mapping should come back with zero entries.
- **`output=png` on `/map/tile` is read only inside the `type == "adt"`
  branch.** Every BLP falls through to the tail path, which unconditionally
  returns `application/octet-stream` holding raw RGBA bytes — `targetSize ×
  targetSize × 4`, no header — with a **200**. Requesting a PNG minimap tile
  succeeds and yields something no image decoder will open. Use
  `/casc/blp2png` for a PNG; use `/map/tile` only for pixels to composite.
- **A 500 from WTL usually means bad input, not a dead server.**
  `Startup.cs` registers `UseDeveloperExceptionPage` only under
  `IsDevelopment()`, and there is no handler registered for any other
  environment. `launchSettings.json` sets `ASPNETCORE_ENVIRONMENT=Development`,
  so launching through `run-wtl.ps1` (`dotnet run`) **does** get the dev
  exception page — a 500 arrives as `text/plain` with a stack trace, which is
  worth reading. Run the built exe without that variable and the same throw
  returns a bare 500 with an empty body instead. Either way the reason is
  never a structured error field. `/casc/blp2png` on a non-BLP, `/dbc/tooltip/item` on a
  `RandPropPoints` miss or unknown `SubclassID`, `/map/wdtMask` on an unknown
  layer and `/map/download?layer=5` all reach it. Scripts must treat 500 as
  possible bad input and keep going, not as "WTL is down" — probe
  `/casc/buildname` to tell the two apart.
- **`/casc/blp2png` 404s on encrypted files.** It reads the first four bytes
  and returns `NotFound()` when all four are zero, which is exactly what a
  file with a missing key decodes to. A 404 there means *unavailable*, not
  *absent* — cross-check the `encryptionStatus` column on `/listfile/files`
  before reporting a texture as missing from the build.
- **The tooltip controller is non-hotfixed and cannot be pointed at another
  build.** Every load is the two-argument `GetOrLoad(name, CASC.BuildName)`,
  i.e. `useHotfixes: false`, with no parameter to change it.
  (`FindRecords(…, true)` looks like a hotfix flag but that fifth argument is
  `single`.) So `/dbc/tooltip/item/<id>` on any of the hotfix-only `ItemSparse`
  additions falls through both `ItemSparse` and `ItemSearchName` and returns
  `Name: "Unknown Item"`, `HasSparse: false` — a plausible-looking answer for
  an item that exists. Anything hotfix-only must be read through
  `/dbc/export`, `/dbc/data`, `/dbc/peek` or `/dbc/find` with
  `useHotfixes=true`.

---

## Baseline metrics (1.60.1.69913)

Track these per build; movement is signal.

| Metric | Value |
|---|---|
| Encrypted files | 5035 |
| ESpecs loaded | 6633 |
| DBD definitions | 1342 |
| Relations / label columns | 531 / 12 |
| Enum mappings / definitions | 606 / 354 |

A **drop in encrypted file count** means keys leaked or content unlocked — one
of the highest-value early signals in beta datamining. Log it every build,
together with the `EncryptedUnknownKey` / `EncryptedButNot` split, which
matters as much as the total. A `MATCH` is only a measurement if the file set
moved and the encrypted count did not.

### Known benign errors

These FDIDs throw "Specified argument was out of the range of valid values"
during analysis. Malformed or placeholder entries, non-blocking:

```
5014017, 5014019, 5014022, 5014025, 7018189,
7079572, 7576057, 7576058, 7576059, 7576060
```

Log and continue. Only investigate if the set changes between builds.

---

## Retail contamination

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

---

## Season of Discovery tags

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

### Detection (counts measured on 1.60.1.70009)

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

### Player reachability (`spell_reach`)

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

---

## Key facts

- **FDIDs share the retail namespace.** `wowdev/wow-listfile` applies directly.
  No Forever-specific listfile exists.
- **DB2 layouts are Classic-flavor, not retail.** WoWDBDefs resolves via
  layouthash, but Forever is new — expect unknown columns in new tables. Name
  them `unk_<offset>`; never guess a meaning in committed output.
- **Typing is the bigger gap, but naming is not zero.** 1.60.1.69913 contains
  **1,441,771** files. **19,583 of them (1.4%) have an empty name** despite
  being members of `Listfile.NameMap` — of those, 11,842 remain `unk` and
  7,721 are classified as `blp` by the magic-byte pass. (2,274,258 is the
  *listfile's* total size across the whole retail FDID namespace, not a count
  for this build.) On typing: **99,348 files have no `content_type`**, and the
  magic-byte pass recovers **87,506** of them — 79,784 as `wmo_or_adt` —
  leaving 11,842 unknown. **Magic-byte classification is valuable and worth
  maintaining.** Filename recovery is the lower-value half.
- **Hotfixes are a separate diffable source.** WTL loads live hotfix data
  from the client's `Cache/ADB/enUS` directory and tracks push IDs. This is
  distinct from the static DB2s shipped in the build: hotfixes are Blizzard
  tuning live data between client patches. Diffing them answers a different
  question than diffing DB2s, and the two must not be conflated.
- **Hotfixes are per build. A new build starts with an almost empty
  overlay.** Earlier hotfixes are either promoted into the new client or
  lapse. A hotfix vanishing on a new build is **not** a revert decision until
  a later wave on that build confirms it; it may simply not have been
  re-pushed yet. And a client change on a new build may already have been
  live for days. Compare the new client against the **previous overlay** as
  well as the previous client.
- **Encryption.** Blizzard withholds Salsa20 keys for unreleased content.
  Encrypted files fail to decode until keys land in `TACTKeys`. Always
  skip-and-log, never error the run.
- **GameTables are a separate data source and the DB2 pipeline cannot see
  them.** 42 tab-separated `.txt` files under `GameTables/` in CASC, holding
  the per-level curves the client interpolates at runtime — `xp.txt`,
  `NpcTotalHp*.txt`, `CombatRatings.txt`, `SpellScaling.txt` and so on.

  They have **no DBD definition**, so `/listfile/db2s` — which enumerates
  definitions — can never list one, and `extract_db2.py` can never reach them.
  WTL has a `GameTableProvider` internally but **exposes no HTTP route** for
  it (checked `Controllers/`; there is none). They are ordinary CASC files, so
  `/casc/fdid` serves them verbatim, which is what `extract_gametables.py`
  uses. Discovery is from `files.csv`, so `inventory.py` must run first.

  Output is `out/<build>/gametables/*.txt`, raw TSV byte-for-byte. No
  conversion to CSV: the header row's exact spelling is how the client names
  the columns, and diffing them as text is the point. 284 KB, ~2s per build.

  **`SpellScaling.txt` is where class spell scaling would live.** The DB2
  named `SpellScaling` ships **204-empty**, so nothing links a spell to a
  scaling class, and in the GameTable **every class column is zero across all
  123 levels** — Rogue through Evoker, including Warrior — while `Item`,
  `Consumable`, `Gem1–3`, `Health`, `DamageReplaceStat`, `DamageSecondary` and
  `Mana Consumable` all carry data. The infrastructure is present and
  unpopulated. A scaling question cannot be answered without checking both.

---

## Tool stack

| Tool | Role | Repo |
|---|---|---|
| wow.tools.local | **Extraction engine + UI.** File browser, DB2 tables, build diffing, model viewer, hotfixes | `github.com/Marlamin/wow.tools.local` |
| TACTSharp | Raw CASC/TACT extraction, CDN-only builds | `github.com/wowdev/TACTSharp` |
| WoWDBDefs | DB2 column definitions — clone locally, set as WTL's DBD dir | `github.com/wowdev/WoWDBDefs` |
| wow-listfile | FDID → path mapping | `github.com/wowdev/wow-listfile` |
| TACTKeys | Decryption keys for encrypted content | `github.com/wowdev/TACTKeys` |
| DBCD | DB2 reader (used internally by WTL) | `github.com/wowdev/DBCD` |
| wago.tools | Remote source for builds not on disk; build history | `wago.tools` |

The **wow.tools website** is deprecated; the **tooling** is not. WTL, TACTSharp,
DBCD and WoWDBDefs are all actively maintained. Site deprecation notices do not
apply to the libraries.

---

## Repo layout

```
.
├── CLAUDE.md
├── .claude/skills/wow-query/SKILL.md   # how to query wow.db, and the rules
├── .claude/notes/state-log.md          # LOCAL ONLY — build log + findings record
├── README.md
├── builds.json              # manifest index — buildConfig/cdnConfig per build
├── sod_allowlist.json       # SoD tags known live in Forever (live_in_forever=1)
├── sod_manual.json          # hand-audited SoD tags no signal reaches
├── requirements.txt         # one entry: mcp. everything else is stdlib
├── scripts/
│   ├── config.py            # product code, paths, build filter — SINGLE SOURCE
│   ├── run-wtl.ps1          # launch WTL from the correct working directory
│   ├── sync_refs.py         # pull WoWDBDefs / listfile / TACTKeys
│   ├── fetch_builds.py      # poll version endpoint, update builds.json
│   ├── extract_db2.py       # WTL HTTP -> CSV per table, plain + hotfixed
│   ├── inventory.py         # file listing + magic-byte classification
│   ├── extract_gametables.py# GameTables/*.txt -- NOT DB2s, see Key facts
│   ├── enrich.py            # ID -> human-readable context, for the reports
│   ├── sod_tags.py          # SoD annotation -> sod_tags in wow.db; NOT contamination
│   ├── test_sod_tags.py     # pins the measured SoD IDs (stdlib unittest)
│   ├── spell_reach.py       # player reachability -> spell_reach in wow.db; patch-note partition
│   ├── test_spell_reach.py  # pins the measured reachability IDs (stdlib unittest)
│   ├── build_db.py          # CSVs -> out/<build>/wow.db, one queryable file
│   ├── query.py             # read-only SQL CLI over wow.db
│   ├── mcp_server.py        # same database over MCP stdio, read-only
│   ├── render_patchnotes.py # self-contained HTML, hotfix + build-diff modes
│   ├── player_notes.py      # build-diff player edition: class -> spec -> spell, live vs live
│   ├── test_player_notes.py # pins provenance and buff/nerf direction rules (stdlib unittest)
│   ├── check_findings.py    # re-test the local findings record against a build
│   └── diff_builds.py       # compare two build dirs, emit markdown
├── out/                     # GITIGNORED — extracted data
│   └── <version>.<build>/
│       ├── db2/*.csv           # plain DB2s, as shipped in the build
│       ├── db2_hotfixed/*.csv  # same tables with the hotfix overlay applied
│       ├── hotfixes.csv        # push IDs + changed rows from Cache/ADB/enUS
│       ├── files.csv           # fdid, path, size, encrypted, content_type
│       ├── gametables/*.txt    # tab-separated, NOT DB2s — see Key facts
│       ├── wow.db              # SQLite over all of the above, rebuildable
│       └── manifest.json       # row counts, layouthashes, metrics
├── reports/                 # GITIGNORED — generated diff output
│   ├── <from>_to_<to>.md
│   ├── hotfix_<build>.md
│   ├── hotfixwave_<build>_since_<date>.html
│   ├── sod_tags_<build>.md              # SoD tiers, chains, coverage
│   ├── patchnotes_<build>.html          # readable, self-contained
│   └── patchnotes_<from>_to_<to>.html
└── vendor/                  # GITIGNORED — cloned third-party tools
```

### `wow.db`

`build_db.py` loads every extracted CSV into one SQLite file per build. The
CSVs remain the source of truth; this is a query surface over them, rebuilt
from scratch on each run and never updated in place. Gitignored with the rest
of `out/`.

| Prefix | Source | Meaning |
|---|---|---|
| *(none)* | `db2_hotfixed/` | the **live** view — shipped plus hotfixes |
| `plain_` | `db2/` | **as shipped** in the client |
| `gt_` | `gametables/` | tab-separated GameTables, **not DB2s** |

The prefixes exist so the comparison this repo is built around is one query:

```sql
SELECT h.ID, h.Display_lang
FROM ItemSparse h LEFT JOIN plain_ItemSparse p USING (ID)
WHERE p.ID IS NULL;        -- 4,218 rows that exist only as live hotfix data
```

`gt_` is not decoration. `SpellScaling` exists both as a DB2 (204-empty in
this build) and as `SpellScaling.txt` in the GameTables, and they are
different things — an unprefixed load would collide the moment the DB2 stopped
being empty.

Two details worth knowing before querying:

- **Column names are sanitised.** `Corpse[0]` becomes `Corpse_0`, because
  brackets are alternative identifier quoting in SQLite and a column you have
  to escape carefully is a column nobody will query. Array foreign keys are
  still indexed: the array suffix is stripped before asking whether a column
  ends in `ID`, so `LightParamsID[3]` → `LightParamsID_3` is indexed.
- **Types are sniffed and declared as affinities**, so `WHERE ID = 6343`
  matches rather than silently finding nothing against TEXT `'6343'`. A
  mis-sniff degrades safely — SQLite stores a value that will not convert
  as-is.

Four derived tables follow the load: `sod_tags` and `sod_tags_coverage` (see
**Season of Discovery tags**), then `spell_reach` and `spell_reach_coverage`
(see **Player reachability** there). They are computed from the live tables
and annotate only.

At 1.60.1.69913: 1,263 tables, 3,844,494 rows, 4,210 indexes, ~317 MB, ~20s.
69876 and 69893 load 1,262 — they have no `TimeEventData`, which exists only
as hotfix data. `_build_info` records which build the file is for, so one
cannot be mistaken for another.

Ad-hoc questions go through `scripts/query.py`, which opens the file
read-only:

```bash
python scripts/query.py "SELECT ID, Name_lang FROM SpellName LIMIT 5"
python scripts/query.py --tables spell     # list matching tables + row counts
python scripts/query.py --schema SpellEffect
```

`.claude/skills/wow-query/SKILL.md` carries the join paths and the query rules
— FK resolution via `/dbc/relations` rather than value matching, array-suffixed
FK columns, unverified columns, context-gated enums, base rates before signals,
and that a missing table usually means "not in this build".

> **`.claude/skills/wow-query/SKILL.md` is force-added, and that is
> deliberate.** A common global gitignore excludes `**/.claude/` as
> *per-project Claude Code state* — settings, caches, session files — which is
> the right default and should stay.
>
> This file is not that. It is shared documentation: the schema's prefixes,
> the join paths, and nine rules each derived from a measurement. Anyone
> cloning the repo needs it to query `wow.db` without repeating known
> mistakes — resolving FKs by value and picking up numeric collisions, missing
> array-suffixed FK columns, asserting a meaning for an unverified column,
> decoding a context-gated enum without its gate.
>
> Committed with `git add -f`. If more files ever land under `.claude/` here,
> add them the same way and only when they are documentation rather than
> state — do not relax the global rule to cover them. `.claude/notes/` is
> state and stays untracked.

### MCP server

`scripts/mcp_server.py` exposes the same database over MCP stdio, for clients
that cannot run `query.py` themselves.

Four tools: `list_tables`, `describe_table`, `query`, `get_conventions`.
Install with `pip install -r requirements.txt` — see **Prerequisites**.

- Read-only is the **driver's** guarantee, not a SQL check: the connection is
  a `file:...?mode=ro` URI, and `ATTACH` is additionally denied by an
  authorizer so a query cannot pull in a second, writable database. The
  statement-shape check exists for clear error messages, not for safety.
- `query` caps results at **100 rows** and says so when it truncates, and
  refuses a query with neither `LIMIT` nor `WHERE` against a table over
  10,000 rows. Aggregates without `GROUP BY` are exempt, so
  `SELECT COUNT(*) FROM ItemSparse` works.
- `get_conventions` returns the nine rules from
  `.claude/skills/wow-query/SKILL.md` **verbatim**. It exists because an MCP
  client cannot see the skill file, and without those rules it will reproduce
  exactly the mistakes they were written to prevent.
- Every response names the build it came from.

Claude Desktop, in `claude_desktop_config.json` — `%APPDATA%\Claude\` on
Windows, `~/Library/Application Support/Claude/` on macOS:

```json
{
  "mcpServers": {
    "wow-datamine": {
      "command": "python",
      "args": ["C:\\path\\to\\wow-datamine\\scripts\\mcp_server.py"]
    }
  }
}
```

**Claude Desktop must be fully restarted after editing that file** — quit it,
not just close the window. It reads the config once at startup, so an edit
with the app still running does nothing, which is indistinguishable from a
broken server.

> Written against **mcp 2.x**, where `FastMCP` was renamed `MCPServer`
> (`from mcp.server.mcpserver import MCPServer`). Examples written for 1.x
> will not import; pin `mcp<2` only if you need that older code.

### `manifest.json` resolution values

One per table, describing how it resolved against the **shipped** build:

| Value | Meaning |
|---|---|
| `ok` | 200, has rows |
| `empty` | 204 — defined but zero rows, in both variants. A success |
| `hotfix_only` | 204 plain, 200 hotfixed — exists **only** as live hotfix data |
| `not_in_build` | 404 — not present in this build |
| `error` | 400, or a transport failure. `error` field carries the reason |

`hotfix_delta` is set independently, from a content hash of the two CSVs, and
is true for `hotfix_only` tables as well as changed `ok` ones.

---

## Endpoints

```
https://us.version.battle.net/v2/products/wow_classic_beta/versions
https://us.version.battle.net/v2/products/wow_classic_beta/cdns
```

Pipe-delimited, not JSON. The `## seqn` line is a comment, not data. Regions:
`us`, `eu`, `kr`, `tw`, `cn`. Use `us`; builds are usually identical across
regions but `cn` can lag or diverge.

---

## Conventions

- **Never hardcode build numbers.** Read from `builds.json` or take as an
  argument. Builds change weekly during beta.
- **Product code and paths live only in `scripts/config.py`.** No scattered
  string literals.
- **Extraction must be resumable.** Checkpoint progress; never restart from zero.
- **Log, don't crash.** An unreadable file is expected. Record it with a reason
  (`encrypted`, `missing_key`, `bad_blte`, `unknown_format`) and continue.
- **Read WTL's source for route shapes.** Do not guess HTTP endpoints.
- **Every rule here is either measured or source-read, and must say which.**
  Reading the controller tells you what the code *can* do. Only a live
  response tells you what it *does*. Source-reading produces a hypothesis;
  measurement produces a rule. Label the difference and never let the first
  masquerade as the second.

  `docs/wtl-api.md` marks measured routes ✅ and keeps an explicit
  "still source-read only" list. Anything unexercised says so where it is
  written, not only in a verification log at the bottom, because the person
  who acts on a rule reads the rule and not the appendix.

  Example: the `build=` rule on `/dbc/meta/getMappings` follows correctly from
  `BuildRange.Contains`, yet read from source alone it points the wrong way.
  Only a live response shows that omitting `build=` returns two conflicting
  enum variants with the retail one first.

  Practically: source-read a route to know what to ask for, then ask. When WTL
  is not running, write the finding down as unexercised and **promote it only
  after a response confirms it**. A hypothesis recorded honestly is useful;
  a hypothesis recorded as fact is a trap.
- **A fix that works does not confirm the diagnosis that motivated it.**
  Symptom, fix and mechanism are three separate claims. A fix landing and the
  symptom going away establishes the first two. The **mechanism has to be
  measured on its own**, because a correct fix sitting next to a wrong
  explanation looks exactly like a correct fix sitting next to a right one —
  and it is the explanation that gets reused on the next problem.

  Example: identical `files.csv` hashes across 69876/69893/69913 are real
  data, not a broken inventory. `ListfileController.cs:208` intersects
  `Listfile.NameMap` with `CASC.AvailableFDIDs` before paging, so the route
  **only ever returns in-build files**, and only 2 of 610 shipped DB2 tables
  differ across those builds. An assertion that fails on identical hashes
  fires on correct data.

  Practically: when a symptom motivates a fix, **state the mechanism as a
  separate, testable claim and measure it separately.** "Does the fix remove
  the symptom" and "is my explanation of the symptom correct" are different
  experiments.
- **Match on declared FK relations, not on raw values.** Scanning every column
  of every table for a numeric value finds real references and coincidental
  collisions in the same pass, with nothing to tell them apart. Looking for
  spell 6343 that way returns `WMOMinimapTexture::ID`, `TaxiPathNode::ID` and
  `UiTextureAtlasMember::ID` alongside the genuine `SkillLineAbility::Spell` —
  those tables simply have a row whose own ID is 6343.

  Resolve the relation instead: `GET /dbc/relations/<Table>::<Column>` returns
  every column DBD declares as a foreign key to that one (157 for a spell at
  1.60.1.69913), and only those columns are worth reading. The difference is
  not cosmetic — a raw-value scan reports ~47 "references" for a classic
  Thunder Clap rank against a true count of 17.

- **An unscanned zero is not a clean result.** A detector that could not read
  the data returns exactly what a detector that read it and found nothing
  returns. Before reporting "0 findings", establish that something was
  actually capable of firing — and report coverage next to the count so a
  reader can tell the two apart without going to the source.

  `contamination.py`'s rules are narrow: `light_absent_map` reads only
  `Light`, `orphan_removal` only removed `Item` rows, and `dangling_map_ref`
  only columns named `instance_id`, `instanceid`, `continentid`, `mapid` or
  `map_id`. No spell table carries a map-reference column, so spell rows
  produce **0 findings and 0 applicable rules**. `scan()` returns
  `(findings, ref, coverage)` with a verdict of `scanned`, `not_scanned` or
  `no_data`, and both diff scripts render **NOT SCANNED — this is not a clean
  result** with a per-rule reason table. When rules *did* run, the report
  names which ones and which were never applicable, so even a genuine zero
  states its own scope. **A detector's output is only meaningful once you have
  shown the detector could have said otherwise.**
- **Diffs are the deliverable.** Raw extraction is a means to an end; the
  markdown reports are what this project produces.
- **Extract every table twice: with and without the hotfix overlay.** Plain
  DB2 output goes to `db2/`, hotfix-applied output to `db2_hotfixed/`.
  Keeping them separate is what makes a client patch distinguishable from
  live tuning — a value that moves only in `db2_hotfixed/` was hotfixed, one
  that moves in both shipped in the build. Collapsing them into a single
  output loses that distinction permanently.
- **Compare variants by content hash, not row count.** A hotfix can change a
  value without adding or removing a row — 5 of the 15 deltas in
  1.60.1.69913 (`GlobalStrings`, `Light`, `LightData`,
  `LightDataGlobalVolumeFog`, `LightParams`) have identical row counts and
  different data. A count-based check misses every one of them.
- **Key rows on the `ID` column, never on column 0.** DBCD emits CSV columns
  in DBD-definition order, so the ID column is **not reliably first** —
  `Achievement.csv` starts with `Description_lang` and has `ID` at index 3.
  A script keying on column 0 produces **silently wrong** diffs: keying
  `Achievement` that way collapses 114 of its 233 rows onto duplicate
  description strings and reports no change against a real 233 → 232
  delta. Always key on the column literally named `ID`, fall back to column 0
  only when absent, and state which key was used in the output. This applies
  to `diff_builds.py` as much as to `diff_hotfixes.py`.

  **A newly generated layout may have no column named `ID` at all.** WoWDBDefs
  names every column of a fresh layout `Field_<build>_NNN` and marks the ID
  with a `$id$` annotation instead. `UiModelSceneActor` at 1.60.1.70009 is
  the case: layout `B777EC3A`, ID column `Field_1_60_1_70009_002`. Keying on
  column 0 there collapses 1,007 rows onto 128 unique `ScriptTag` strings.
  `key_index(header, table)` tries three things in order: a column named
  `ID`, then the DBD's `$id$` column, then column 0. `diff_builds.py` keys
  each side on its own header, because the two sides of a schema change can
  name the ID column differently. Pass the table name at every call site.
- **Never field-compare across a schema change.** Two **independent** signals
  govern whether a positional comparison is valid, and they move separately:

  | Signal | Source | Tracks |
  |---|---|---|
  | layouthash | the DB2 header (`manifest.json`) | the **client's** record layout |
  | CSV header | WoWDBDefs definitions | the **definition** |

  A definition update can rename or add columns with **no layouthash change** —
  an `unk_<offset>` becoming a real name does exactly that — so checking the
  layouthash alone is not enough. Two gates:

  - `fields_comparable` requires **neither** to have changed.
  - `rows_comparable` requires neither the **layouthash** nor the **column
    count** to have changed.

  The gates differ because a column **renamed in place** keeps every value in
  its position: row-level change detection survives, while field names become
  ambiguous. A layout change or a column insert invalidates both — a positional
  diff then reports every row as changed and every field as different, which is
  noise dressed up as signal.

  **A table with a schema change must stay in the report even when its
  magnitude is zero.** Suppressed field diffs leave added/removed/changed all
  at 0, and a "skip anything with magnitude 0" rule would silently drop the
  tables that most need flagging. Report the change in the summary, in a
  dedicated section, and in the table's own section, stating which
  comparisons were suppressed and why. Keep a schema change with no row
  changes in the test fixtures.

  **A layout change with byte-identical data is still a schema change.**
  `diff_table()` checks the layouthash before skipping identical CSVs, so a
  table like `SpellDispelType` at 1.60.1.70009 (layout `47AA7AEB` →
  `3B574D4B`, retail 12.1.5's layout, `Mask` u8 → u16, all 11 rows identical)
  is reported as "data byte-identical (storage-only change)", with nothing
  suppressed.

  `diff_hotfixes.py` does **not** have this guard. It compares two exports of
  the same build, so the schema is normally identical on both sides — but if
  definitions shift mid-build (a `sync_refs.py` run plus `/dbc/updateDefs`
  between the two extractions), the same trap applies and the guard should be
  added there too.
- **Treat push IDs of `2^24 + recordID` as synthetic.** Not every push ID in
  the hotfix table is a real push. IDs equal to `16777216 + recordID` are
  derived arithmetically from the record ID by a bulk-injection path, and
  counting them as distinct pushes invents authoring events that never
  happened — the 4,218 `ItemSparse` additions in 1.60.1.69913 produce 4,218
  phantom "pushes" that way, which reads as incremental authoring when it was
  a single bulk load. **Real pushes for this build are in the 112xxx range;
  anything above ~16.7M is generated.** Label those records
  `bulk injection (N records, no real push attribution)` and report genuine
  push IDs separately. The two overlap: the hotfix table holds 4,218 synthetic
  and 194 real records for each of `ItemSparse` and `ItemSearchName`, but only
  114 of the real ones (all in `ItemSparse`) attach to rows the diff sees as
  added — those 114 were bulk-loaded *and* later hotfixed for real. Re-verify
  the base if a future build changes the scheme.
- **Cap concurrency at ~8.** CASC reads are IO-bound; oversubscribing thrashes
  the disk.

---

## Patch-day checklist

1. Let Battle.net finish patching, then **launch the game client once** so the
   local CASC index is complete. Skipping this yields partial extractions.
2. Close WoW and idle Battle.net.
3. Run `fetch_builds.py` — capture the new `buildConfig` / `cdnConfig` into
   `builds.json` **before anything else**. Unrecoverable if skipped.
4. Run `sync_refs.py` — clones/updates WoWDBDefs, the listfile and TACTKeys
   into `vendor/`. This must happen **before** WTL's DBD directory is
   configured or used: `definitionDir` points at a clone that has to exist
   on disk first, and WTL silently falls back to the remote manifest if it
   does not.
5. Point WTL's `definitionDir` at `vendor/WoWDBDefs/definitions`, then start
   WTL.
6. **Call `GET /dbc/updateDefs`** (the "Update WoWDBDefs & clear cache"
   button). This must follow `sync_refs.py` — WTL caches definitions and
   will otherwise keep using the stale set from the previous build even
   though step 4 refreshed them on disk. With a local `definitionDir` this
   only reloads and clears; it downloads nothing.
7. Extract DBCs for the new build via the builds page.
8. Run `extract_db2.py`, then `inventory.py`, then `extract_gametables.py`,
   then `build_db.py` (in that order — GameTable discovery reads
   `files.csv`, and the database loads all three). `patchday.py` step 8
   does all four.
9. Diff against the previous Forever build. **WTL must still be running**:
   `diff_builds.py` compares CASC content keys through `/casc/diff` and
   diffs changed Lua/XML/TOC files. That is the only check that sees a file
   rewritten under an unchanged FDID. It caches to
   `out/<to>/content_diff_<from>.json`, which `render_patchnotes.py` reads.
   With WTL down and no cache, both reports say **not measured**.
   `inventory.py` cannot see content-only changes: `files.csv` has no content
   hash.
10. Commit `builds.json` and the new report.

### Hotfix-only day (downtime with no new build)

Blizzard takes the realms down "for fixes" and the version endpoint still
serves the **same `buildConfig`**. Nothing was patched; the hotfix overlay
moved. The build-to-build diff has nothing to say, and the standard hotfix
report (overlay vs shipped client) answers the wrong question — it restates
every hotfix ever applied to this build, so today's wave is invisible inside
it.

Diff the overlay against **its own previous state** instead. The baseline is a
copy of `db2_hotfixed/` taken *before* the re-extract, so it must be made
first and cannot be recovered afterwards:

```bash
B=1.60.1.69913
cp -rp out/$B/db2_hotfixed out/$B/db2_hotfixed.snapshot-$(date +%Y%m%d)
python scripts/extract_db2.py --build $B --restart
python scripts/render_patchnotes.py --build $B --since <YYYYMMDD>
```

`--since` is the third renderer mode. Both sides are live data, so on that page
`rows_plain` is **the previous live state, not the shipped build**.

- **Snapshot before extracting, always.** A re-extract overwrites
  `db2_hotfixed/` in place. Skip the copy and the previous live state is gone
  — the same class of unrecoverable miss as not capturing `buildConfig`.
- **Log out of the client before extracting.** WTL's `HotfixManager` globs the
  exact filename `DBCache.bin` and never the session buffer
  `DBCache.bin<pid>.tmp`, which the client holds under an exclusive lock, so a
  wave measured mid-session is a lower bound.
- **A re-extract on an unchanged build is cheap.** 13s for all 1161 tables,
  because WTL serves the exports from cache. There is no reason to extract
  selectively.
- **Do not drive the wave diff off `/dbc/hotfixes/list`.** Scan every CSV.
  Measured on 69913: the list reported new records in **19** tables while
  only **8** tables' exported rows actually moved, and it named **5**
  `BroadcastText` records against **11** rows that appeared.
- **`firstDetected` is when WTL first saw a record, not when Blizzard pushed
  it.** It is an upper bound on push time. The wave page uses the baseline
  directory's mtime as the cutoff and says so; do not present the window as
  push times.
- Re-run `diff_hotfixes.py`, `inventory.py` and `build_db.py` afterwards — the
  cumulative report, manifest and database all describe the old overlay until
  you do. `write_manifest()` carries forward the keys it does not own, and
  `inventory.py` recomputes a missing baseline from `files.csv`.
  `inventory.py` will print its "file set did not change" banner; on a
  hotfix-only day that is the correct result, not a failure.
- **`build_db.py` cannot replace `wow.db` while `mcp_server.py` is running.**
  Windows holds the file open even for a read-only connection, `os.replace`
  fails with `WinError 5`, and the rebuilt database is left as `wow.db.tmp`.
  Stop the MCP server, then re-run.

### After the diff

`python scripts/check_findings.py --build <version>` re-tests the dated
predictions in the local findings record (`config.FINDINGS_DOC`,
`.claude/notes/state-log.md`). It is not part of the repo; without it the
script exits with a message. RESOLVED or FALSIFIED findings are updated there
by hand.

---

## Scope

Reading and diffing local client data for analysis. Not client modification,
not CASC writing, not redistribution of extracted assets.
