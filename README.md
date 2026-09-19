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
5. `.\scripts\run-wtl.ps1`, then extract DBCs for the new build via the builds
   page.
6. Run the inventory and DB2 extraction. *(`inventory.py` / `extract_db2.py`
   are not written yet.)*
7. Diff against the previous Forever build. *(`diff_builds.py` not written
   yet.)*
8. Commit `builds.json` and the new report.

⚠️ On WTL's diff page, the **Manual build** box defaults to product `wow`. It
must be `wow_classic_beta`. Invalid config hashes crash WTL outright.

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
| `extract_db2.py` | ⬜ | WTL HTTP → CSV per table |
| `inventory.py` | ⬜ | File listing + magic-byte classification |
| `diff_builds.py` | ⬜ | Compare two build dirs, emit markdown |

Both completed Python scripts are idempotent and safe to re-run.
`fetch_builds.py` only ever adds entries to `builds.json`, never overwrites
one; `sync_refs.py` skips the 150 MB listfile download when upstream is
unchanged.

---

## Scope

Reading and diffing local client data for analysis. Not client modification,
not CASC writing, not redistribution of extracted assets.
