# WoW Forever Datamining

Local pipeline for extracting, parsing and diffing World of Warcraft: Forever
(Classic+) client data across beta builds.

The deliverable is the markdown diffs in `reports/` — raw extraction is a means
to that end. `CLAUDE.md` holds the project's context and locked decisions; this
file is the operational how-to.

---

## Prerequisites

| | |
|---|---|
| .NET SDK | **10.x** — `wow.tools.local` targets `net10.0`. Runtimes alone are not enough; check with `dotnet --list-sdks` |
| Python | 3.10+ — scripts use only the standard library, no `pip install` |
| git | for the vendored repos and their submodules |
| WoW | a Battle.net install with the `_classic_beta_` flavor (product `wow_classic_beta`) |
| RAM | 3–5 GB free while WTL is running; more for DB2 global search |
| Disk | the listfile alone is ~150 MB, and `out/` grows quickly |

Paths and the product code live in exactly one place: [`scripts/config.py`](scripts/config.py).
If your WoW install is somewhere else, change `INSTALL_ROOT` there and nowhere
else.

---

## First-time setup

```powershell
# 1. Reference data: WoWDBDefs, wow-listfile, TACTKeys + the listfile CSV
python scripts/sync_refs.py

# 2. The extraction engine
git clone --recurse-submodules https://github.com/Marlamin/wow.tools.local.git vendor/wow.tools.local
cd vendor/wow.tools.local
dotnet build -c Release
cd ../..
```

Then create `vendor/wow.tools.local/config.json`. WTL reads it from the
**working directory**, with every key nested under a top-level `"config"`
object. Missing keys fall back to defaults:

```json
{
  "config": {
    "wowFolder": "A:\World of Warcraft",
    "wowProduct": "wow_classic_beta",
    "region": "us",
    "locale": "enUS",
    "definitionDir": "A:\claude\projects\wow-datamine\vendor\WoWDBDefs\definitions"
  }
}
```

Four things worth getting right:

- **`wowFolder` is the install root, not the flavor folder.** WTL validates it
  by looking for `.build.info`, which sits at the root; `_classic_beta_` is
  selected by `wowProduct`.
- **`definitionDir` points at the `definitions` subdirectory**, not the
  WoWDBDefs repo root. WTL validates it by looking for `Map.dbd` inside, and
  other code resolves `definitionDir/../manifest.json` and
  `definitionDir/../meta/mapping.dbdm`. Point it at the repo root and
  validation fails; leave it empty and WTL silently falls back to downloading
  a remote manifest.
- **`region` defaults to `eu`.** Set it to match `.build.info`.
- **Run `sync_refs.py` before setting `definitionDir`** — the setting points at
  a clone that has to exist on disk first.

WTL rewrites `config.json` on every start, expanding it to the full key set in
its own order. That is normal and does not drop your values.

---

## Running WTL

```powershell
.\scripts\run-wtl.ps1
```

Then open **http://localhost:5080**.

**Close WoW and idle Battle.net first.** Both hold locks on the CASC data
files; WTL will otherwise fail to load the build or load it partially, without
a clear error.

The launcher exists to avoid one specific trap: WTL must run with
`vendor/wow.tools.local` as the working directory. `Program.cs` exits
immediately if `wwwroot` is not in the current directory, and `config.json` is
read from the current directory first — launch from elsewhere and WTL quietly
uses defaults (wrong product, wrong region, no DBD directory). Running the
built exe under `bin/Release/` directly needs its own copy of `config.json`
beside it.

WTL is a blocking server; it holds the terminal until Ctrl+C. First start
downloads definitions and the listfile and indexes the build — expect several
minutes.

---

## Patch-day sequence

1. Let Battle.net finish patching, then **launch the game client once** so the
   local CASC index is complete. Skipping this yields partial extractions.
2. Close WoW and idle Battle.net.
3. `python scripts/fetch_builds.py` — **do this before anything else.** It
   records the new build's `buildConfig` / `cdnConfig` into `builds.json`.
   Once Blizzard rotates a build off the live version list, those hashes are
   the only way to reach it again, and they are not recoverable after the fact.
4. `python scripts/sync_refs.py` — refresh WoWDBDefs, the listfile and
   TACTKeys. Must precede any use of `definitionDir`.
5. `.\scripts\run-wtl.ps1`.
6. **Call `GET /dbc/updateDefs`** — the "Update WoWDBDefs & clear cache"
   button. This must follow step 4: WTL caches definitions and will
   otherwise keep using the previous build's set even though `sync_refs.py`
   just refreshed them on disk. With a local `definitionDir` it downloads
   nothing and only reloads and clears.
   ```powershell
   curl.exe -s http://localhost:5080/dbc/updateDefs
   ```
7. Extract DBCs for the new build via the builds page.
8. `python scripts/extract_db2.py` — every table extracted **twice**, plain
   into `db2/` and hotfix-applied into `db2_hotfixed/`, with per-table results
   in `manifest.json`. Resumable: interrupt and re-run to continue. See
   [Hotfixes vs DB2s](#hotfixes-vs-db2s).
   Then `python scripts/inventory.py` for `files.csv` and the encrypted-file
   count — a **drop** in that count means keys leaked or content unlocked.
9. Diff against the previous Forever build. *(`diff_builds.py` not written
   yet.)*
10. Commit `builds.json` and the new report.

⚠️ On WTL's diff page, the **Manual build** box defaults to product `wow`. It
must be `wow_classic_beta`. Invalid config hashes crash WTL outright.

---

## WTL API gotchas

Scripts drive WTL over HTTP. [`docs/wtl-api.md`](docs/wtl-api.md) is the full
route reference — route, method, params and response shape, read from the
controller source and verified live. The traps that cause **silently wrong
output**:

| Gotcha | Consequence |
|---|---|
| The API parameter is `useHotfixes=true`; the browse page URL spells it `hotfixes=` | Sending `hotfixes=` returns 200 with **non-hotfixed** data — 6,622 vs 10,556 rows on `itemsearchname`. Never use the page's spelling in scripts |
| `/listfile/db2s` returns 1342 tables unfiltered, 1161 build-filtered | Iterate the filtered list or ~181 exports come back empty |
| Two DataTables envelope shapes | `/dbc/info` and `/dbc/data` have an `error` key; `/dbc/hotfixes/list`, `/listfile/files`, `/build/table` do not — `d["error"]` raises `KeyError` |
| `204` vs `404` | 204 = table defined but zero rows in this build; 404 = not in this build. Handle distinctly |
| The two variants can return different statuses | `TimeEventData` is 204 plain but 200 with rows hotfixed — it exists only as live data. Don't judge "empty" from the plain request alone |
| `/dbc/hotfixes/list` with no query string | Returns all zeros, looking like "no hotfixes". Pass `?length=N` |
| Default `locale` is `All_WoW`, not `enUS` | Pass `locale` explicitly |
| `type:unk` search returns 0 | `type:` looks up `Listfile.TypeMap`, which has no `unk` bucket, and degrades to a substring match. 99,348 files actually carry that type — read the `content_type` column, not the token |
| No per-file size over HTTP | `/size/data` aggregates only; real sizes need `Data/data/*.idx` parsed directly. `files.csv` leaves `size` empty |

---

## Hotfixes vs DB2s

Two different sources, answering two different questions:

- **DB2s** are the static tables shipped inside the build. A change here means
  Blizzard published a new client.
- **Hotfixes** are live tuning data pushed between client patches. WTL loads
  them from the client's `Cache/ADB/enUS` directory and tracks push IDs.

Because of this, every table is extracted twice — plain into `db2/`, and with
the hotfix overlay applied into `db2_hotfixed/`:

```
out/<version>.<build>/
├── db2/*.csv           plain DB2s, as shipped in the build
├── db2_hotfixed/*.csv  same tables with the hotfix overlay applied
├── hotfixes.csv        push IDs + changed rows from Cache/ADB/enUS
├── files.csv           fdid, path, size, encrypted, content_type
└── manifest.json       row counts, layouthashes, metrics
```

Each table in `manifest.json` resolves to one of `ok`, `empty`,
`hotfix_only` (204 plain but populated by hotfixes), `not_in_build` or
`error`, alongside both row counts, the layouthash, both HTTP statuses and a
`hotfix_delta` flag computed from a content hash — not a row count, since a
hotfix can change a value without changing the number of rows.

A value that moves only in `db2_hotfixed/` was hotfixed; one that moves in both
shipped in the build. Collapsing the two into a single output loses that
distinction permanently, so do not "simplify" it away.

---

## Build filtering

`wow_classic_beta` is a **recycled product code** — it has also hosted the
Classic 2019, Era/SoD, BC, Wrath, Cataclysm and MoP Classic betas. A build is
Forever if and only if:

```
version matches ^1\.6\d\.   AND   buildId >= 69900
```

Both halves matter: `1.60.0.69800` matches the version pattern but fails the
build-ID floor. Anything else on this product is a different game — discard it,
and never diff across the boundary. Do not filter on date; wago.tools backfills
and re-indexes older builds.

This rule lives in `config.is_forever_build()`. Use it rather than
reimplementing the check.

---

## What is committed

```
builds.json    manifest index, keyed by buildId — the unrecoverable bit
scripts/       the pipeline
reports/       diff output, the actual product
CLAUDE.md      project context and locked decisions
README.md      this file
docs/          WTL API reference
```

`out/` (extracted data) and `vendor/` (third-party clones) are gitignored. Both
are reproducible; `builds.json` is not.

### Scripts

| Script | Status | Purpose |
|---|---|---|
| `config.py` | ✅ | Product code, paths, build filter — **single source of truth** |
| `fetch_builds.py` | ✅ | Poll the version endpoint, merge Forever builds into `builds.json` |
| `sync_refs.py` | ✅ | Clone/refresh WoWDBDefs, listfile, TACTKeys; download listfile CSV |
| `run-wtl.ps1` | ✅ | Launch WTL from the correct working directory |
| `extract_db2.py` | ✅ | WTL HTTP → CSV per table, both plain and hotfix-applied; resumable, writes `manifest.json` |
| `inventory.py` | ✅ | File listing + magic-byte classification; writes `files.csv`, merges counts into `manifest.json` |
| `diff_builds.py` | ⬜ | Compare two build dirs, emit markdown |

The WTL routes these drive are documented in
[`docs/wtl-api.md`](docs/wtl-api.md).

Both completed Python scripts are idempotent and safe to re-run.
`fetch_builds.py` only ever adds entries to `builds.json`, never overwrites
one; `sync_refs.py` skips the 150 MB listfile download when upstream is
unchanged.

---

## Scope

Reading and diffing local client data for analysis. Not client modification,
not CASC writing, not redistribution of extracted assets.
