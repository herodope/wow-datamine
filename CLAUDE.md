# WoW Forever Datamining

Local pipeline for extracting, parsing, and diffing World of Warcraft: Forever
client data across beta builds.

---

## Target

| | |
|---|---|
| Game | World of Warcraft: Forever (Classic+) |
| Install root | `C:\Program Files (x86)\World of Warcraft` |
| Flavor folder | `_classic_beta_` |
| TACT product code | `wow_classic_beta` |
| CDN path | `tpr/wow` |
| Current build | `1.60.1.69913` |
| Beta started | 2026-09-17 |

Storage is **CASC** (not MPQ). Everything goes through TACT/CASC, BLTE
decoding, then format-specific parsing.

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
version matches  ^1\.6\d\.       (1.6x, tolerant of a future 1.61 content patch)
AND buildId >= 69900
```

Anything else on this product is a **different game**. Discard it silently.
Never diff across the boundary — do not compare a 1.60 build against a 5.5
build, the output is meaningless noise.

Do **not** filter on date alone. wago.tools backfills and re-indexes older
builds, so dates are not a reliable discriminator. Build IDs are globally
monotonic across all Blizzard products and are the reliable secondary check.

---

## Key facts

- **FDIDs share the retail namespace.** The community listfile
  (`wowdev/wow-listfile`) applies directly. No Forever-specific listfile exists.
- **DB2 layouts are Classic-flavor, not retail.** WoWDBDefs resolves these via
  layouthash, but Forever is new — expect unknown/unnamed columns in new tables
  until definitions catch up. Do not guess column meanings in committed output;
  mark them `unk_<offset>`.
- **Forever has a graphical update over Classic.** Expect new and renamed model
  and texture paths that the community listfile does not yet cover. Unnamed
  files are normal and expected, especially in the first few builds.
- **Encryption.** Blizzard withholds Salsa20 keys for unreleased content.
  Encrypted files will fail to decode until keys leak into `TACTKeys`. Always
  skip-and-log encrypted files rather than erroring out the run.
- **Manifests rotate.** Once Blizzard drops a build from the live version list,
  you can only reach it if you saved its `buildConfig` + `cdnConfig` hashes.
  Capture these every patch day — this is not recoverable after the fact.

---

## Tool stack

| Tool | Role | Repo |
|---|---|---|
| wow.tools.local (WTL) | Primary UI: file browser, DB2 tables, build diffing, model viewer, hotfixes | `github.com/Marlamin/wow.tools.local` |
| TACTSharp | CASC/TACT extraction library + `TACTTool` CLI | `github.com/wowdev/TACTSharp` |
| DBCD | DB2/DBC reader | `github.com/wowdev/DBCD` |
| WoWDBDefs | DB2 column definitions | `github.com/wowdev/WoWDBDefs` |
| wow-listfile | FDID → path mapping | `github.com/wowdev/wow-listfile` |
| TACTKeys | Decryption keys for encrypted content | `github.com/wowdev/TACTKeys` |
| wago.tools | Remote source for builds not on disk; build history | `wago.tools` |

Notes:
- The **wow.tools website** is deprecated (announced Dec 2021, stopped
  archiving early 2023). The **tooling** is not — WTL, TACTSharp, DBCD and
  WoWDBDefs are all actively maintained. Ignore deprecation notices about the
  site; they do not apply to the libraries.
- WTL pulls from wago.tools only for builds not loaded locally. Recent versions
  prefer spawning their own TACTSharp instance during diffs.
- Requires the .NET SDK (WTL, TACTSharp and DBCD are all C#).

---

## Repo layout

```
.
├── CLAUDE.md
├── builds.json              # manifest index — buildConfig/cdnConfig per build
├── scripts/
│   ├── fetch_builds.py      # poll version endpoint, update builds.json
│   ├── extract_db2.py       # CASC -> DB2 -> CSV for one build
│   ├── classify.py          # magic-byte identification of unnamed files
│   └── diff_builds.py       # compare two build dirs, emit markdown
├── out/                     # GITIGNORED — extracted data
│   └── <version>.<build>/
│       ├── db2/*.csv
│       ├── files.csv        # fdid, path, size, encrypted, type
│       └── unknown/         # unnamed files by fdid
├── reports/                 # COMMITTED — diff output
│   └── <from>_to_<to>.md
└── vendor/                  # cloned third-party tools
```

`out/` is enormous. Gitignore it. Only `builds.json`, `scripts/` and
`reports/` are committed.

---

## Endpoints

```
# live version list for the product
https://us.version.battle.net/v2/products/wow_classic_beta/versions

# same, CDN hosts
https://us.version.battle.net/v2/products/wow_classic_beta/cdns
```

Response is pipe-delimited with a `## seqn` line, not JSON. Parse accordingly.
Regions: `us`, `eu`, `kr`, `tw`, `cn`. `us` is the default; builds are usually
identical across regions but `cn` can lag or diverge.

---

## Conventions

- **Never hardcode build numbers in scripts.** Read from `builds.json` or take
  as an argument. Builds change weekly during beta.
- **Product code lives in one place.** A constant, not scattered string
  literals. If Blizzard ever moves Forever to a dedicated product code, that
  should be a one-line change.
- **Extraction must be resumable.** Runs take a long time and encrypted files,
  network blips and missing keys will interrupt them. Checkpoint progress;
  never restart from zero.
- **Log, don't crash.** An unreadable file is expected, not exceptional. Write
  it to a failure log with a reason (`encrypted`, `missing_key`, `bad_blte`,
  `unknown_format`) and continue.
- **Diffs are the deliverable.** Raw extraction is a means to an end. The
  markdown reports in `reports/` are what the project actually produces.
- **Parallelize extraction**, but cap concurrency — CASC reads are IO-bound and
  oversubscribing thrashes the disk. 8 workers is a sane default.

---

## Patch-day checklist

1. Let Battle.net finish patching, then **launch the client once** so the local
   CASC index is complete. Skipping this yields partial extractions.
2. Run the version fetch — capture the new `buildConfig` / `cdnConfig` into
   `builds.json` **before doing anything else**. This is the step that is
   unrecoverable if skipped.
3. Sync `wow-listfile`, `WoWDBDefs` and `TACTKeys`.
4. Full extract into `out/<version>.<build>/`.
5. Diff against the previous Forever build.
6. Commit `builds.json` and the new report.

---

## Scope

Reading and diffing local client data for analysis. Not client modification,
not CASC writing, not redistribution of extracted assets.
