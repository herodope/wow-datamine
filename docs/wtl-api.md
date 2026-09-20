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
hotfixed** — the table ships empty and exists only as live hotfix data, never
present in the client build itself. A script that decides "empty" from the
plain request alone will silently drop it. `extract_db2.py` records this as
`resolution: "hotfix_only"`, distinct from `empty` (204 in both variants) and
from `not_in_build` (404).

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

This route iterates `Listfile.NameMap`, so it can only ever return rows that
are **members of** that map. `recordsTotal` is `CASC.AvailableFDIDs.Count` and
`recordsFiltered` (unsearched) counts map members that are available in the
build.

> **Trap: matching totals do not mean every file is named.** At 1.60.1.69913
> both are **1,441,771**, and an earlier revision of this file read that as
> "nothing is unnamed". That is wrong, and the mistake was committed.
> Membership in `NameMap` is not the same as having a non-empty name —
> **19,583** of those rows carry an empty filename. Count empty values in the
> filename column (index 1); never infer naming coverage from the two totals
> agreeing. CLAUDE.md records this under **Key facts**.

> **Trap: `type:unk` returns 0 despite 99,348 rows carrying that type.**
> The `type:` search token looks up `Listfile.TypeMap` (`Listfile.cs:588`),
> which has no `unk` bucket. The lookup fails, no predicate is returned, and
> the search degrades to a substring match on filenames — which matches
> nothing. Meanwhile the row's `content_type` column gets `unk` from an
> unrelated `TryGetValue` fallback. Trusting the token would report nothing
> to classify when 99,348 files needed it. **Read the column, not the
> token.** The same applies to `encrypted:` — it takes a hex key, so
> `encrypted:true` silently degrades to a substring match; the exact-match
> tokens are `knownkey`, `unknownkey` and `encryptedbutnot`.

> **No per-file size exists anywhere in this API.** `/size/data` aggregates
> (`results[type] += fileSize`, `SizeController.cs:158`) and every other
> route works from `Listfile.NameMap`, which carries no size. Per-file sizes
> live in the CASC indices; `SizeController` reads `Data/data/*.idx` off
> disk directly, and reproducing that is the only route to real sizes.
> `inventory.py` emits an empty `size` column rather than inventing one.

### Aggregate sizes — `GET /size/data`

| Param | Type | Default |
|---|---|---|
| `groupType` | string | `filetype` — also `folder`, `expansion`, `majorpatch`, `patch` |
| `uniqueOnly` | bool | `false` |
| `localOnly` | bool | `false` — reads `Data/data/*.idx` from the install |
| `encodedSizes` | bool | `false` |
| `listfileSearch` | string | `available` |

Returns **totals per group**, never per-file rows. Listed here so it is not
mistaken for a source of per-file sizes.

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

## Images and textures

Three routes in the whole API produce pixels: `/casc/blp2png`, `/map/tile` and
`/map/download`. Everything else that looks image-related returns FDIDs or
metadata that you then feed to one of these.

### BLP → PNG — `GET /casc/blp2png`

`CASCController.cs:1603`.

| Param | Type | Default |
|---|---|---|
| `fileDataID` | int | — |
| `build` | string | `""` or `"?"` → current build |

Returns `image/png`, inline (no `FileDownloadName`, so no download filename).
Implementation is BLPSharp → NetVips:

```csharp
var blp = new BLPSharp.BLPFile(file);
var pixels = blp.GetPixels(0, out var w, out var h);   // mip 0 only
raw[2].Bandjoin([raw[1], raw[0], raw[3]])              // BGRA -> RGBA
```

This is the route the UI uses everywhere — `tooltips.js:240`,
`m3modelviewer.js:62`, `maps/worldmap.html:236`. The front end spells the
parameter `filedataid`; ASP.NET query binding is case-insensitive, so either
casing works.

- **Always mip 0** — full resolution, no resize parameter. A 512×512 minimap
  tile comes back 512×512; a 1024×1024 UI texture comes back 1024×1024. If the
  report needs thumbnails, resize them after fetching.
- **404 has two meanings.** `CASC.GetFileByID` returning null is one; the other
  is the **encryption probe** — the route reads the first 4 bytes and returns
  `NotFound()` if all four are zero. An encrypted file whose key is missing
  decodes to zeros, so it 404s rather than erroring. Treat a 404 here as
  "unavailable", not "does not exist", and cross-check the `encryptionStatus`
  column on `/listfile/files` before reporting a file as absent.
- **Not a BLP → 500 with an empty body.** There is no format check;
  `new BLPFile(stream)` on a non-BLP throws, and `Startup.cs:25` only installs
  `UseDeveloperExceptionPage` under `IsDevelopment()`. Running `-c Release`
  there is no exception handler at all, so an unhandled throw is a bare 500.
  Check `content_type` before calling.

### Raw file bytes — `GET /casc/fdid`, `GET /casc/chash`

`/casc/fdid` is documented under **Files** above. `/casc/chash`
(`CASCController.cs:59`) is the same thing keyed on a content hash:

| Param | Type | Default |
|---|---|---|
| `contenthash` | string | hex MD5 |
| `filename` | string | `""` → `<chash>.unk` |
| `build` | string | `""` → current build |

Resolves CKey → EKey via `CASC.TryGetEKeysByCKey` and streams the first EKey.
404 on miss; exceptions are swallowed to a console line and also 404.

Neither route decodes anything — they hand back the file as it sits in CASC
after BLTE. For a BLP that means BLP bytes, not an image.

### Bulk extraction — `GET /casc/zip/fdids`

`ZipController.cs:12`. The route is `casc/[controller]/fdids`, i.e.
**`/casc/zip/fdids`**.

| Param | Type | Notes |
|---|---|---|
| `ids` | string | comma-separated FDIDs, `uint.Parse` per element |
| `filename` | string | download name for the zip |

Builds the archive in memory and **stops adding at 100 MB** — over-limit files
are skipped and listed in an `errors.txt` entry inside the zip, and the
response is still a 200. Per-file failures go there too. **Read `errors.txt`;
a short zip is not reported any other way.**

Entry names come from `Path.GetFileName(Listfile.NameMap[fdid])`, so files with
duplicate basenames collide inside the archive, and an FDID absent from
`NameMap` raises `KeyNotFoundException` — caught by the generic handler and
recorded in `errors.txt` rather than named `<fdid>.unk`. The `.unk` fallback in
the source only fires for an FDID that is *in* the map with an empty name.

### Map list — `GET /map/list`

`MapController.cs:225`. No parameters. Returns a JSON array of

```
{ ID, internalName, displayName, wdtFileDataID }
```

Built from the `Map` DB2 (requires `ID`, `Directory`, `MapName_lang` — throws
if any is missing) filtered to maps whose WDT exists in CASC, then **appended
with listfile-derived entries** for any `world/minimaps/<dir>/` folder not
already covered. Those appended rows have `ID` = the folder name (a *string*,
not a numeric map id) and `wdtFileDataID` = 0. Downstream routes accept
`wdtFileDataID=0` and fall back to listfile name probing, so this works — but
**do not assume `ID` parses as an integer.**

Uses `CASC.BuildName`; there is no `build` parameter.

### Tile grid for a map — `GET /map/wdtMask`, `GET /map/wdtMaskPuzzle`

`MapController.cs:636` and `:643`.

| Param | Type | Notes |
|---|---|---|
| `mapID` | string | as returned by `/map/list` |
| `directory` | string | `internalName` |
| `wdtFileDataID` | uint | 0 = listfile fallback |
| `layer` | byte | `wdtMask` only, default 0 |

`wdtMask` returns a **flat array of 4096 ints** — FDIDs, 0 for an absent tile,
indexed `x * 64 + y`. Layers:

| `layer` | Source |
|---|---|
| 0 | `world/minimaps/<dir>/mapNN_NN.blp` |
| 1 | `world/maptextures/<dir>/<dir>_NN_NN.blp` |
| 2 | the same, `_n.blp` normals |
| 3, 4 | root ADTs — vertex colours / heightmap, rendered not read |
| 5 | `world/liquidflow/<dir>/….blp` |

Anything else throws (`"Unknown layer type"`) → 500.

`wdtMaskPuzzle` returns all layers at once as an array of
`{ x, y, rootADT, minimapTexture, mapTexture, mapTextureN, liquidFlow }`, which
is one request instead of five. Prefer it.

Both are memoised per process in `puzzleMapMaskCache` / `mapMaskCache`, keyed on
`(mapID, layer)` — **the cache key does not include the build**, and
`GET /map/clearCache` (`MapController.cs:38`) is the only way to drop it. Clear
it after switching builds or the second build gets the first build's grid.

Note the listfile-fallback branch builds `liquidFlow` paths as
`world/liquidflow/…` while it filters the candidate set on
`world/maps/liquidflow/…` (`MapController.cs:301` vs `:326`), so `liquidFlow` is
always 0 on that path. Do not read anything into a zero there.

### One tile as pixels — `GET /map/tile`

`MapController.cs:156`.

| Param | Type | Default |
|---|---|---|
| `fileDataID` | uint | — |
| `targetSize` | int | — |
| `adtMethod` | string | `""` → `mccv` |
| `output` | string | `raw` |

> **`output=png` does not work for BLP input.** The `output` switch is only
> consulted inside the `type == "adt"` branch (`MapController.cs:178`). Every
> BLP goes down the tail path, which unconditionally returns
> `application/octet-stream` holding **raw RGBA bytes** — `targetSize ×
> targetSize × 4`, no header. Asking for `output=png` on a minimap tile returns
> 200 with raw pixels that no image decoder will open. Use `/casc/blp2png` when
> you want a PNG, and `/map/tile` only when you want pixels to composite
> yourself.

Two more behaviours worth knowing: a missing FDID returns a **black PNG** of
`targetSize` (not a 404, and not raw bytes — the content type disagrees with
both other branches), and an FDID with no known type is **assumed to be BLP**.
The BLP path picks the smallest mip still ≥ `targetSize` then resizes down, so
`targetSize` is honoured; the ADT path renders at 128 and scales up.

### Whole map as one PNG — `GET /map/download`

`MapController.cs:795`. Same four parameters as `wdtMask`. Returns `image/png`
named `<mapID>.png`.

**This is a 64×64 grid of 512 px tiles.** `DownloadMap` hardcodes the bounds to
`(0, 0, 63, 63)` and `CompileMap` hardcodes `blpRes = 512`, so the output is
**32768 × 32768** regardless of how few tiles the map actually uses — a
full-size PNG built entirely in memory. There is no crop or scale parameter. For
a report, fetch tiles individually and composite to the bounding box you need.

Two source-level cautions:

- `CompileMap` only handles layers 0–4. **Layer 5 produces an empty image
  list**, and `Image.Arrayjoin` on an empty array throws → 500.
- Inside the tile loop, a tile that fails to read (`GetFileByID` null, or a BLP
  that will not decode) is logged and `continue`d **without appending a
  placeholder**, while an absent tile (`fdid == 0`) does append one. The
  array-join is positional, so one unreadable tile shifts every subsequent tile
  one cell. A visibly skewed map means read failures, not map data.

### World-map art, for reference

There is no route for "the world map image of zone X". `maps/worldmap.html`
assembles it from ordinary DB2 routes plus `/casc/blp2png`:

```
UiMap.ID
  -> UiMapXMapArt (UiMapID -> UiMapArtID)
     -> UiMapArtTile    (UiMapArtID -> FileDataID, RowIndex, ColIndex)
     -> UiMapArt        -> UiMapArtStyleLayer (TileWidth, TileHeight)
     -> WorldMapOverlay (UiMapArtID, OffsetX, OffsetY)
        -> WorldMapOverlayTile (WorldMapOverlayID -> FileDataID, Row/ColIndex)
```

It loads each of those with `/dbc/header/<table>` plus
`/dbc/data/<table>?…&useHotfixes=true&length=100000` (`worldmap.html:129`,
`:136`). Replicating that chain is the way to get zone maps into a report.

---

## Column metadata — enums, flags, colours

WoWDBDefs' `meta/` tree **is** exposed over HTTP, so there is no need to parse
`.dbdm` / `.dbde` / `.dbdf` ourselves.

### Where the definitions come from

`Providers/EnumProvider.cs:16`. On startup:

```
SettingsManager.DefinitionDir + "/../meta/mapping.dbdm"
  exists  -> FilesystemEnumProvider, reads the local clone
  missing -> isUsingBDBD = true, downloads the remote BDBD blob instead
```

With `definitionDir` pointed at `vendor/WoWDBDefs/definitions`, that resolves to
`vendor/WoWDBDefs/meta/mapping.dbdm`, which the clone does ship. The fallback is
**silent** — the same failure mode CLAUDE.md records for `definitionDir` itself.
Confirm it from the numbers: the local file holds **606 mappings** (44 `COLOR`,
297 `ENUM`, 265 `FLAGS`) referencing **354 distinct** definition files
(169 `.dbde` + 187 `.dbdf` on disk). Those are the baseline metrics in
CLAUDE.md, so a `getMappings` response that does not total 606 means WTL fell
back to BDBD.

`MetaType` serialises as an **integer**: `FLAGS = 0`, `ENUM = 1`, `COLOR = 2`
(`DBDefsLib/Constants/MetaType.cs`). The `// null for Color/Date (meta 2/3)`
comment in `MetaController.cs` is stale — there is no meta 3 in this version.

### All mappings — `GET /dbc/meta/getMappings`

`MetaController.cs:26`.

| Param | Type | Default |
|---|---|---|
| `tableName` | string? | null = every table |
| `build` | string? | null = no entry filtering |

Returns an array of

```
{ meta, tableName, columnName, arrIndex, conditionalTable,
  conditionalColumn, conditionalValue, entries }
```

where `entries` is `[{ value, name, builds, buildRanges, comment }]` for `meta`
0/1 and `null` for `meta` 2 (`COLOR`).

- `tableName` is an **exact case-insensitive equality**, not a substring.
- `entries: null` is ambiguous: it means either "this is a COLOR" or "this is an
  ENUM/FLAGS whose `.dbde`/`.dbdf` file is missing". **Switch on `meta`, not on
  `entries`.**
- **The response drops `metaValue` and `comment`.** `MappingWithEntries` (bottom
  of `MetaController.cs`) does not carry them, so the *name* of the enum a
  column maps to (`AchievementFlags`, `WeatherType`, …) is not obtainable over
  HTTP — only its entries. Read `meta/mapping.dbdm` directly if the report wants
  to name the enum.

> **Do not pass `build` on a 1.60.x build.** The filter is `EntryMatchesBuild` →
> `BuildRange.Contains`, which compares componentwise:
> `build.major >= min.major && build.major <= max.major`. Forever is
> `1.60.1.69913`, so `major = 60`. Against the `Vanilla` preset
> (`1.0.0.3980`–`1.12.3.6141`) that is `60 <= 12` → false; against every
> TBC-and-later preset `expansion = 1 < 2` → false. **A 1.60.x build matches no
> preset range that exists**, so the filter can only ever subtract.
>
> Currently 7 of 13,252 entry lines carry build tags, and all 7 are dropped:
> `WeatherType` loses **all six** of its entries and comes back as
> `entries: []`, and `SpellEffect` loses `146 ACTIVATE_RUNE`. An empty list
> reads as "no enum defined" rather than "filtered out", because
> `entries ??= new List<EnumEntry>()` runs before the `continue`. Omit `build`
> and filter nothing.

### One column — `GET /dbc/meta/getMeta`

`MetaController.cs:74`.

| Param | Type | Notes |
|---|---|---|
| `tableName` | string | required |
| `columnName` | string | accepts `Name[3]`; the index is split off and used |

Returns `{ metaType, entries }` or bare `null`.

- **COLOR columns always return `null` here.**
  `FilesystemEnumProvider.PopulateCache` skips `MetaType.COLOR` outright
  (`DBCD/DBCD/Providers/FilesystemEnumProvider.cs`), so the cache never holds
  one. `LightData::AmbientColor` is a mapped COLOR and still returns `null`.
  **`getMappings` is the only way to learn that a column is a colour.**
- There is **no conditional support** — the action signature takes only
  `tableName` and `columnName`, so the `conditionalTable`/`Column`/`Value`
  arguments the provider supports are always null. Conditional mappings are only
  reachable through `getMappings`.
- There is no `build` parameter, so no entry filtering — which, per above, is
  what we want anyway.

Relevant to the open `LightData` question in CLAUDE.md: 23 `LightData` columns
are mapped `COLOR` in `mapping.dbdm`, all of them named.
`Field_1_60_1_69876_055` is **not** among them, so the meta tree does not
support reading it as packed RGB either. That finding stays unconfirmed.

### Column headers, FKs and comments — `GET /dbc/header/{name}`

`HeaderController.cs:37`. Not previously documented here, and the most useful
route for annotating a diff.

| Param | Type | Notes |
|---|---|---|
| `name` | string | path segment |
| `build` | string | `?` → current build |

```
{ headers[], fks{col: "Table::Column"}, comments{col: text},
  unverifieds[], relationsToColumns{col: [...]}, error }
```

`unverifieds` lists columns whose DBD definition is marked unverified — the
machine-readable version of the "never assert an unknown column's meaning"
convention. `fks` is what turns an ID column into a name in a report.

> **The header shape changes when the table is empty.** With
> `storage.Values.Count == 0` the route walks `AvailableColumns` and emits bare
> names; with rows it walks the first row and expands arrays to `Field[0]`,
> `Field[1]`, … (`HeaderController.cs:60` vs `:80`). Our `db2/` CSVs come from
> `/dbc/export`, which expands arrays, so **`/dbc/header` on a 204-empty table
> does not line up with the CSV header for that table**.
>
> It also uses `GetOrLoad(name, build)` — two-arg, so `useHotfixes` is **false**
> and cannot be changed. A `hotfix_only` table such as `TimeEventData` therefore
> loads zero rows and takes the collapsed-header path even though
> `db2_hotfixed/TimeEventData.csv` has three rows and expanded array columns.

Errors are returned as HTTP 200 with the message in `error`, like `/dbc/info`.

### `GET /dbc/relations` and `GET /dbc/labelColumns`

`RelationController.cs` / `LabelController.cs`. No parameters.

- `/dbc/relations` → `{ "Table::Column": ["Other::Col", …] }` across all
  definitions. `/dbc/relations/{foreignColumn}` narrows to one.
- `/dbc/labelColumns` → a flat list of columns DBD marks as the human-readable
  label for their table.

These are the 531 / 12 in CLAUDE.md's baseline metrics. For a report, the label
column is what to display when resolving an FK.

---

## Row lookup and rendered tooltips

### One row — `GET /dbc/peek/{name}`

`PeekController.cs:30`.

| Param | Type | Default |
|---|---|---|
| `build` | string | `?` → current build |
| `col` | string | column to match |
| `val` | int | value to match |
| `useHotfixes` | bool | `false` |
| `pushIDs` | string | `""` — comma-separated, only honoured with `useHotfixes=true` |

Returns `{ values: { "Column": "string", "Array[0]": "…" }, offset }` — the
**first** matching row, every column stringified, arrays expanded. Enum-typed
fields are emitted as their numeric value.

- **Never 404s.** A miss is 200 with `values: {}`. An unloadable table is 200
  with `values: { "Error": "Invalid or missing DBC \"x\"" }`, and
  `name=filedata` is 200 with a `"Sorry"` key. Check for those keys.
- `offset` is declared and never assigned — always 0. Ignore it.
- `pushIDs` filters the hotfix overlay to specific pushes, which is the cheapest
  way to answer "what did push 112132 change in this row".

### All matching rows — `GET /dbc/find` and `GET /dbc/find/{name}`

`FindController.cs:15` and `:127`.

- `/dbc/find?name=&value=&build=&useHotfixes=` — every row where **any** column
  (or array element) stringifies to exactly `value`. No column filter, so it is
  a full scan of the table.
- `/dbc/find/{name}?build=&col=&val=&useHotfixes=&calcOffset=true` — every row
  where `col == val`.

Both return a list of the same stringified-dict shape `peek` uses. Use `find`
where `peek` would silently return only the first of several matches.

### Rendered tooltips — `GET /dbc/tooltip/item/{id}`, `/dbc/tooltip/spell/{id}`

`TooltipController.cs:108` and `:339`. These are the closest thing to
"presentable" output in the API and the obvious source for a patch-note report:
both return the icon FDID to hand to `/casc/blp2png`.

`item` returns `TTItem`:

```
Name, IconFileDataID, ExpansionID, ClassID, SubClassID, InventoryType,
ItemLevel, OverallQualityID, HasSparse, FlavorText, ItemEffects[],
Stats[], Speed, DPS, MinDamage, MaxDamage, RequiredLevel
```

Stats are **computed**, not read — `TooltipUtils.CalculateItemStat` against
`RandPropPoints` and the `ItemDamage*` tables. If `Item.IconFileDataID` is 0 it
falls back through `ItemModifiedAppearance` → `ItemAppearance`.

`spell` (`?level=60&difficulty=-1&mapID=-1`) returns `TTSpell`
(`SpellID, Name, SubText, Description, IconFileDataID`) with the description run
through `WoWTools.SpellDescParser`, so `$s1`-style tokens are resolved against
real spell data at the given level. `IconFileDataID` defaults to `134400` (the
question-mark icon) when `SpellMisc` has no row.

> **Tooltips are never hotfixed and the build is not selectable.** Every load in
> this controller is `GetOrLoad(name, CASC.BuildName)` — the two-argument
> overload, which is `useHotfixes: false` (`Managers/DBCManager.cs:23`).
> `FindRecords(…, true)` looks like a hotfix flag but the fifth parameter is
> `single` (`DBCManager.cs:172`), and `FindRecords` itself calls the two-arg
> `GetOrLoad`. There is no parameter to change this.
>
> That matters directly for finding #3 in CLAUDE.md: the 1,385 modern-ID
> `ItemSparse` additions are **hotfix-only**, so `/dbc/tooltip/item/<id>` on any
> of them falls through `ItemSparse` *and* `ItemSearchName` and returns
> `Name: "Unknown Item"` with `HasSparse: false`. The route is not a usable
> source for the PvP-rank items until they ship in the client.

> **`/dbc/tooltip/item/` throws on ordinary data.** Two uncaught paths:
> `"Item Level N not found in RandPropPoints"` and
> `"Don't know what table to map to unknown SubClassID N"`. With no exception
> handler in Release these are bare 500s. Do not iterate a list of item IDs
> through this route without catching per-item failures.

`/dbc/tooltip/file/{fileDataID}` returns only `{ fileDataID, filename, type }` —
`"Unknown"` for either miss. It is cheaper than `/listfile/info` for the type,
which that route does not return at all.

`/dbc/tooltip/wex/{expression}` renders a world state expression to English via
`WSExpressionParser`.

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
- **Release builds have no exception handler.** `Startup.cs:25` only adds
  `UseDeveloperExceptionPage` under `IsDevelopment()`, so any unhandled throw
  is a bare 500 with an empty body and the reason only on WTL's console. The
  routes that throw on ordinary input are `/casc/blp2png` (non-BLP),
  `/dbc/tooltip/item` (`RandPropPoints` / unknown `SubclassID`),
  `/map/wdtMask?layer=6+`, `/map/download?layer=5` and `/map/list` (missing
  `Map` columns). Catch per-item and read the console when debugging.
- **Several routes answer failure with 200.** `/dbc/peek` returns
  `values: {}` on a miss and an `"Error"` key on a bad table; `/dbc/header`
  and `/dbc/info` put the message in `error`; `/dbc/find` returns `[]`;
  `/casc/zip/fdids` hides per-file failures in an `errors.txt` entry inside
  the zip. Status code alone is not a success check on any of them.
- **Nothing in the image or tooltip surface is hotfix-aware.**
  `/casc/blp2png`, `/map/*` and `/dbc/tooltip/*` all read the plain build.
  Hotfix-only data reaches a report only through `/dbc/export`, `/dbc/data`,
  `/dbc/peek` or `/dbc/find` with `useHotfixes=true`.

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

6. **Row counts are not a sufficient delta test.** Five of the 15 deltas —
   `GlobalStrings`, `Light`, `LightData`, `LightDataGlobalVolumeFog` and
   `LightParams` — have **identical row counts in both variants but different
   values**. Comparing counts reports no change for any of them, so
   `extract_db2.py` computes `hotfix_delta` from a SHA-256 of each CSV
   instead. A 204 hashes as empty content, so a `hotfix_only` table also
   registers as a delta.

### Not yet verified

`/dbc/export/all`, `/dbc/export/alltodisk`, `/dbc/export/db2`,
`/casc/fdid`, `/casc/moreinfo`, `/build/list`,
`/dbc/hotfixes/downloadLatest`, `/dbc/reloadDefs` and `/dbc/reloadHotfixes`.
Their signatures are read from source but not exercised — the remaining cache
and download routes mutate WTL state, so they were left alone while it was
serving.

Everything added 2026-09-20 under **Images and textures**, **Column metadata**
and **Row lookup and rendered tooltips** is **source-read only** — no live
instance was running. Specifically unexercised: `/casc/blp2png`, `/casc/chash`,
`/casc/zip/fdids`, `/map/list`, `/map/tile`, `/map/wdtMask`,
`/map/wdtMaskPuzzle`, `/map/download`, `/map/clearCache`,
`/dbc/meta/getMappings`, `/dbc/meta/getMeta`, `/dbc/header/{name}`,
`/dbc/relations`, `/dbc/labelColumns`, `/dbc/peek/{name}`, `/dbc/find`,
`/dbc/tooltip/item`, `/dbc/tooltip/spell`, `/dbc/tooltip/file` and
`/dbc/tooltip/wex`. The enum-mapping counts (606 / 44 / 297 / 265, 354 distinct
definition files, 7 build-tagged entries of 13,252) were measured against
`vendor/WoWDBDefs/meta/` on disk, not against a response.

`/dbc/updateDefs` **was** exercised (see above) after confirming from source
that it only clears in-memory caches.
