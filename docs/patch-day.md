# Patch day

What to do when a new Forever build lands, on a hotfix-only downtime, and
after the diff. `scripts/patchday.py` automates steps 3-10 of the checklist.

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
8. Run `extract_db2.py`, then `inventory.py`, then `extract_gametables.py`,
   then `build_db.py` (in that order — GameTable discovery reads
   `files.csv`, and the database loads all three). `patchday.py` step 8
   does all four.
9. Diff against the previous Forever build. **WTL must still be running**:
   `diff_builds.py` compares CASC content keys through `/casc/diff` and
   diffs changed Lua/XML/TOC files. That is the only check that sees a file
   rewritten under an unchanged FDID. It caches to
   `out/<to>/content_diff_<from>.json`, which `render_patchnotes.py` reads.
   With WTL down and no cache, both reports say **not measured**.
   `inventory.py` cannot see content-only changes: `files.csv` has no content
   hash.
10. Commit `builds.json` and the new report.

## Hotfix-only day (downtime with no new build)

Blizzard takes the realms down "for fixes" and the version endpoint still
serves the **same `buildConfig`**. Nothing was patched; the hotfix overlay
moved. The build-to-build diff has nothing to say, and the standard hotfix
report (overlay vs shipped client) answers the wrong question — it restates
every hotfix ever applied to this build, so today's wave is invisible inside
it.

Diff the overlay against **its own previous state** instead. The baseline is a
copy of `db2_hotfixed/` taken *before* the re-extract, so it must be made
first and cannot be recovered afterwards:

```bash
B=1.60.1.69913
cp -rp out/$B/db2_hotfixed out/$B/db2_hotfixed.snapshot-$(date +%Y%m%d)
python scripts/extract_db2.py --build $B --restart
python scripts/render_patchnotes.py --build $B --since <YYYYMMDD>
```

`--since` is the third renderer mode. Both sides are live data, so on that page
`rows_plain` is **the previous live state, not the shipped build**.

- **Snapshot before extracting, always.** A re-extract overwrites
  `db2_hotfixed/` in place. Skip the copy and the previous live state is gone
  — the same class of unrecoverable miss as not capturing `buildConfig`.
- **Log out of the client before extracting.** WTL's `HotfixManager` globs the
  exact filename `DBCache.bin` and never the session buffer
  `DBCache.bin<pid>.tmp`, which the client holds under an exclusive lock, so a
  wave measured mid-session is a lower bound.
- **A re-extract on an unchanged build is cheap.** 13s for all 1161 tables,
  because WTL serves the exports from cache. There is no reason to extract
  selectively.
- **Do not drive the wave diff off `/dbc/hotfixes/list`.** Scan every CSV.
  Measured on 69913: the list reported new records in **19** tables while
  only **8** tables' exported rows actually moved, and it named **5**
  `BroadcastText` records against **11** rows that appeared.
- **`firstDetected` is when WTL first saw a record, not when Blizzard pushed
  it.** It is an upper bound on push time. The wave page uses the baseline
  directory's mtime as the cutoff and says so; do not present the window as
  push times.
- Re-run `diff_hotfixes.py`, `inventory.py` and `build_db.py` afterwards — the
  cumulative report, manifest and database all describe the old overlay until
  you do. `write_manifest()` carries forward the keys it does not own, and
  `inventory.py` recomputes a missing baseline from `files.csv`.
  `inventory.py` will print its "file set did not change" banner; on a
  hotfix-only day that is the correct result, not a failure.
- **`build_db.py` cannot replace `wow.db` while `mcp_server.py` is running.**
  Windows holds the file open even for a read-only connection, `os.replace`
  fails with `WinError 5`, and the rebuilt database is left as `wow.db.tmp`.
  Stop the MCP server, then re-run.

## Two products on one build ID

A build ID can ship on more than one product. 1.60.1.70532 shipped first on
the Forever stress test (`wowf`, flavor folder `_forever_`) and then on the
beta, with the same version string and a different `buildConfig`.

- **`builds.json`**: run `fetch_builds.py --product <code>` for a product
  other than `config.PRODUCT_CODE`. Its entry is keyed `<product>:<buildId>`
  and carries a `product` field, so it cannot shadow or block the default
  product's entry for the same ID. `patchday.py` ignores those entries when
  picking the previous build.
- **`out/`**: extract the other product under the bare version, because every
  WTL route takes the bare version. Once its diffs are written, rename the
  folder to `out/<product>-<version>/`. That name fails the Forever version
  pattern, so no default or previous-build lookup picks it up.
  `query.py --build <product>-<version>` still opens it.
- **WTL's own caches** are keyed by bare version: `dbcs/<version>/` and
  `manifests/<version>.wtlm`. Rename them to the same `<product>-<version>`
  label. Otherwise a second product on that version is served the first
  product's DB2s and manifest. WTL writes a manifest for the loaded build when
  none exists for its version.
- To re-run a WTL-backed step for a labelled build, rename all three back to
  the bare version first.

### Hotfix caches with the same header build are merged

WTL loads every file named exactly `DBCache.bin` under the install, plus
`caches/*.bin` in its own folder. It merges all caches whose header carries
the same build ID into one overlay. This is source-read, not measured:
`HotfixManager.LoadCaches` and `ParseCache` key readers on the header build,
and `CombineCache` folds each further file into the existing reader.

When two products' clients are on the same build ID, a hotfixed extraction of
either one therefore returns both products' hotfixes combined, with no error.

- **Before starting WTL for one product, move the other product's
  `DBCache.bin` out of the install.** Check that the header build of every
  `DBCache.bin` left under the install is the one you mean to extract. The
  header build is the little-endian uint32 at byte offset 8, after `XFTH`
  and the format version.
- **Restore the moved file afterwards** and compare hashes. The client reads
  it on its next login.
- **`hotfixes.csv` cannot tell the two products apart.** WTL files each push
  once, under the first cache that showed it, and both products report the
  same version string. Attribute a push by its `firstDetected` time, which
  says which cache delivered it first. A push filed under one product can
  still be live on the other.

## After the diff

`python scripts/check_findings.py --build <version>` re-tests the dated
predictions in the local findings record (`config.FINDINGS_DOC`,
`.claude/notes/state-log.md`). It is not part of the repo; without it the
script exits with a message. RESOLVED or FALSIFIED findings are updated there
by hand.
