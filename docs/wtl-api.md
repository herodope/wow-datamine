# WTL HTTP API

Routes in `wow.tools.local` that this pipeline uses. Everything here was read
from the controller source in `vendor/wow.tools.local/Controllers/`, not
inferred from URL patterns, and the marked routes were confirmed live against
`http://localhost:5080` on build `1.60.1.69913`.

Base URL: `http://localhost:5080` (`config.WTL_URL`).

WTL serves exactly one build at a time — the one it loaded at startup. The
`build` parameter on most routes selects which *extracted-to-disk* copy to read;
for the currently loaded build it reads from CASC directly.

---

## Cross-cutting parameters

### `locale`

Bound to the `TACTSharp.RootInstance.LocaleFlags` enum, so ASP.NET accepts
**either the name or the numeric value**: `locale=enUS` and `locale=2` are the
same request.

| Name | Value | | Name | Value |
|---|---|---|---|---|
| `None` | 0 | | `enGB` | 0x200 |
| `enUS` | 0x2 | | `enCN` | 0x400 |
| `koKR` | 0x4 | | `enTW` | 0x800 |
| `frFR` | 0x10 | | `esMX` | 0x1000 |
| `deDE` | 0x20 | | `ruRU` | 0x2000 |
| `zhCN` | 0x40 | | `ptBR` | 0x4000 |
| `esES` | 0x80 | | `itIT` | 0x8000 |
| `zhTW` | 0x100 | | `ptPT` | 0x10000 |
| `All_WoW` | all of the above OR'd | | `All` | 0xFFFFFFFF |

It is a **flags** enum. The default on every DB2 route is `All_WoW`, not
`enUS`. Our client is `enUS`; leaving the default in place is fine for
extraction and is what the UI does.

### `useHotfixes`

Plain `bool`, default `false`. Present on `/dbc/data/{name}`,
`/dbc/export`, and `/dbc/export/all`. `true` applies the live hotfix overlay
from `Cache/ADB/enUS` on top of the shipped DB2.

> **Trap:** the browse page's own URL spells this `hotfixes=`, but the API
> parameter is `useHotfixes=`. `wwwroot/dbc/index.html:1102` literally rewrites
> `&hotfixes=` to `&useHotfixes=` when building the CSV button's href. Sending
> `hotfixes=true` to an API route is silently ignored — it binds nothing, and
> you get non-hotfixed data with a 200. **This is the single most dangerous
> parameter in this document for us**, because the failure is silent and
> produces a `db2_hotfixed/` directory full of non-hotfixed rows.
>
> Measured on `itemsearchname` at build `1.60.1.69913`:
>
> | request | rows | result |
> |---|---|---|
> | `&useHotfixes=true` | 10,556 | hotfix overlay applied |
> | *(no parameter)* | 6,622 | plain |
> | `&hotfixes=true` | 6,622 | **byte-identical to plain — silently ignored** |
>
> A 59% row difference, with HTTP 200 and no warning in either case.
>
> WTL's console proves it server-side. The DBC cache key includes the hotfix
> flag, and WTL logs which variant it loads. Across those three requests it
> logged only **two** loads:
>
> ```
> Exporting DBC itemsearchname as CSV ...
> DBC itemsearchname for build 1.60.1.69913 (hotfixes: False) is not cached, loading!
> Exporting DBC itemsearchname as CSV ...
> DBC itemsearchname for build 1.60.1.69913 (hotfixes: True) is not cached, loading!
> Exporting DBC itemsearchname as CSV ...      <- hotfixes=true, no load
> ```
>
> The third request (`&hotfixes=true`) logged no load at all: it was served
> from the **`hotfixes: False`** cache entry. The parameter bound nothing.
> `(hotfixes: False|True)` in WTL's log is the reliable way to confirm which
> variant a script actually requested.

### DataTables envelopes — there are two

`data` is always an array of string arrays (positional, no field names), but
**two different result types exist and they do not have the same fields.**
Confirmed live.

**With `error`** — nested classes in `DataController.cs:12` and
`InfoController.cs:12`:

```json
{ "draw": 0, "recordsFiltered": 0, "recordsTotal": 1161,
  "data": [["..."]], "error": "" }
```

Used by `/dbc/info` and `/dbc/data/{name}`. These **return HTTP 200 with a
populated `error` string** instead of a 4xx — check `error`, not the status
code.

**Without `error`** — the shared struct at `ListfileController.cs:942`
(namespace `wow.tools.local.Controllers`):

```json
{ "draw": 0, "recordsFiltered": 0, "recordsTotal": 26542, "data": [["..."]] }
```

Used by `/dbc/hotfixes/list`, `/listfile/files` and `/build/table`. A
`d["error"]` lookup against these raises `KeyError`. Read it with
`.get("error")` if you want one code path for both.

---

## DB2 / DBC

### List tables — `GET /listfile/db2s` ✅ confirmed

| Param | Type | Default | Notes |
|---|---|---|---|
| `build` | string | `""` | Empty returns every table WoWDBDefs knows; a build filters to tables cached for it |

Returns a bare JSON array of table names, **not** a DataTables envelope:

```json
["Achievement","Achievement_Category","AchievementCategory"]
```

`ListfileController.cs:408` → `dbcManager.GetDBCNames(build)`. With a build, it
filters `dbdProvider.GetNames()` by `dbcProvider.DB2IsCached(name, build)`
(`DBCManager.cs:152`).

### Table metadata — `GET /dbc/info` ✅ confirmed

| Param | Type | Notes |
|---|---|---|
| `build` | string | required |
| `draw` | int | optional, echoed back |

DataTables envelope. Each row is the DB2 header parsed directly from the file,
in this order:

```
[name, recordCount, fieldCount, recordSize, tableHash, layoutHash,
 minId, maxId, locale, flags, idIndex, totalFields, sectionCount, magic]
```

`tableHash` and `layoutHash` are hex strings (`X8`); fields unavailable in older
formats are the literal string `"N/A"`. `magic` is the last element
(`WDC5`, `WDC4`, …).

**Requires DBCs extracted to disk.** `InfoController.cs:41` checks
`Directory.Exists(DBCFolder/build)` first and returns
`error: "Could not find DBCs on disk for build <build>!"` with HTTP 200
otherwise. This is a per-build, one-time prerequisite — see
`/dbc/export/alltodisk`.

### Export one table as CSV — `GET|POST /dbc/export` ✅ confirmed

Also routed as `/dbc/export/csv` — identical handler, both `[Route("")]` and
`[Route("csv")]` on the same method (`ExportController.cs:57`).

**This is what the Tables page CSV button calls.** `wwwroot/dbc/index.html:1086`
builds it by taking the browse URL and replacing `/dbc/?dbc=` with
`/dbc/export/?name=`, so `dbc` becomes `name` and `build`/`locale` carry over
unchanged.

| Param | Type | Default | Notes |
|---|---|---|---|
| `name` | string | — | table name, lowercase |
| `build` | string | — | e.g. `1.60.1.69913` |
| `useHotfixes` | bool | `false` | **the hotfix overlay toggle** |
| `newLinesInStrings` | bool | `true` | `false` strips CR/LF inside string cells |
| `locale` | LocaleFlags | `All_WoW` | |

Returns `application/octet-stream` with
`Content-Disposition: attachment; filename=<name>.csv`. Header row is the column
names; **array columns are exploded with an ordinal suffix**, `Field[0]`,
`Field[1]`, … (`GetColumnNames`). Cells containing `,` `"` CR or LF are quoted
and inner quotes doubled.

Status codes (`ExportController.cs:70-93`):

| Code | Meaning |
|---|---|
| 200 | CSV body |
| 204 | table loaded but has 0 rows |
| 404 | `FileNotFoundException` — table not in this build |
| 400 | any other exception during generation |

**The two variants of the same table can return different statuses.**
`TimeEventData` at 1.60.1.69913 returns **204 plain and 200 with 3 rows
hotfixed** — the table ships empty and exists only as live hotfix data. A
script that decides "empty" from the plain request alone will silently drop
it. `extract_db2.py` records this as `resolution: "hotfix_only"`.

On POST, the form body becomes `DBCViewFilter` parameters (search/filter), same
shape as the DataTables query. For plain extraction, use GET.

> For our dual extraction, the same table is fetched twice, differing only in
> `useHotfixes`:
> ```
> /dbc/export/?name=spell&build=1.60.1.69913                      -> db2/
> /dbc/export/?name=spell&build=1.60.1.69913&useHotfixes=true     -> db2_hotfixed/
> ```

### Export every table as CSV — `GET /dbc/export/all`

| Param | Type | Default |
|---|---|---|
| `build` | string? | current build if omitted |
| `useHotfixes` | bool | `false` |
| `newLinesInStrings` | bool | `true` |
| `locale` | LocaleFlags | `All_WoW` |

Returns one ZIP (`alldbc-<build>-<locale>.zip`) containing `<table>.csv` per
table. Per-table failures are logged to WTL's console and skipped, not
surfaced in the response — a table missing from the ZIP is indistinguishable
from a table that failed. Prefer per-table calls when you need to account for
failures.

### Extract DB2s to disk — `GET /dbc/export/alltodisk`

| Param | Type | Default |
|---|---|---|
| `locale` | LocaleFlags | `All_WoW` |

Writes raw `.db2` files to `<dbcFolder>/<currentBuild>/dbfilesclient/`, always
for the **currently loaded build** — it takes no `build` parameter. Returns
bare `true`. This is the "DBCs missing, extract?" action on the builds page
(`wwwroot/builds/index.html:359`) and the prerequisite for `/dbc/info` and for
diffing this build later.

### Raw DB2 file — `GET /dbc/export/db2`

| Param | Type | Default |
|---|---|---|
| `tableName` | string | — |
| `fullBuild` | string | — |
| `locale` | LocaleFlags | `All_WoW` |

Note the parameter is `fullBuild` here, not `build`. Serves from CASC when
`fullBuild == CASC.BuildName`, otherwise from disk. 404 if absent.

### Paged table data — `GET|POST /dbc/data/{name}`

| Param | Type | Default |
|---|---|---|
| `build` | string | — |
| `draw` / `start` / `length` | int | DataTables paging |
| `useHotfixes` | bool | `false` |
| `locale` | LocaleFlags | `All_WoW` |

DataTables envelope; values are HTML-encoded (`WebUtility.HtmlEncode`), which
CSV export does not do. **Use `/dbc/export` for extraction, not this** — the
encoding would have to be undone.

---

## Files

### File list — `GET /listfile/files`

| Param | Type | Notes |
|---|---|---|
| `draw` / `start` / `length` | int | DataTables paging |

DataTables envelope. Row order (`ListfileController.cs:267`):

```
[fdid, filename, lookup, availableInBuild, type, encryptionStatus,
 otherLocaleOnly, placeholderFilename, lookupMatch, soundKits,
 modelFileData, textureFileData, creatureModelData, parentCount]
```

`availableInBuild` is `"true"`/`"false"` as strings. `encryptionStatus` is the
`CASC.EncryptionStatus` enum name, empty when not encrypted. `recordsTotal` is
`CASC.AvailableFDIDs.Count`, or `Listfile.NameMap.Count` when the `showAllFiles`
setting is on.

First call triggers lazy init of the SoundKit / ModelFileData /
TextureFileData / CreatureModelData maps and blocks on all four
(`Task.WaitAll`), so it can be slow.

### Filename by FDID — `GET /listfile/info` ✅ confirmed

| Param | Type | Notes |
|---|---|---|
| `filedataid` | string | single id, or comma-separated |
| `filename` | int | present in the signature but unused |

Returns a bare string — the filename, or `""` if unknown. With a
comma-separated list it returns the **first** id that resolves, not all of
them.

### File contents by FDID — `GET /casc/fdid`

| Param | Type | Default |
|---|---|---|
| `fileDataID` | uint | — |
| `filename` | string | `""` — download name; defaults to `<fdid>.<type>` |
| `build` | string | `""` — current build |

Returns the file bytes as `application/octet-stream`, 404 if
`!CASC.FileExists(fileDataID)` or the read returns null.

### File detail — `GET /casc/moreinfo`

| Param | Type |
|---|---|
| `filedataid` | int |

Returns an **HTML fragment**, not JSON — the source calls this out as legacy
(`"generating HTML here is ugly but that's how the old system worked"`).
Scraping it is fragile; prefer `/listfile/files` and `/listfile/info`.

---

## Builds

### Current build name — `GET /casc/buildname` ✅ confirmed

No parameters. Returns a bare string, e.g. `1.60.1.69913`. The cheapest
liveness probe for WTL.

### Build table — `POST /build/table` ✅ confirmed

`[HttpPost]` only — a GET returns 405.

| Form field | Default | Notes |
|---|---|---|
| `mode` | `local` | |
| `showLocal` | `true` | from `.build.info` |
| `showOnline` | `false` | queries Ribbit/TACT for all `wow*` products |
| `showArchived` | `false` | from WTL's SQLite |
| `showEncrypted` | `false` | includes `wowdev`/`wownev`/`wowv` products |
| `start` / `length` | 0 / 20 | |
| `order[0][column]` / `order[0][dir]` | 1 / `desc` | |
| `search[value]` | — | substring match across all fields |

DataTables envelope. Row order (`BuildController.cs:163`):

```
[patch, build, product, buildConfig, cdnConfig,
 isActive, hasManifest, hasDBCs, isRemote]
```

where `patch` is `1.60.1` and `build` is `69913` — **split, not the full
version string**. The last four are `"True"`/`"False"` (C# `ToString()`, so
capitalised, unlike the lowercase strings in `/listfile/files`).

`hasDBCs` is the authoritative answer to "are DB2s extracted for this build?"

> `showOnline=true` queries every `wow*` product from the version service. Those
> rows are other games on recycled product codes — apply
> `config.is_forever_build()` before using anything from them.

### Archived builds — `GET /build/list`

No parameters. Returns `SQLiteDB.GetBuilds()` as a JSON array of objects (not
the DataTables envelope) — WTL's own record of builds it has seen.

---

## Hotfixes

### Hotfix list — `GET /dbc/hotfixes/list` ✅ confirmed

| Param | Type | Default |
|---|---|---|
| `draw` | int | 0 |
| `start` | int | 0 |
| `length` | int | 10 |

DataTables envelope. Row order (`HotfixController.cs:56`):

```
[pushID, tableName, recordID, build, status, firstDetected, tableIsKnown]
```

`build` is resolved from the hotfix's build id, or `"?"` when unknown.
`status` is the raw integer. `firstDetected` is when **WTL** first saw the
hotfix, not when Blizzard pushed it. `tableIsKnown` is `"1"`/`"0"`.

> **Trap:** the handler returns an empty result set if the request has **no
> query string at all** (`if (!Request.QueryString.HasValue)` returns zeros
> immediately). Bare `GET /dbc/hotfixes/list` yields
> `{"recordsTotal":0,"data":[]}` — which looks like "no hotfixes exist" but is
> not. Always send at least one parameter, e.g. `?length=100`.

Ordering is fixed in SQL: `firstdetected DESC, pushID DESC, tableName DESC,
recordID DESC`. `length` is applied as a SQL `LIMIT`, so paging is server-side.

### Download latest hotfixes — `GET /dbc/hotfixes/downloadLatest`

| Param | Type |
|---|---|
| `branch` | string |

Pulls DBCache files from Raidbots. Returns 200 with an empty body.

---

## Cache management (patch day)

### `GET /dbc/updateDefs` ✅ confirmed

Reloads the DBD manifest and definitions, then clears both the DBC cache and
the hotfix cache. Returns `"Reloaded <n> definitions and cleared DBC cache!"`
as `text/plain`.

This backs the **"Update WoWDBDefs & clear cache"** button. With a local
`definitionDir` — which is our setup — `UpdateDefsController.cs:19` logs
*"WARNING: You are using a local DBD definitions directory, updating can not be
done through WTL itself"* and skips the download. The reload-and-clear half
still runs, and that is the half we need after `sync_refs.py` has refreshed the
clone on disk.

**Observed** (2026-09-19, build `1.60.1.69913`):

```
$ curl -s http://localhost:5080/dbc/updateDefs
Reloaded 1342 definitions and cleared DBC cache!
[HTTP 200, 0.56s, text/plain; charset=utf-8]
```

WTL's console for the same call:

```
WARNING: You are using a local DBD definitions directory, updating can not be
done through WTL itself.
Reloading definitions from directory A:\...\vendor\WoWDBDefs\definitions
Loaded 1342 definitions from definitions folder!
Loaded 531 relations and 12 label columns
```

The skip-download warning fires as expected, and definitions are reloaded from
our local clone rather than fetched. The relations/label-column counts match
CLAUDE.md's baseline metrics (531 / 12), so the reload reproduced the same
state.

Return string matches the source exactly. The count is **1342** — it comes from
`Directory.EnumerateFiles(definitionsDir)` in `DBDProvider.LoadDefinitions`,
which counts **every** file in the directory, not just `.dbd`. Our clone happens
to contain 1342 files, all of them `.dbd`, so the two agree here; a stray file
dropped into `definitions/` would inflate this number.

**What it clears, and what it does not.** `DBCManager.ClearCache()` disposes and
recreates an in-memory `MemoryCache` (`DBCManager.cs:20`, SizeLimit 250) keyed
by `(name, build, useHotfixes, locale)`, and rebuilds the `DBCD` instance.
Nothing on disk is touched — it does not read or write `dbcFolder`.

Verified by before/after around the call:

| Check | Before | After |
|---|---|---|
| `/listfile/db2s?build=…` | 1161 | 1161 |
| `/dbc/info?build=…` rows | 1161 | 1161 |
| `dbcs/1.60.1.69913/dbfilesclient/` | 1161 `.db2` files | 1161 `.db2` files |
| `/dbc/export` `itemsearchname` hotfixed | 10,556 rows | 10,556 rows |

**No re-extraction is needed after calling this.** The next request for a table
re-parses it from the existing on-disk DB2 and repopulates the cache; the only
cost is losing warm cache entries, so the first query per table is slower.

The cache clear is real, not just claimed by the return string: the
post-call export of `itemsearchname` logged
`(hotfixes: True) is not cached, loading!` even though that exact variant had
been served minutes earlier — the entry was gone and was rebuilt from the
on-disk DB2.

### `GET /dbc/reloadDefs`

Same, plus clearing the enum provider cache and `HotfixManager`. Does not touch
the DBD manifest.

### `GET /dbc/reloadHotfixes`

Clears hotfix state and re-reads the DBCache files. Returns
`"Reloaded hotfixes"`. Needed after the client writes new hotfix data —
otherwise `useHotfixes=true` serves a stale overlay.

---

## Notes for scripting

- **Check `error` where it exists** (`/dbc/info`, `/dbc/data`) — those report
  failure as HTTP 200 plus an `error` string. The other DataTables routes have
  no `error` field at all; use `.get("error")`.
- **`useHotfixes`, never `hotfixes`.** The wrong spelling returns 200 with
  non-hotfixed data.
- WTL holds one build at a time. Extracting a build that is not loaded requires
  it to have been extracted to disk previously (`hasDBCs` on `/build/table`).
- Routes that mutate state (`updateListfile`, `exportListfile`, `updateLookups`)
  return `false` immediately when the `readOnly` setting is on.
- `/dbc/export` is single-threaded per request and loads the whole table into a
  `MemoryStream` before responding. Cap concurrency at ~8 per project
  convention.

---

## Verification log

Checked against a live WTL on `http://localhost:5080`, build `1.60.1.69913`,
2026-09-19. Routes marked ✅ above were exercised directly.

| Route | Result |
|---|---|
| `GET /casc/buildname` | `1.60.1.69913`, bare string |
| `GET /listfile/db2s` | bare array, 1342 names |
| `GET /listfile/db2s?build=…` | bare array, 1161 names (filtered to cached) |
| `GET /dbc/info?build=…` | 1161 rows, 14 elements each, order as documented |
| `GET /dbc/export` ×3 | see the `useHotfixes` table above |
| `GET /dbc/hotfixes/list` | empty-query trap reproduced exactly |
| `GET /dbc/hotfixes/list?length=…` | 26,542 hotfix records, 7 elements per row |
| `POST /build/table` | 1 local build, 9 elements, order as documented |
| `GET /build/table` | 405, as expected from `[HttpPost]` |
| `GET /listfile/info?filedataid=…` | bare string filename |
| `GET /dbc/updateDefs` | `Reloaded 1342 definitions and cleared DBC cache!`, 0.56s; on-disk DB2s unaffected |

### Discrepancies found

1. **Two DataTables envelopes, not one.** First written up as a single shape
   with an `error` field. Live responses showed `/dbc/hotfixes/list`,
   `/listfile/files` and `/build/table` omit `error` entirely — they use the
   struct at `ListfileController.cs:942`, while `/dbc/info` and `/dbc/data`
   use their own nested classes. Corrected above.

2. **1342 vs 1161 tables.** `/listfile/db2s` unfiltered returns every table
   WoWDBDefs defines (1342 — matching the DBD count in CLAUDE.md's baseline
   metrics); filtered by build it returns 1161. The 181-table gap is retail
   tables absent from this Classic build. **Iterate the build-filtered list**,
   or ~181 exports will come back 204/404.

3. **Hotfix data references tables not in this build.** Of the distinct
   hotfixed tables sampled, all resolved in the build-filtered list, but the
   first table tried (`modifiedcraftingitem`) returned **204 No Content** for
   all three variants — present in the definitions and in the hotfix DB, zero
   rows in the DB2 itself. A 204 is not an error and carries no body; treat it
   as "empty table", distinct from 404 "not in this build".

4. **A 204 on the plain variant does not mean the table is empty.** Found
   during the first full extraction run: `db2_hotfixed/` came out with one
   more CSV than `db2/`. The extra was `TimeEventData` — 204 plain, 200 with
   3 rows hotfixed. Across all 1161 tables it is the only such case, which is
   precisely why it is easy to miss.

5. **Full-run figures** (1161 tables, 2322 exports, 0 errors, ~12s at 8
   workers): 610 `ok`, 550 `empty`, 1 `hotfix_only`, 0 `not_in_build`,
   15 with a hotfix delta. 1,913,530 plain rows / 1,921,642 hotfixed.
   Five of the 15 deltas have **identical row counts** with changed values,
   so variant comparison must hash content rather than count rows.

### Not yet verified

`/dbc/export/all`, `/dbc/export/alltodisk`, `/dbc/export/db2`,
`/casc/fdid`, `/casc/moreinfo`, `/build/list`,
`/dbc/hotfixes/downloadLatest`, `/dbc/reloadDefs` and `/dbc/reloadHotfixes`.
Their signatures are read from source but not exercised — the remaining cache
and download routes mutate WTL state, so they were left alone while it was
serving.

`/dbc/updateDefs` **was** exercised (see above) after confirming from source
that it only clears in-memory caches.
