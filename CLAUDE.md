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
- [ ] Only one Forever build known locally, so diffing is not yet possible

---

## Target

| | |
|---|---|
| Game | World of Warcraft: Forever (Classic+) |
| Install root | see `scripts/config.py` (`INSTALL_ROOT`) |
| Flavor folder | `_classic_beta_` |
| TACT product code | `wow_classic_beta` |
| CDN path | `tpr/wow` |
| Beta started | 2026-09-17 |

Storage is **CASC** (not MPQ).

### Known builds

| Version | Build | Build config | CDN config |
|---|---|---|---|
| 1.60.1 | 69913 | `6c0df97e8e481a9a41600e373367c200` | `5525ea1ce6668e895569c89c2d6a154c` |

These hashes are the only way to reach a build after Blizzard rotates it off the
live version list. Capture them every patch day, before anything else.

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

`1.60` collides with nothing. A build belongs to Forever if and only if:

```
version matches  ^1\.6\d\.       (tolerant of a future 1.61 content patch)
AND buildId >= 69900
```

Anything else on this product is a **different game**. Discard it silently.
Never diff across the boundary — comparing 1.60 against 5.5 produces meaningless
noise. `diff_builds.py` must refuse to run if either build fails this filter.

Do **not** filter on date alone; wago.tools backfills and re-indexes older
builds. Build IDs are globally monotonic across all Blizzard products and are
the reliable secondary check.

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

---

## Key facts

- **FDIDs share the retail namespace.** `wowdev/wow-listfile` applies directly.
  No Forever-specific listfile exists.
- **DB2 layouts are Classic-flavor, not retail.** WoWDBDefs resolves via
  layouthash, but Forever is new — expect unknown columns in new tables. Name
  them `unk_<offset>`; never guess a meaning in committed output.
- **Naming is not the gap; typing is.** This build contains **1,441,771**
  files, and **every one of them has a listfile name** — zero unnamed.
  (2,274,258 is the *listfile's* total size across the whole retail FDID
  namespace, not a count for this build. Do not conflate the two.)
  But **99,348 files had no `content_type`**, and a magic-byte pass
  recovered **87,506** of them — 79,784 as `wmo_or_adt` — leaving 11,842
  still unknown. **Magic-byte classification is valuable and worth
  maintaining.** What is not worth chasing is filename recovery.
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
