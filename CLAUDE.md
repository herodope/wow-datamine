# WoW Forever Datamining

Local pipeline for extracting, parsing, and diffing World of Warcraft: Forever
client data across beta builds.

Local only: `.claude/notes/state-log.md` (build log, findings record) and
`.claude/archive/` (the full pre-split CLAUDE.md).

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

## WTL API

Read **Gotchas** at the top of [`docs/wtl-api.md`](docs/wtl-api.md) before
writing or changing anything that calls WTL. Each trap there returns
plausible output rather than an error. The three that corrupt an extraction:

- The hotfix parameter on API routes is `useHotfixes=true`. The browse page's
  `hotfixes=` binds nothing, returns 200 and silently yields plain data.
- Iterate the build-filtered table list (`/listfile/db2s?build=<version>`,
  1161 tables), never the unfiltered one (1342).
- `204` is defined-but-empty, `404` is not in this build. A table can be 204
  plain and 200 hotfixed (`hotfix_only`), so never call a table empty from the
  plain request alone.

---

## Reference docs

Tracked, not loaded every session. Read the one that matches the task.

| Doc | Read it when |
|---|---|
| [`docs/wtl-api.md`](docs/wtl-api.md) | calling a WTL route or adding one to a script |
| [`docs/conventions.md`](docs/conventions.md) | a rule below needs its measurement or example, or a diff or report script changes |
| [`docs/patch-day.md`](docs/patch-day.md) | a new build lands, or on a hotfix-only downtime |
| [`docs/key-facts.md`](docs/key-facts.md) | FDIDs, DB2 layouts, naming and typing coverage, hotfixes, encryption or GameTables |
| [`docs/baseline-metrics.md`](docs/baseline-metrics.md) | logging a build's metrics or reading an encrypted-file count |
| [`docs/retail-contamination.md`](docs/retail-contamination.md) | changing `contamination.py` or judging a contamination finding |
| [`docs/sod-tags.md`](docs/sod-tags.md) | touching `sod_tags.py` or `spell_reach.py`, or calling a spell SoD, cut or player-facing |
| [`docs/repo-layout.md`](docs/repo-layout.md) | `wow.db` schema and prefixes, MCP server setup, `manifest.json` resolution values |

Ad-hoc data questions go through the `wow-query` skill
(`.claude/skills/wow-query/SKILL.md`) and `scripts/query.py`.

---

## Repo layout

```
builds.json                     build index (buildConfig/cdnConfig); commit it
sod_allowlist.json, sod_manual.json   hand-kept SoD tag inputs
requirements.txt                one entry: mcp
docs/                           reference docs (table above)
scripts/                        the pipeline; config.py is the single source
.claude/skills/wow-query/       query rules skill (force-added)
out/<version>.<build>/          GITIGNORED: db2/, db2_hotfixed/, hotfixes.csv,
                                files.csv, gametables/, wow.db, manifest.json
reports/                        GITIGNORED: generated diffs and patch notes
vendor/                         GITIGNORED: WTL, TACTSharp, WoWDBDefs, listfile, TACTKeys
```

`wow.db` table prefixes: none is the live view (shipped plus hotfixes),
`plain_` is as shipped, `gt_` is GameTables. Full tree and schema notes:
[`docs/repo-layout.md`](docs/repo-layout.md).

---

## Conventions

Short form. The measurement and examples behind each rule are in
[`docs/conventions.md`](docs/conventions.md).

- **Never hardcode build numbers.** Read from `builds.json` or take an argument.
- **Product code and paths live only in `scripts/config.py`.**
- **Extraction must be resumable.** Checkpoint progress; never restart from zero.
- **Log, don't crash.** Record an unreadable file with a reason (`encrypted`,
  `missing_key`, `bad_blte`, `unknown_format`) and continue.
- **Read WTL's source for route shapes.** Never guess an endpoint.
- **Every rule is measured or source-read, and says which.** Source-reading
  gives a hypothesis; promote it only after a live response confirms it.
- **A fix that works does not confirm its diagnosis.** Measure the mechanism
  as a separate claim.
- **Match on declared FK relations, never raw values.** Use
  `/dbc/relations/<Table>::<Column>`; a raw-value scan reports ~47 references
  for a Thunder Clap rank against a true 17.
- **An unscanned zero is not a clean result.** Report coverage next to every
  count, and render `not_scanned` as **NOT SCANNED**, never as 0.
- **Diffs are the deliverable.**
- **Extract every table twice:** plain to `db2/`, hotfixed to `db2_hotfixed/`.
- **Compare variants by content hash, not row count.**
- **Key rows on the `ID` column, never column 0.** `key_index(header, table)`
  tries `ID`, then the DBD's `$id$` column, then column 0.
- **Never field-compare across a schema change.** `fields_comparable` needs
  layouthash and CSV header unchanged; `rows_comparable` needs layouthash and
  column count unchanged. A schema-changed table stays in the report even at
  magnitude 0.
- **Push IDs of `2^24 + recordID` are synthetic** bulk injection. Report them
  apart from real pushes (112xxx).
- **Cap concurrency at ~8.**

---

## Patch day

Follow [`docs/patch-day.md`](docs/patch-day.md). `scripts/patchday.py` runs
steps 3–10; use `--dry-run` first. Two steps cannot be redone later:

- Run `fetch_builds.py` and capture `buildConfig`/`cdnConfig` into
  `builds.json` **before anything else**.
- On a hotfix-only day, copy `db2_hotfixed/` to a dated snapshot **before**
  re-extracting.

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

## Endpoints

```
https://us.version.battle.net/v2/products/wow_classic_beta/versions
https://us.version.battle.net/v2/products/wow_classic_beta/cdns
```

Pipe-delimited, not JSON. The `## seqn` line is a comment, not data. Regions:
`us`, `eu`, `kr`, `tw`, `cn`. Use `us`; builds are usually identical across
regions but `cn` can lag or diverge.

---

## Scope

Reading and diffing local client data for analysis. Not client modification,
not CASC writing, not redistribution of extracted assets.
