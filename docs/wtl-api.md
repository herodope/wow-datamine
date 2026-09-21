# WTL HTTP API

Routes in `wow.tools.local` that this pipeline uses. Everything here was read
from the controller source in `vendor/wow.tools.local/Controllers/`, not
inferred from URL patterns, and the marked routes were confirmed live against
`http://localhost:5080` on build `1.60.1.69913`.

Base URL: `http://localhost:5080` (`config.WTL_URL`).

**Every route here is marked ✅ measured or ⚠️ source-read**, per the
convention in CLAUDE.md. ✅ means a live response confirmed the behaviour
described, against `1.60.1.69913`. ⚠️ means the signature and logic were read
from the controller but nothing has been sent — treat those as hypotheses and
promote them after exercising them. The distinction is not pedantic: the
`build=` rule on `/dbc/meta/getMappings` was source-read, committed as fact,
and was backwards.

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

### List tables — `GET /listfile/db2s` ✅ measured

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

### Table metadata — `GET /dbc/info` ✅ measured

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

### Export one table as CSV — `GET|POST /dbc/export` ✅ measured

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

### Export every table as CSV — `GET /dbc/export/all` ⚠️ source-read

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

Left ⚠️ deliberately: it is read-only, but it builds all 1,161 tables into one
in-memory ZIP -- a full extraction's work for a result `extract_db2.py`
already produces per-table, with per-table error accounting this route does
not offer. There is nothing to learn from running it that the per-table route
has not already shown.

### Extract DB2s to disk — `GET /dbc/export/alltodisk` ✅ measured

| Param | Type | Default |
|---|---|---|
| `locale` | LocaleFlags | `All_WoW` |

Writes raw `.db2` files to `<dbcFolder>/<currentBuild>/dbfilesclient/`, always
for the **currently loaded build** — it takes no `build` parameter. Returns
bare `true`. This is the "DBCs missing, extract?" action on the builds page
(`wwwroot/builds/index.html:359`) and the prerequisite for `/dbc/info` and for
diffing this build later.

### Raw DB2 file — `GET /dbc/export/db2` ✅ measured

| Param | Type | Default |
|---|---|---|
| `tableName` | string | — |
| `fullBuild` | string | — |
| `locale` | LocaleFlags | `All_WoW` |

Note the parameter is `fullBuild` here, not `build`. Serves from CASC when
`fullBuild == CASC.BuildName`, otherwise from disk. 404 if absent.

### Paged table data — `GET|POST /dbc/data/{name}` ✅ measured

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

### File list — `GET /listfile/files` ✅ measured

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

### Aggregate sizes — `GET /size/data` ✅ measured — **500s on this build**

| Param | Type | Default |
|---|---|---|
| `groupType` | string | `filetype` — also `folder`, `expansion`, `majorpatch`, `patch` |
| `uniqueOnly` | bool | `false` |
| `localOnly` | bool | `false` — reads `Data/data/*.idx` from the install |
| `encodedSizes` | bool | `false` |
| `listfileSearch` | string | `available` |

Returns **totals per group**, never per-file rows. Listed here so it is not
mistaken for a source of per-file sizes.

> **It returns HTTP 500 on 1.60.1.69913.** ✅ measured:
> `?groupType=filetype` throws `ArgumentOutOfRangeException` from
> `TACTSharp.EncodingInstance.FindContentKey`, wrapped in an
> `AggregateException`. That is the **same exception as the ten known-benign
> FDIDs** in CLAUDE.md's baseline metrics — the route walks every file and one
> malformed encoding entry takes the whole request down, where the analysis
> pass logs and continues. So on this build there is no working size route at
> all, per-file or aggregate. `inventory.py` emitting an empty `size` column
> is not a shortcut; it is the only available answer.

### Filename by FDID — `GET /listfile/info` ✅ measured

| Param | Type | Notes |
|---|---|---|
| `filedataid` | string | single id, or comma-separated |
| `filename` | int | present in the signature but unused |

Returns a bare string — the filename, or `""` if unknown. With a
comma-separated list it returns the **first** id that resolves, not all of
them.

### File contents by FDID — `GET /casc/fdid` ✅ measured

| Param | Type | Default |
|---|---|---|
| `fileDataID` | uint | — |
| `filename` | string | `""` — download name; defaults to `<fdid>.<type>` |
| `build` | string | `""` — current build |

Returns the file bytes as `application/octet-stream`, 404 if
`!CASC.FileExists(fileDataID)` or the read returns null.

### File detail — `GET /casc/moreinfo` ✅ measured

| Param | Type |
|---|---|
| `filedataid` | int |

Returns an **HTML fragment**, not JSON — the source calls this out as legacy
(`"generating HTML here is ugly but that's how the old system worked"`).
Scraping it is fragile; prefer `/listfile/files` and `/listfile/info`.

✅ measured: 200, 5,129 bytes for FDID 135274, served as
**`Content-Type: text/plain`** despite the body being HTML. It is, however,
the only route that exposes a file's **content hash** — `/casc/hashbyid` is
broken (see below), so scraping the 32-hex string out of this fragment is
currently the only way to get a CKey for `/casc/chash`.

### Content hash by FDID — `GET /casc/hashbyid` ✅ measured — **broken**

> **Returns `{}` for every input.** ✅ measured: FDID 135274 and FDID
> 999999999 both give HTTP 200 with a literal empty object.
>
> The action is declared `public (string, int) HashByID(int filedataid)`
> (`CASCController.cs:971`). `System.Text.Json` serialises a `ValueTuple` by
> its **fields**, and field serialisation is off by default — so both members
> vanish and the envelope is empty. The routes that return field-bearing
> types and work, such as `/dbc/meta/getMappings` and `/map/list`, all pass
> `IncludeFields = true` explicitly; this one does not.
>
> A 200 carrying `{}` is indistinguishable from "no hash for this file"
> without reading the source. Use `/casc/moreinfo` and scrape the CKey.

---

## Builds

### Current build name — `GET /casc/buildname` ✅ measured

No parameters. Returns a bare string, e.g. `1.60.1.69913`. The cheapest
liveness probe for WTL.

### Build table — `POST /build/table` ✅ measured

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

### Archived builds — `GET /build/list` ✅ measured

No parameters. Returns `SQLiteDB.GetBuilds()` as a JSON array of objects (not
the DataTables envelope) — WTL's own record of builds it has seen.

---

## Hotfixes

### Hotfix list — `GET /dbc/hotfixes/list` ✅ measured

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

### Download latest hotfixes — `GET /dbc/hotfixes/downloadLatest` ⚠️ source-read — **do not run on this install**

> **It imports another game's hotfixes into the local database, permanently.**
> `branch` accepts only `retail`, `ptr` or `beta` — there is no
> `wow_classic_beta` option. It fetches
> `storage.googleapis.com/raidbots-static/wow/<branch>/enUS/DBCache.bin`,
> writes it to `caches/`, and calls `HotfixManager.ParseCache`, which
> **INSERTs into the `wow_hotfixes`, `wow_hotfixes_data` and
> `wow_hotfixpushxbuild` SQLite tables** (`HotfixManager.cs:187`, `:202`,
> `:179`).
>
> `/dbc/hotfixes/list` counts that table unfiltered
> (`SELECT COUNT(*) FROM wow_hotfixes`), so the measured 26,542-record
> baseline for 1.60.1.69913 would change and `diff_hotfixes.py` would be
> reading a mixture of two games. There is no delete route and no undo.
>
> Records are keyed on the build ID inside the DBCache, so a retail import
> would not corrupt the `useHotfixes` overlay for a 1.60.x build — but it
> would permanently pollute the hotfix table this project diffs. Deliberately
> left unexercised for that reason, not for lack of opportunity.

| Param | Type |
|---|---|
| `branch` | string |

Pulls DBCache files from Raidbots. Returns 200 with an empty body.

---

## Cache management (patch day)

### `GET /dbc/updateDefs` ✅ measured

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
Reloading definitions from directory C:\path\to\wow-datamine\vendor\WoWDBDefs\definitions
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

### `GET /dbc/reloadDefs` ✅ measured

Same, plus clearing the enum provider cache and `HotfixManager`. Does not touch
the DBD manifest.

### `GET /dbc/reloadHotfixes` ✅ measured

Clears hotfix state and re-reads the DBCache files. Returns
`"Reloaded hotfixes"`. Needed after the client writes new hotfix data —
otherwise `useHotfixes=true` serves a stale overlay.

---

## Images and textures

Three routes in the whole API produce pixels: `/casc/blp2png`, `/map/tile` and
`/map/download`. Everything else that looks image-related returns FDIDs or
metadata that you then feed to one of these.

### BLP → PNG — `GET /casc/blp2png` ✅ measured

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
  decodes to zeros, so it 404s rather than erroring. ✅ measured: FDIDs 2144058
  and 2147641 (both `EncryptedUnknownKey`, both `blp`) return 404 from
  `blp2png` while `/casc/fdid` returns **200 with 175,948 and 350,724 bytes**
  of undecryptable data. An `EncryptedButNot` file (2147636) renders normally.
  Treat a 404 here as "unavailable", not "does not exist", and cross-check the
  `encrypted` column in `files.csv` before reporting a texture as absent.
- **Not a BLP → 500.** ✅ measured: FDID 1100087 returns HTTP 500. There is no
  format check; `new BLPFile(stream)` on a non-BLP throws. `Startup.cs:25`
  installs `UseDeveloperExceptionPage` only under `IsDevelopment()` and nothing
  for other environments — but `launchSettings.json` sets
  `ASPNETCORE_ENVIRONMENT=Development`, so under `run-wtl.ps1` (`dotnet run`)
  the 500 arrives as `text/plain` with a readable stack trace. Run the built
  exe without that variable and it is a bare 500 with an empty body. Check
  `content_type` before calling either way.

### Raw file bytes — `GET /casc/fdid`, `GET /casc/chash` ✅ measured

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

### Bulk extraction — `GET /casc/zip/fdids` ✅ measured

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

### Map list — `GET /map/list` ✅ measured

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

### Tile grid for a map — `GET /map/wdtMask`, `GET /map/wdtMaskPuzzle` ✅ measured

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

### One tile as pixels — `GET /map/tile` ✅ measured

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
> targetSize × 4`, no header. ✅ measured:
> `/map/tile?fileDataID=135274&targetSize=256&output=png` returns HTTP 200,
> `application/octet-stream`, **exactly 262144 bytes** = 256 × 256 × 4 — raw
> pixels that no image decoder will open. Use `/casc/blp2png` when
> you want a PNG, and `/map/tile` only when you want pixels to composite
> yourself.

Two more behaviours worth knowing: a missing FDID returns a **black PNG** of
`targetSize` (not a 404, and not raw bytes — the content type disagrees with
both other branches), and an FDID with no known type is **assumed to be BLP**.
The BLP path picks the smallest mip still ≥ `targetSize` then resizes down, so
`targetSize` is honoured; the ADT path renders at 128 and scales up.

### Whole map as one PNG — `GET /map/download` ⚠️ source-read — deliberately not exercised

> Left unexercised on purpose, not by omission. It is read-only, but every
> call composes a 64 × 64 grid of 512 px tiles into a single **32768 × 32768**
> image in memory with no crop or scale parameter. Exercising it while WTL is
> serving an extraction risks taking the process down. Run it deliberately,
> between runs, and record the result then.

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

### All mappings — `GET /dbc/meta/getMappings` ✅ measured

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

> **Pass `build`.** ✅ measured 2026-09-20 against 1.60.1.69913.
>
> | | mappings | colliding values | empty ENUM/FLAGS |
> |---|---|---|---|
> | no `build=` | 606 | **2** | 0 |
> | `build=1.60.1.69913` | 606 | **0** | 0 |
>
> The two collisions unfiltered are `Weather::Type` — 12 entries covering
> values 0–5 twice, retail (`0 None, 1 Clear, 2 Rain, 3 Snow, 4 Sandstorm,
> 5 Miscellaneous`) ahead of Classic (`0 Clear, 1 Rain, 2 Snow, 3 Sandstorm,
> 4 Miscellaneous, 5 Fire`) — and `SpellEffect::Effect`, 361 entries with one
> duplicate. **A decoder taking the first entry matching a value labels every
> Forever weather row with retail names.** `build=` removes the collision and
> leaves the Classic set.
>
> The mechanism constrains what the filter can do, so it is worth stating.
> `EntryMatchesBuild` → `BuildRange.Contains` compares componentwise:
> `build.major >= min.major && build.major <= max.major`. Forever is
> `1.60.1.69913`, so `major = 60`. Against the `Vanilla` preset
> (`1.0.0.3980`–`1.12.3.6141`) that is `60 <= 12` → false; against every
> TBC-and-later preset `expansion = 1 < 2` → false. **A 1.60.x build matches no
> preset range that exists**, so `build=` can only ever *drop* tagged entries,
> never select one. Currently 7 of 13,252 entry lines carry build tags (6 in
> `WeatherType.dbde`, 1 in `SpellEffect.dbde`) and all 7 are dropped — which is
> the correct result only because the era-specific variants are the tagged ones
> and the Classic defaults sit untagged below them. That is an authoring
> convention, not a guarantee: if a sync ever tags the Classic variant, the
> filter would strip it and `entries ??= new List<EnumEntry>()` running before
> the `continue` would return `entries: []`, which reads as "no enum defined"
> rather than "filtered out". **After every `sync_refs.py`, re-check that no
> ENUM/FLAGS mapping returns zero entries with `build=` set.**

### One column — `GET /dbc/meta/getMeta` ✅ measured

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

> **On an array column the index is required; the bare name returns `null`.**
> ✅ measured. `BattlePetEffectProperties::ParamTypeEnum` is mapped at every
> index and unmapped without one:
>
> ```
> ParamTypeEnum[1]  -> {metaType: 1, entries: [Int, Ability]}
> ParamTypeEnum     -> null
> ParamTypeEnum[9]  -> null        (no such index, no bare key to fall back to)
> ```
>
> The fallback in `FilesystemEnumProvider.GetEnumDefinition` runs
> **indexed → bare**, never bare → indexed: with an index it tries
> `table::column[n]` then `table::column`; without one it tries only
> `table::column`. Iterating CSV headers is safe because DBCD emits
> `ParamTypeEnum[0]`, `[1]`, … — but any code that normalises a column name
> by stripping `[n]` before asking will get `null` and read it as "not an
> enum". Table and column matching are case-insensitive.

Relevant to the open `LightData` question in CLAUDE.md: 23 `LightData` columns
are mapped `COLOR` in `mapping.dbdm`, all of them named.
`Field_1_60_1_69876_055` is **not** among them, so the meta tree does not
support reading it as packed RGB either. That finding stays unconfirmed.

### Column headers, FKs and comments — `GET /dbc/header/{name}` ✅ measured

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

### `GET /dbc/relations` and `GET /dbc/labelColumns` ✅ measured

`RelationController.cs` / `LabelController.cs`. No parameters.

- `/dbc/relations` → `{ "Table::Column": ["Other::Col", …] }` across all
  definitions. `/dbc/relations/{foreignColumn}` narrows to one.
- `/dbc/labelColumns` → a flat list of `Table::LabelID` columns.

These are the 531 / 12 in CLAUDE.md's baseline metrics.

> **`labelColumns` is not "the display name column per table".** ✅ measured:
> it returns 12 entries, all of the form `SpellLabel::LabelID`,
> `CreatureLabel::LabelID`, `QuestLabel::LabelID` — columns participating in
> WoW's gameplay *Label* system, not human-readable names. There is no route
> that says "display `MapName_lang` for a `Map::ID`". A report resolving FKs
> to names needs its own per-table registry; `scripts/enrich.py` keeps one in
> `LABELS`.

---

## Row lookup and rendered tooltips

### One row — `GET /dbc/peek/{name}` ✅ measured

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

### All matching rows — `GET /dbc/find` and `GET /dbc/find/{name}` ✅ measured

`FindController.cs:15` and `:127`.

- `/dbc/find?name=&value=&build=&useHotfixes=` — every row where **any** column
  (or array element) stringifies to exactly `value`. No column filter, so it is
  a full scan of the table.
- `/dbc/find/{name}?build=&col=&val=&useHotfixes=&calcOffset=true` — every row
  where `col == val`.

Both return a list of the same stringified-dict shape `peek` uses. Use `find`
where `peek` would silently return only the first of several matches.

### Rendered tooltips — `GET /dbc/tooltip/item/{id}`, `/dbc/tooltip/spell/{id}` ✅ measured

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
> That matters directly for finding #3 in CLAUDE.md. ✅ measured: of 4,218
> hotfix-only `ItemSparse` rows, `/dbc/tooltip/item/720` and `/item/286554`
> both return **HTTP 200** with `name: "Unknown Item"`, `hasSparse: false`,
> while `/item/25` gives `"Worn Shortsword"` and `/item/19019` gives
> `"Thunderfury, Blessed Blade of the Windseeker"`. A 200 with a plausible
> shape is the whole problem — the route is not a usable source for hotfix-only
> items until they ship in the client. Note the **icon still resolves**
> (132939, 133358), because `Item.db2` ships and carries `IconFileDataID`.
>
> The JSON is serialised **camelCase** (`name`, `hasSparse`,
> `iconFileDataID`), not the PascalCase the C# struct declares.

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

> **`expression` is a hex byte string, not an ID, and bad input is a 500.**
> ✅ measured: `/dbc/tooltip/wex/1` throws `FormatException` ("not a valid hex
> string as its length is not a multiple of 2") from `Convert.FromHexString`,
> and `/dbc/tooltip/wex/0100` — valid hex, but not a well-formed expression —
> throws `IndexOutOfRangeException` from `EvalArethmaticExp`. There is no
> validation and no error envelope. Feed it a serialised WSE blob from a DB2
> column or leave it alone.

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

**The per-route ✅ / ⚠️ markers are the authoritative list** — this section does
not repeat them, because a second list drifts out of step with the first and
then quietly contradicts it. Scan the headings.

Two standing reasons a route stays ⚠️:

- **It mutates WTL state.** `/dbc/export/alltodisk`,
  `/dbc/hotfixes/downloadLatest`, `/dbc/reloadDefs`, `/dbc/reloadHotfixes`
  and the `/casc/update*` family write to disk or reload caches. They are left
  alone while WTL is serving an extraction. Exercise them deliberately,
  between runs, not as part of documenting them.
- **Nothing has needed it yet.** The rest are simply unexercised. Any of them
  is one request away from ✅; promote it when a script first uses it, and
  record what came back.

A second pass on 2026-09-20 exercised the following against a live instance on
1.60.1.69913:

| Route | Result |
|---|---|
| `GET /dbc/meta/getMappings` | 606 mappings, 10.3 MB; `meta` counts 44 / 297 / 265 match `mapping.dbdm` exactly, so the filesystem provider is in use |
| `GET /dbc/meta/getMappings?build=…` | 606 mappings, **0** colliding values vs **2** unfiltered — see the corrected guidance above |
| `GET /dbc/header/Achievement` | 19 headers, 9 `fks`, 2 `unverifieds` (`HiddenBeforeDisplaySeason`, `LegacyAfterTimeEvent`), `Instance_ID → Map::ID` |
| `GET /dbc/header/AreaTriggerBox` | `['ID', 'Extents']` — array column collapsed, empty-table path confirmed |
| `GET /dbc/tooltip/item/{id}` ×4 | hotfix-only 720 / 286554 → `"Unknown Item"`; shipped 25 / 19019 resolve |
| `GET /casc/blp2png` ×5 | 200 `image/png` on an icon; **404** on two `EncryptedUnknownKey` BLPs; **500** on a non-BLP |
| `GET /casc/fdid` | 200 with bytes for the same encrypted FDIDs `blp2png` 404s on |
| `GET /map/tile?…&output=png` | 200 `application/octet-stream`, 262144 bytes = 256×256×4 |

**Corrected by that pass:** the `build=` guidance on `/dbc/meta/getMappings`
was originally written backwards — "omit it" — from reading
`WeatherType.dbde`'s six build-tagged lines without noticing the six untagged
lines below them. The live response settled it.

| `GET /dbc/labelColumns` | 12 entries, all `Table::LabelID` — not display-name columns |
| `GET /dbc/header/Light` | `ContinentID → Map::ID`, `LightParamsID[0..7] → LightParams::ID`, no unverifieds |

A fourth pass exercised the mutating routes, between extraction runs, with
WTL serving 1.60.1.69913:

| Route | Result |
|---|---|
| `GET /dbc/reloadDefs` | 200, `Reloaded 1342 definitions and cleared DBC cache!`, 3.35s — same body as `/dbc/updateDefs`, and 1342 is the **unfiltered** definition count |
| `GET /dbc/reloadHotfixes` | 200, `Reloaded hotfixes`, 0.56s; the hotfix table still reports 26,542 records afterwards, so it is non-destructive |
| `GET /dbc/export/alltodisk` | 200, `true`, 0.78s, 1161 `.db2` files — idempotent on a re-run, and fast because CASC is already cached |

`/dbc/hotfixes/downloadLatest` was **not** run; see the warning on its section.
It is the one route here that permanently alters data this project depends on.

A third pass cleared every non-mutating route that was still ⚠️:

| Route | Result |
|---|---|
| `GET /dbc/meta/getMeta` | ENUM resolves; COLOR returns `null`; **array columns need the index** — `ParamTypeEnum[1]` resolves, bare `ParamTypeEnum` is `null` |
| `GET /dbc/peek/{name}` | every documented quirk reproduced: `values:{}` on a miss, `Error` key on a bad table, `Sorry` on `filedata`, `offset` always 0, `build=?` accepted, arrays expanded |
| `GET /dbc/find/{name}` | 131 rows for `Light::ContinentID=1`; `[]` on no match **and** on a bad table; `ItemSparse::ID=720` returns 0 plain / 1 hotfixed (`"Brawler Gloves"`) |
| `GET /casc/chash` | byte-identical to `/casc/fdid` for the same file (SHA-256 match); default name `<chash>.unk`; 404 on both an unknown and a malformed hash |
| `GET /casc/zip/fdids` | 200; entries named from the listfile (`INV_Sword_04.blp`); the bogus FDID landed in `errors.txt` as documented |
| `GET /map/list` | 71 entries from 75 `Map` rows; **all numeric IDs, no `wdtFileDataID == 0`** — the listfile-fallback branch does not trigger on this build |
| `GET /map/wdtMask` | 4096 ints, 1224 non-zero for Azeroth; `layer=6` is a 500, as predicted |
| `GET /map/wdtMaskPuzzle` | 4096 tiles, the 7 documented keys; 87 non-zero `liquidFlow` on the WDT path |
| `GET /map/clearCache` | 200, empty body; masks rebuild identically afterwards |
| `GET /dbc/tooltip/spell` | 133 → Fireball with a parsed description; **27997 and 32837 (no `SpellMisc` row) return icon 134400**, confirming the fallback |
| `GET /dbc/tooltip/file` | `{fileDataID, filename, type}`; `"Unknown"` for both on a miss; `fileDataID` comes back a **string** |
| `GET /dbc/data/{name}` | envelope carries `error`; values **are** HTML-encoded, as warned |
| `GET /dbc/export/db2` | 200, `map.db2`, magic **`WDC5`** |
| `GET /dbc/relations` | 531 keys, matching the baseline metric; `/dbc/relations/Map::ID` lists 70 referencing columns |
| `GET /build/list` | 200, JSON array — **and it holds two Forever builds we had not recorded** (see below) |
| `GET /casc/moreinfo` | 200, 5,129 bytes, HTML served as `text/plain` |
| `GET /size/data` | **500** — `ArgumentOutOfRangeException` from `TACTSharp.EncodingInstance.FindContentKey` |
| `GET /casc/hashbyid` | **`{}` for every input** — `ValueTuple` return with no `IncludeFields` |
| `GET /dbc/tooltip/wex` | 500 on `1` (not hex) and on `0100` (hex, malformed expression) |

**Discrepancies between the source-read description and the response:**

1. **`getMeta` on an array column requires the index.** The fallback runs
   indexed → bare, not bare → indexed, so stripping `[n]` before asking
   returns `null` and reads as "not an enum".
2. **`/casc/hashbyid` is broken** — a `ValueTuple` return serialises to `{}`.
   `/casc/moreinfo` is the only way to obtain a CKey.
3. **`/size/data` 500s on this build**, on the same TACTSharp exception as
   the ten known-benign FDIDs. There is no working size route at all here.
4. **`/dbc/tooltip/wex` takes a hex blob, not an expression ID**, and has no
   validation.
5. **`/casc/moreinfo` is served `text/plain`** while returning HTML.
6. **`/map/list`'s listfile-fallback branch never triggers on this build** —
   every map has a WDT, so `wdtFileDataID == 0` and the always-zero
   `liquidFlow` path documented from source remain unexercised claims inside
   an otherwise measured route.

Not exercised, with reasons: `/map/download` (a 32768 × 32768 in-memory PNG),
`/dbc/export/all` (effectively a full extraction), and the four mutating
routes — `/dbc/export/alltodisk`, `/dbc/hotfixes/downloadLatest`,
`/dbc/reloadDefs`, `/dbc/reloadHotfixes` — which are held until there is a gap
between extraction runs.

**Corrected by that pass**, beyond the `build=` reversal: `/casc/fdid` returns
**200 with bytes** for encrypted FDIDs that `/casc/blp2png` 404s on, so the two
routes disagree about the same file by design; and `/dbc/labelColumns` is the
gameplay Label system, not a display-name registry.

`/dbc/updateDefs` **was** exercised (2026-09-19) after confirming from source
that it only clears in-memory caches.
