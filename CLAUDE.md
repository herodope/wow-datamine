# WoW Forever Datamining

Local pipeline for extracting, parsing, and diffing World of Warcraft: Forever
client data across beta builds.

---

## Current state

WTL (wow.tools.local) builds and runs, and has successfully loaded the current
build from local CASC. CASC reading is **verified working** — do not re-litigate
this layer.

Outstanding:
- [x] DB2s extracted in WTL
- [x] DBD directory configured — `definitionDir` points at
      `vendor/WoWDBDefs/definitions`, no longer the remote manifest
- [x] `builds.json` seeded with 1.60.1.69913 (5a6cc43)
- [x] Build filter corrected — `FOREVER_MIN_BUILD_ID` lowered to 69876 and
      near misses now warn loudly instead of being dropped. See
      **CRITICAL: build filtering**
- [ ] **Two earlier Forever builds are known but not extracted.** 69876 and
      69893 (hashes under **Known builds**) now pass the filter and are absent
      from `builds.json` and `out/`. Both are off the live version list, so
      they must be reached through their recorded `buildConfig`/`cdnConfig`,
      which WTL's manual-build path can load. **Extracting either one makes
      the first real diff possible** — the project's stated deliverable, and
      the thing listed as blocked since the repo was created.

---

## Target

| | |
|---|---|
| Game | World of Warcraft: Forever (Classic+) |
| Install root | see `scripts/config.py` (`INSTALL_ROOT`) |
| Flavor folder | `_classic_beta_` |
| TACT product code | `wow_classic_beta` |
| CDN path | `tpr/wow` |
| Beta announced | 2026-09-17 — the **public** start |
| First build on CDN | 2026-09-16 18:23 UTC (build 69876) |

Storage is **CASC** (not MPQ). Builds predate the announcement by about a day:
69876 and 69893 were both pushed on 09-16. Do not use 2026-09-17 as a lower
bound when looking for builds — it is a press date, not a data date.

### Known builds

| Version | Build | First seen | Build config | CDN config |
|---|---|---|---|---|
| 1.60.1 | 69876 | 2026-09-16 18:23 | `e7fab7248766e9e7daddb3b6083c9c3c` | `272d201d5b2d6fec8fdb4aa59b2a9eea` |
| 1.60.1 | 69893 | 2026-09-16 23:14 | `5aa0eecfa8d49f5ad01221dbc8601144` | `c39a363b4a67449d8f16736dba987a43` |
| 1.60.1 | 69913 | 2026-09-18 03:02 | `6c0df97e8e481a9a41600e373367c200` | `5525ea1ce6668e895569c89c2d6a154c` |

These hashes are the only way to reach a build after Blizzard rotates it off the
live version list. Capture them every patch day, before anything else.

69876 and 69893 come from WTL's archive (`GET /build/list`) and are **already
off the live version list** — measured 2026-09-20, the versions endpoint
returns only 69913, once per region. They are reachable now solely because WTL
recorded them.

> **A `cdnConfig` rotates independently of the build.** WTL recorded
> `1f946798ccc0e9281f9ac94e3539ada9` for 69913 on 2026-09-18; the live
> endpoint served `5525ea1ce6668e895569c89c2d6a154c` for the same build on
> 2026-09-20. Both are real. The `buildConfig` is the stable identity; treat a
> changed `cdnConfig` for an unchanged `buildConfig` as normal, and keep
> whichever you captured rather than assuming one supersedes the other.

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

**Why this matters more than the threshold.** The gate was `69900` until
2026-09-20 — a round number chosen below the only build then known. It rejected
builds **69876 and 69893**, which are real Forever builds, and it did so on the
same code path and with the same silence as a Mists of Pandaria beta build. The
two were invisible for four days, and with them the possibility of producing
the first diff, which is this project's deliverable. They have since rotated
off the live version list and survive only in WTL's archive.

The defect was never the number. It was that a near miss and a different game
were indistinguishable in the output. **If the threshold ever needs raising,
something is wrong — lower it to match reality instead**, capture the build's
`buildConfig`/`cdnConfig` immediately, and note that a near miss on a *higher*
ID than anything seen is impossible by construction.

> **The `>= 69900` half of this rule is measurably wrong and excludes real
> Forever builds.** ✅ measured 2026-09-20 against `/build/list`: three builds
> match `^1\.6\d\.` — 69876, 69893 and 69913 — and the threshold rejects the
> first two. The version lines present on `wow_classic_beta` are 1.13, 1.60,
> 2.5, 3.4, 4.4 and 5.5, so the version pattern already discriminates
> completely; the build-ID gate adds only false negatives, and it silently
> discarded the two builds that would have made the first diff possible.
>
> The threshold was chosen as a round number below 69913 before any earlier
> build was known to exist. The safe correction is to lower
> `FOREVER_MIN_BUILD_ID` to **69876** — keeping a secondary check against a
> future 1.6x collision while admitting every build actually observed — rather
> than deleting the gate. **That is a decision for the repo owner**, and
> `scripts/config.py` is unchanged pending it. Until then, `diff_builds.py`
> will refuse both earlier builds, which is the documented behaviour working
> as specified against a specification that is wrong.

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
  This exact wrong inference was made here once and committed.
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

  The mechanism is worth knowing because it constrains what the filter can do.
  `BuildRange.Contains` compares **componentwise**, not lexicographically:
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
  (An earlier revision of this file said the opposite — omit `build=` — from
  reading `WeatherType.dbde`'s six build-tagged lines without noticing the six
  untagged ones below them, and without checking a live response. Corrected
  2026-09-20 against a running instance.)
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
of the highest-value early signals in beta datamining. Log it every build.

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

`scripts/contamination.py` runs as part of the hotfix report. Rules, ranked by
how much they actually discriminate:

| Rule | Confidence | What it catches |
|---|---|---|
| `dangling_map_ref` | HIGH | A row references a Map ID absent from this build. Exactly 1 of 233 `Achievement` rows hits this — near-zero false positives |
| `light_absent_map` | HIGH | A `LightParams` ID whose only referencing `Light` rows sit on absent maps |
| `orphan_removal` | MEDIUM | Rows pulled together in one push that carry no supporting display data |

**Two rules deliberately not implemented**, because measurement showed they do
not discriminate in this build:

- **ID falls in a "modern retail range".** `Achievement` IDs run 627–64159 with
  160 of 233 rows above 61000, so an ID-range test flags most of the table. ID
  ranges are supporting evidence only, never a trigger.
- **Item row has no ItemSparse/ItemSearchName data.** 8,286 of 31,675 `Item`
  rows lack display data, because `ItemSparse` ships incomplete and arrives by
  hotfix. Orphanhood alone would flag a quarter of the table. What is
  suspicious is a **coordinated removal** of orphans in a single push.

### Known cases in 1.60.1.69913

| Record | Evidence | Push |
|---|---|---|
| `Achievement` 9275 — "Warlord Zaela kills (Upper Blackrock Spire)" | `Instance_ID` 1358, a map not in this build. Warlord Zaela is a Warlords of Draenor boss. Removed together with its `Achievement_Category` 15233 | 112039, invalidated |
| `LightParams` 453 | Referenced only by `Light` 16161 on map 3064, which does not exist here. Replaced by 7641 (Kalimdor) in `Light` 269 | 112132, valid |
| 75 `Item` stubs | All `ClassID` 4 / `SubclassID` 0 — 32 trinkets, 23 rings, 20 necks — with no display data, pulled in one push | 112078, invalidated |

The 75 IDs, so this can be checked exactly rather than by population count
(`scripts/check_findings.py` reads them from here):

```
833, 942, 1315, 1443, 1447, 1980, 2246, 5004, 5005, 5010, 7549,
7550, 7551, 13001, 13002, 13089, 13091, 13096, 14557, 14558, 17063,
17065, 17082, 17108, 17109, 17110, 17982, 19856, 19863, 19871,
19873, 19876, 19885, 19893, 19898, 19905, 19912, 19920, 19923,
19925, 19930, 19947, 22721, 22722, 209681, 209816, 211420, 211449,
211450, 211451, 213347, 213348, 213349, 213350, 215461, 220632,
220633, 220634, 223327, 227967, 227972, 228432, 228464, 228465,
228466, 228467, 228523, 228576, 228589, 228599, 228678, 228722,
236784, 279834, 282019
```

Counting `ClassID` 4 / `SubclassID` 0 orphans instead would measure the wrong
thing: there are **1,128** such rows in 1.60.1.69913, and orphanhood is normal
here. Check these IDs; keep the population figure as context only.

---

## Findings to verify

Dated predictions from 1.60.1.69913, recorded **2026-09-20** so the next build
can confirm or kill them. Each entry states what was observed, what would
confirm it, and what would falsify it. **Resolve these before adding new ones**
— an unresolved prediction is worth more than a new guess.

### 1. A weekly event schedule starting 12 October 2026

`TimeEventData` is **hotfix-only** (ships empty, push 112079) and holds exactly
three rows:

| ID | TimeEventID | Timestamp | UTC |
|---|---|---|---|
| 25930 | 3162 | 1791824400 | 2026-10-12 17:00 |
| 25943 | 3163 | 1792429200 | 2026-10-19 17:00 |
| 25956 | 3164 | 1793034000 | 2026-10-26 17:00 |

Exactly 7 days apart, sequential event IDs, same region group (5). 17:00 UTC is
Blizzard's usual test-window slot.

**Watch:** whether the dates shift, whether a fourth row appears (extending the
cadence), and whether the table ships populated in the client rather than
arriving by hotfix. A shifted date means the schedule slipped; a fourth row
means the cadence is ongoing rather than a three-week run.

### 2. A shard/world mechanic being repositioned

Three `GlobalStrings` rows changed under one push (112128), all the same rename:

```
60077  "Transfer Now"                                  -> "Refresh Now"
60078  "Your character will be transferred to another  -> "The world around you will
        shard in %s %s."                                   refresh in %s %s. ..."
60175  "Transfer to a new shard now."                  -> "Refresh the world now."
```

Nothing else in `GlobalStrings` changed. This is user-facing wording for a live
mechanic being settled *after* the build shipped, which suggests the system
itself is still being positioned.

**Watch:** supporting UI strings using "refresh" language, new tables or columns
for the mechanic, and whether "shard" wording survives anywhere. If the rename
is cosmetic, expect nothing further; if the mechanic is being reworked, expect
more strings and possibly a new table.

### 3. The vanilla PvP rank ladder arrived by bulk injection

1,385 modern-ID `ItemSparse` additions, of which **481 carry vanilla PvP rank
titles** across both factions (Blood Guard 49, Knight-Lieutenant 47, General 43,
Warlord 42, Marshal 40, Field Marshal 39, Grand Marshal 26, High Warlord 26,
Stone Guard 20, Sergeant Major 20, …). 689 of the modern-ID additions require
level 60. All arrived with **synthetic push IDs**, i.e. one bulk load rather
than incremental authoring.

**Watch:** whether these ship in the client next build instead of arriving by
hotfix. Shipping in the client means the feature is settled; arriving by hotfix
again means it is still being staged. Also watch whether the ladder grows —
the vanilla honor system has 14 ranks per faction, so an incomplete set now
implies more to come.

### 4. LightData column 055 — LEANING FALSIFIED, do not assert

Twelve `LightData` rows (74945–74956, `LightParamID` 7742, Kalimdor) covering
the complete day cycle each changed `Field_1_60_1_69876_055` from `0` to
`13533183`, under push 112132.

`13533183` = `0xCE7FFF` = bytes (206, 127, 255), which is *consistent with* a
packed RGB value — a light lavender. **This is a guess and must not be stated
as fact.** The column is unnamed in WoWDBDefs for this build, and per the
conventions above an unknown column's meaning is never asserted in committed
output.

**Evidence against it, added 2026-09-20.** WoWDBDefs' `meta/mapping.dbdm`
marks 23 `LightData` columns as `COLOR`, and `Field_1_60_1_69876_055` is not
among them. On its own that would be weak — the meta tree might simply not
cover unnamed columns. It does: four of the 44 `COLOR` mappings are on
**unnamed columns in this very build**, under the same generated naming
scheme —

```
COLOR LightDataGlobalVolumeFog::Field_1_60_1_69876_001
COLOR LightDataGlobalVolumeFog::Field_1_60_1_69876_002
COLOR LightDataGlobalVolumeFog::Field_1_60_1_69876_003
COLOR LightDataGlobalVolumeFog::Field_1_60_1_69876_004
```

So contributors have gone through Forever's unnamed light columns and tagged
the ones they read as colours, and did not tag this one. That is a negative
signal, not merely absence of evidence — hence **leaning falsified**. It is
not conclusive: the four tagged columns are in a different table, and nobody
may have worked through `LightData`'s unnamed columns at all.

**The gate is unchanged.** The reading becomes reportable only if WoWDBDefs
names the column as a colour in a later definition sync — the meta mapping
does not make it reportable now, and the absence does not make it refutable in
committed output either. If named as something other than a colour, discard
the reading entirely.

**Watch:** whether a definition sync names `LightData::Field_1_60_1_69876_055`,
and whether `mapping.dbdm` gains a `COLOR` entry for it. Re-check both after
every `sync_refs.py` run.

### 5. Three open retail-contamination cases

See the section above for evidence. All three are open as of 1.60.1.69913:

| Record | Status |
|---|---|
| `Achievement` 9275 (Warlord Zaela, WoD) + category 15233 | removed by hotfix, still in the shipped client |
| `LightParams` 453 (map 3064) | replaced by hotfix, still in the shipped client |
| 75 `Item` stubs (ClassID 4 / SubclassID 0) | removed by hotfix, still in the shipped client |

Each was pruned *live* but remains in the client data, so the question is
whether 1.60.2 ships without them.

**Watch:** whether each is gone from the shipped DB2s in the next build — that
confirms the hotfix was a stopgap ahead of a real fix — and whether new
contamination appears. `scripts/contamination.py` reports this automatically;
a *new* HIGH-confidence finding is the thing to look at.

### 6. The encrypted-count detector is untested and reports MATCH

`inventory.py` compares the encrypted-file count against a hardcoded baseline
of **5035** and prints `MATCH` or `DIFFERS`. It has now passed on three builds:

| Build | files | encrypted | UnknownKey | ButNot | files.csv SHA-256 |
|---|---|---|---|---|---|
| 69876 | 1,441,771 | 5,035 | 3,371 | 1,664 | `86733ec34aee…` |
| 69893 | 1,441,771 | 5,035 | 3,371 | 1,664 | `86733ec34aee…` |
| 69913 | 1,441,771 | 5,035 | 3,371 | 1,664 | `86733ec34aee…` |

**All three file sets are byte-identical**, so the detector has never been
shown data that could make it fail. Three passes is one observation repeated
three times. CLAUDE.md calls a drop in this count "one of the highest-value
early signals" — on the evidence so far it is an **untested detector reporting
MATCH**, which is precisely the category the identical-hash check in
`inventory.py` was in until it was pointed at real data and turned out to fail
on every correct run.

A `MATCH` is only a measurement if the file set moved and the encrypted count
did not. If neither moved, `MATCH` carries no information about encryption at
all — it is restating that the build did not change.

**The first real test is 1.60.2.** What to record when it lands:

- whether `files.csv` differs from 69913's at all. If it does not, the
  encrypted result is still untested and must be reported as such rather than
  as a pass.
- whether the encrypted total moved, and **which way**. A drop means keys
  leaked or content unlocked; a rise means new encrypted content shipped.
- the `EncryptedUnknownKey` / `EncryptedButNot` split separately from the
  total. The two can move in opposite directions and cancel — 3,371 / 1,664
  summing to 5,035 could become 3,300 / 1,735 with the total unchanged, and a
  total-only check reports `MATCH` through a real key release.

**Change needed either way:** `inventory.py` should report whether the file set
changed **alongside** the encrypted count, so a passing result cannot be read
as a measurement when it isn't. The identical-inventory warning already
computes the hash needed for this; the two should be reported together rather
than as unrelated lines. The 5035 baseline should also stop being a magic
number in the script — it belongs with the other per-build baselines, or it
should be read from the previous build's manifest so it tracks reality instead
of a constant frozen at 1.60.1.69913.

---

## Key facts

- **FDIDs share the retail namespace.** `wowdev/wow-listfile` applies directly.
  No Forever-specific listfile exists.
- **DB2 layouts are Classic-flavor, not retail.** WoWDBDefs resolves via
  layouthash, but Forever is new — expect unknown columns in new tables. Name
  them `unk_<offset>`; never guess a meaning in committed output.
- **Typing is the bigger gap, but naming is not zero.** This build contains
  **1,441,771** files. **19,583 of them (1.4%) have an empty name** despite
  being members of `Listfile.NameMap` — of those, 11,842 remain `unk` and
  7,721 were classified as `blp` by the magic-byte pass. An earlier revision
  of this file claimed zero unnamed files; that was wrong, and the mistake is
  recorded as a gotcha below because the API makes it easy to repeat.
  (2,274,258 is the *listfile's* total size across the whole retail FDID
  namespace, not a count for this build. Do not conflate the two — though
  note **2,274,258 − 2,254,675 = 19,583**, exactly the empty-name count, which
  is what the discrepancy between the listfile release's row count and the
  named-file figure was all along.)
  On typing: **99,348 files had no `content_type`**, and a magic-byte pass
  recovered **87,506** of them — 79,784 as `wmo_or_adt` — leaving 11,842
  still unknown. **Magic-byte classification is valuable and worth
  maintaining.** Filename recovery is the lower-value half, but it is not a
  solved problem either.
- **Hotfixes are a separate diffable source.** WTL loads live hotfix data
  from the client's `Cache/ADB/enUS` directory and tracks push IDs. This is
  distinct from the static DB2s shipped in the build: hotfixes are Blizzard
  tuning live data between client patches. Diffing them answers a different
  question than diffing DB2s, and the two must not be conflated.
- **Encryption.** Blizzard withholds Salsa20 keys for unreleased content.
  Encrypted files fail to decode until keys land in `TACTKeys`. Always
  skip-and-log, never error the run.

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
├── README.md
├── builds.json              # manifest index — buildConfig/cdnConfig per build
├── scripts/
│   ├── config.py            # product code, paths, build filter — SINGLE SOURCE
│   ├── run-wtl.ps1          # launch WTL from the correct working directory
│   ├── sync_refs.py         # pull WoWDBDefs / listfile / TACTKeys
│   ├── fetch_builds.py      # poll version endpoint, update builds.json
│   ├── extract_db2.py       # WTL HTTP -> CSV per table, plain + hotfixed
│   ├── inventory.py         # file listing + magic-byte classification
│   └── diff_builds.py       # compare two build dirs, emit markdown
├── out/                     # GITIGNORED — extracted data
│   └── <version>.<build>/
│       ├── db2/*.csv           # plain DB2s, as shipped in the build
│       ├── db2_hotfixed/*.csv  # same tables with the hotfix overlay applied
│       ├── hotfixes.csv        # push IDs + changed rows from Cache/ADB/enUS
│       ├── files.csv           # fdid, path, size, encrypted, content_type
│       └── manifest.json       # row counts, layouthashes, metrics
├── reports/                 # COMMITTED — diff output
│   └── <from>_to_<to>.md
└── vendor/                  # GITIGNORED — cloned third-party tools
```

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

  This is not hypothetical. The `build=` rule on `/dbc/meta/getMappings` was
  derived correctly from `BuildRange.Contains` — componentwise comparison,
  1.60.x matches no preset range — committed as though verified, and was
  **backwards**. The real behaviour is that omitting `build=` returns two
  conflicting enum variants with the retail one first, so a decoder taking the
  first match would have labelled every Forever weather row with **retail**
  names: `0 None, 1 Clear, 2 Rain` instead of `0 Clear, 1 Rain, 2 Snow`. Wrong
  data, plausible output, no error — the same shape as every other gotcha in
  the WTL section, arrived at by the documentation process itself. One HTTP
  request settled it. Corrected in `7575f2c`; the original is `7655839`.

  Practically: source-read a route to know what to ask for, then ask. When WTL
  is not running, write the finding down as unexercised and **promote it only
  after a response confirms it**. A hypothesis recorded honestly is useful;
  a hypothesis recorded as fact is a trap with this project's name on it.
- **A fix that works does not confirm the diagnosis that motivated it.**
  Symptom, fix and mechanism are three separate claims. A fix landing and the
  symptom going away establishes the first two. The **mechanism has to be
  measured on its own**, because a correct fix sitting next to a wrong
  explanation looks exactly like a correct fix sitting next to a right one —
  and it is the explanation that gets reused on the next problem.

  The worked example. Three builds produced byte-identical `files.csv`.
  `inventory.py` was found to read row indices `0, 1, 4, 5` and never index 3,
  `availableInBuild`, and the conclusion drawn — and stated as fact — was that
  every other column comes from the global listfile, so the output could not be
  build-specific and the encrypted-file count "cannot change no matter what
  Blizzard does". Two real defects were then fixed on that basis.

  The mechanism was wrong. `ListfileController.cs:208` already intersects
  `Listfile.NameMap` with `CASC.AvailableFDIDs` before paging, so the route
  **only ever returns in-build files** and ignoring column 3 changed nothing.
  Measured after the fix: `skipped_not_in_build = 0`, and the fixed script
  reproduced the original CSV **byte for byte** — same SHA-256,
  `86733ec34aee…`. Neither defect could have caused the symptom:

  | Claim | Status |
  |---|---|
  | `availableInBuild` was unread | **true**, and worth fixing for the `showAllFiles` case |
  | `without_name` used the forbidden subtraction | **true**, reported 0 against 19,583 |
  | Either caused the identical hashes | **false** — one was a no-op, the other never touched a row |
  | The file set is identical across all three builds | **true**, and it is the whole explanation |

  The identical inventories were real data: only 2 of 610 shipped DB2 tables
  differ across 69876/69893/69913, so an unchanged file set is exactly what to
  expect. An assertion was added to fail on identical hashes, on the assumption
  that identical meant broken; it fires on correct data and is a false positive
  by construction.

  **This is the second instance of source-read reasoning committed as fact**,
  after the `build=` meta-route rule (`7655839` → `7575f2c`). Both followed the
  same shape: read the code, build a mechanism that explained the symptom, skip
  the measurement because the reasoning felt tight, write it down as fact. The
  reasoning being *sound* is what makes it dangerous — it was sound and still
  wrong, because it rested on an unread line thirty lines away.

  Practically: when a symptom motivates a fix, **state the mechanism as a
  separate, testable claim and measure it separately.** "Does the fix remove
  the symptom" and "is my explanation of the symptom correct" are different
  experiments. Here the second one cost a single request — sampling
  `availableInBuild` over 20,000 rows and finding every value `true`.
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
  `Achievement` that way collapsed 114 of its 233 rows onto duplicate
  description strings and reported no change against a real 233 → 232
  delta. Always key on the column literally named `ID`, fall back to column 0
  only when absent, and state which key was used in the output. This applies
  to `diff_builds.py` as much as to `diff_hotfixes.py`.
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

  **Critically: a table with a schema change must stay in the report even when
  its magnitude is zero.** Suppressed field diffs leave added/removed/changed
  all at 0, and a "skip anything with magnitude 0" rule then silently drops the
  tables that most need flagging. This was a real bug in `diff_builds.py`,
  caught only because the test fixture included a schema change with no row
  changes. Report the change in the summary, in a dedicated section, and in the
  table's own section, stating which comparisons were suppressed and why.

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
8. Run `inventory.py` and `extract_db2.py`.
9. Diff against the previous Forever build.
10. Commit `builds.json` and the new report.

---

## Scope

Reading and diffing local client data for analysis. Not client modification,
not CASC writing, not redistribution of extracted assets.
