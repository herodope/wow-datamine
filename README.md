# WoW Forever Datamining

A local pipeline that extracts, parses and diffs *World of Warcraft: Forever*
(Classic+) client data across beta builds, and turns the result into readable
patch notes.

Everything runs on your own machine against your own game install. Nothing is
uploaded, and no game files are redistributed — the extracted client data
(`out/`) is gitignored and reproducible from `builds.json`.

**The deliverable is `reports/`.** Raw extraction is a means to that end:

- [`reports/1.60.1.69893_to_1.60.1.69913.md`](reports/1.60.1.69893_to_1.60.1.69913.md) — a build-to-build diff
- [`reports/hotfix_1.60.1.69913.md`](reports/hotfix_1.60.1.69913.md) — what Blizzard changed without shipping a patch
- `reports/patchnotes_*.html` — the same data rendered as standalone patch notes

You do not need to run anything to read those.

> `CLAUDE.md` is the project's working context, written for Claude Code — locked
> decisions, measured findings, and traps discovered the hard way. It is long
> and reference-shaped. This file is the operational how-to; start here.

---

## Prerequisites

| | |
|---|---|
| OS | Windows — the pipeline drives a local WoW install and a PowerShell launcher |
| .NET SDK | **10.x** — `wow.tools.local` targets `net10.0`. Runtimes alone are not enough; check with `dotnet --list-sdks` |
| Python | 3.10+ |
| git | for the vendored repos and their submodules |
| WoW | a Battle.net install with the `_classic_beta_` flavor (product `wow_classic_beta`) |
| RAM | 3–5 GB free while WTL is running; more for DB2 global search |
| Disk | the listfile alone is ~150 MB, and `out/` grows quickly |

### Python dependencies

The pipeline is deliberately **stdlib-only** — extraction, diffing, enrichment,
the SQLite build, the HTML renderer and the query CLI use nothing but the
standard library. There is exactly one third-party import in the repo, and it
is confined to the optional MCP server:

```powershell
pip install -r requirements.txt   # only needed for scripts/mcp_server.py
```

Skip it if you are not using the MCP server. Extraction, diffing and reporting
run on a bare Python.

### Pointing the pipeline at your install

Paths and the product code live in exactly one place:
[`scripts/config.py`](scripts/config.py). It defaults to the stock Battle.net
location. If your install is elsewhere, set an environment variable rather than
editing the file:

```powershell
$env:WOW_INSTALL_ROOT = 'D:\Games\World of Warcraft'
```

`INSTALL_ROOT` is the folder containing `.build.info`, **not** the
`_classic_beta_` flavor folder. WTL needs the same path in its own
`config.json` (below) — if you set one, set both.

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
object. Missing keys fall back to defaults.

Note the doubled backslashes — these are JSON string escapes, and a single
backslash makes the file invalid:

```json
{
  "config": {
    "wowFolder": "C:\\Program Files (x86)\\World of Warcraft",
    "wowProduct": "wow_classic_beta",
    "region": "us",
    "locale": "enUS",
    "definitionDir": "C:\\path\\to\\wow-datamine\\vendor\\WoWDBDefs\\definitions"
  }
}
```

Substitute your own install root and repo location. Four things worth getting
right:

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

## Patch day

Steps 1–2 are manual and yours:

1. Let Battle.net finish patching, then **launch the game client once** so the
   local CASC index is complete. Skipping this yields partial extractions.
2. Close WoW and idle Battle.net.

Then one command runs steps 3–10:

```powershell
python scripts/patchday.py             # the whole sequence
python scripts/patchday.py --dry-run   # walk the plan, change nothing
python scripts/patchday.py --push      # also push the final commit
python scripts/patchday.py --build 1.60.1.69913   # resume a failed run
```

It stops on the first failure and tells you what to fix; earlier steps are
idempotent, so re-running is safe. Two steps still need you — starting WTL, and
extracting DBCs in the WTL UI — and it pauses and waits at each.

<details>
<summary>What those steps are, if you would rather run them by hand</summary>

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
7. Extract DBCs for the new build via the WTL builds page. Manual.
8. Extract and index:
   ```powershell
   python scripts/extract_db2.py          # every table, twice: plain + hotfixed
   python scripts/inventory.py            # files.csv + the encrypted-file count
   python scripts/extract_gametables.py   # tab-separated scaling curves
   python scripts/build_db.py             # fold the CSVs into out/<build>/wow.db
   ```
   `extract_db2.py` is resumable — interrupt and re-run to continue. A **drop**
   in the encrypted-file count means keys leaked or content unlocked.
9. Diff. Client changes and live changes are separate questions:
   ```powershell
   python scripts/diff_builds.py 1.60.1.69893 1.60.1.69913   # what the patch changed
   python scripts/diff_hotfixes.py                           # what changed without a patch
   python scripts/render_patchnotes.py --from 1.60.1.69893 --to 1.60.1.69913
   python scripts/check_findings.py                          # re-test recorded predictions
   ```
10. Commit `builds.json` and the new reports.

</details>

⚠️ On WTL's diff page, the **Manual build** box defaults to product `wow`. It
must be `wow_classic_beta`. Invalid config hashes crash WTL outright.

---

## Querying the extracted data

`build_db.py` folds a build's CSVs into `out/<build>/wow.db`, a read-only
SQLite query surface. The CSVs stay the source of truth; the database is
rebuildable in ~20 seconds.

```powershell
python scripts/query.py "SELECT ID, Name_lang FROM SpellName LIMIT 5"
python scripts/query.py --tables spell        # tables matching 'spell'
python scripts/query.py --schema SpellEffect  # columns, and which are indexed
python scripts/query.py -f q.sql --json
```

Three table prefixes carry the whole schema convention:

| Prefix | Source | Meaning |
|---|---|---|
| *(none)* | `db2_hotfixed/` | **live** — shipped client plus Blizzard's hotfixes |
| `plain_` | `db2/` | **as shipped** in the client |
| `gt_` | `gametables/` | tab-separated GameTables — not DB2s |

`enrich.py` turns IDs into readable context (foreign keys resolved to names,
enums and flags decoded) and can be run directly:

```powershell
python scripts/enrich.py Achievement 9275
```

### MCP server

[`scripts/mcp_server.py`](scripts/mcp_server.py) exposes the same database
read-only over stdio, so an MCP client can ask questions without shelling out
to `query.py`. Four tools: `list_tables`, `describe_table`, `query`,
`get_conventions`. It is the one thing in the repo needing
`pip install -r requirements.txt`. Setup instructions are in its module
docstring and in `CLAUDE.md`.

Read-only is enforced by the driver, not by inspecting SQL: the connection is a
`file:...?mode=ro` URI and `ATTACH` is denied by an authorizer, so a query
cannot pull in a second, writable database.

There is also a Claude Code skill at
[`.claude/skills/wow-query/SKILL.md`](.claude/skills/wow-query/SKILL.md)
carrying the schema conventions and the join paths that stop a query returning
a confident wrong answer.

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
├── gametables/*.txt    tab-separated per-level scaling curves
├── hotfixes.csv        push IDs + changed rows from Cache/ADB/enUS
├── files.csv           fdid, path, size, encrypted, content_type
├── wow.db              SQLite query surface over all of the above
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
version matches ^1\.6\d\.   AND   buildId >= 69876
```

**The version regex is the discriminator.** Measured against WTL's build list,
the version lines that have ever lived on this product code are 1.13, 1.60,
2.5, 3.4, 4.4 and 5.5 — only 1.60 is Forever, and nothing else comes close.

The build-ID floor is a *sanity check* for a future 1.6x collision, not a
second filter, and it fails loudly rather than quietly. It was set to a round
69900 for four days, which silently discarded builds 69876 and 69893 — both
real. If it ever seems to need raising, something is wrong; lower it to match
reality instead.

Do not filter on date: wago.tools backfills and re-indexes older builds.

This rule lives in `config.is_forever_build()`. Use it rather than
reimplementing the check.

---

## What is committed

```
builds.json    manifest index, keyed by buildId — the unrecoverable bit
scripts/       the pipeline
reports/       diff output, the actual product
docs/          WTL API reference
CLAUDE.md      project context and locked decisions (agent-facing)
README.md      this file
```

`out/` (extracted client data) and `vendor/` (third-party clones) are
gitignored. Both are reproducible; `builds.json` is not — once Blizzard rotates
a build off the version list, its config hashes are gone.

### Scripts

| Script | Purpose |
|---|---|
| `config.py` | Product code, paths, build filter — **single source of truth** |
| `patchday.py` | Runs the whole patch-day sequence, steps 3–10 |
| `fetch_builds.py` | Poll the version endpoint, merge Forever builds into `builds.json` |
| `sync_refs.py` | Clone/refresh WoWDBDefs, listfile, TACTKeys; download the listfile CSV |
| `run-wtl.ps1` | Launch WTL from the correct working directory |
| `extract_db2.py` | WTL HTTP → CSV per table, plain and hotfix-applied; resumable, writes `manifest.json` |
| `extract_gametables.py` | Extract GameTables to `out/<build>/gametables/` |
| `inventory.py` | File listing + magic-byte classification; writes `files.csv` |
| `build_db.py` | Load a build's CSVs into `out/<build>/wow.db` |
| `query.py` | Read-only SQL against `wow.db`, formatted for reading |
| `mcp_server.py` | The same database over MCP stdio (needs `requirements.txt`) |
| `enrich.py` | Resolve IDs to names, enums and flags; imported by the reporters |
| `contamination.py` | Detect retail-era data that leaked into a Classic+ build |
| `diff_builds.py` | Compare two builds' shipped DB2s, emit markdown |
| `diff_hotfixes.py` | Compare `db2/` against `db2_hotfixed/` within one build |
| `render_patchnotes.py` | Render either diff as standalone HTML patch notes |
| `check_findings.py` | Re-test the predictions recorded in `CLAUDE.md` against a build |

Every script is idempotent and safe to re-run. `fetch_builds.py` only ever adds
entries to `builds.json`, never overwrites one; `sync_refs.py` skips the 150 MB
listfile download when upstream is unchanged.

---

## Scope and limits

Reading and diffing **local** client data for analysis, from a client you have
installed yourself. Not client modification, not CASC writing, not bulk
redistribution of extracted assets — `out/` is gitignored precisely so that
the extracted client data never lands in the repository.

The committed reports quote game strings (item names, spell text, NPC
dialogue) where quoting them is what makes a diff readable. All extracted data
and every game asset referenced here remain the property of Blizzard
Entertainment; this project is unaffiliated with and unendorsed by Blizzard.

### Built on

- [wow.tools.local](https://github.com/Marlamin/wow.tools.local) — the extraction engine
- [WoWDBDefs](https://github.com/wowdev/WoWDBDefs) — DB2 structure definitions
- [wow-listfile](https://github.com/wowdev/wow-listfile) — community filename listfile
- [TACTKeys](https://github.com/wowdev/TACTKeys) — known encryption keys
