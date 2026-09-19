# WoW Forever Datamining

Local pipeline for extracting, parsing, and diffing World of Warcraft: Forever
client data across beta builds.

---

## Current state

WTL (wow.tools.local) builds and runs, and has successfully loaded the current
build from local CASC. CASC reading is **verified working** — do not re-litigate
this layer.

Outstanding:
- [ ] DB2s not yet extracted in WTL (the "DBCs missing, extract?" action)
- [ ] DBD directory not configured — WTL is using the remote manifest
- [ ] `builds.json` not yet seeded
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
`vendor/wow.tools.local` to find it.** Never guess route shapes.

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

## Key facts

- **FDIDs share the retail namespace.** `wowdev/wow-listfile` applies directly.
  No Forever-specific listfile exists.
- **DB2 layouts are Classic-flavor, not retail.** WoWDBDefs resolves via
  layouthash, but Forever is new — expect unknown columns in new tables. Name
  them `unk_<offset>`; never guess a meaning in committed output.
- **Forever has a graphical update over Classic.** Expect new and renamed model
  and texture paths the listfile does not yet cover. Unnamed files are normal,
  especially in early builds.
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
│   ├── extract_db2.py       # WTL HTTP -> CSV per table
│   ├── inventory.py         # file listing + magic-byte classification
│   └── diff_builds.py       # compare two build dirs, emit markdown
├── out/                     # GITIGNORED — extracted data
│   └── <version>.<build>/
│       ├── db2/*.csv
│       ├── files.csv        # fdid, path, size, encrypted, content_type
│       └── manifest.json    # row counts, layouthashes, metrics
├── reports/                 # COMMITTED — diff output
│   └── <from>_to_<to>.md
└── vendor/                  # GITIGNORED — cloned third-party tools
```

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
- **Cap concurrency at ~8.** CASC reads are IO-bound; oversubscribing thrashes
  the disk.

---

## Patch-day checklist

1. Let Battle.net finish patching, then **launch the game client once** so the
   local CASC index is complete. Skipping this yields partial extractions.
2. Close WoW and idle Battle.net.
3. Run `fetch_builds.py` — capture the new `buildConfig` / `cdnConfig` into
   `builds.json` **before anything else**. Unrecoverable if skipped.
4. Run `sync_refs.py`.
5. Start WTL, extract DBCs for the new build.
6. Run `inventory.py` and `extract_db2.py`.
7. Diff against the previous Forever build.
8. Commit `builds.json` and the new report.

---

## Scope

Reading and diffing local client data for analysis. Not client modification,
not CASC writing, not redistribution of extracted assets.
