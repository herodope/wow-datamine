# Conventions

The pipeline's working rules in full, each with the measurement behind it.
`CLAUDE.md` carries the short form.

- **Never hardcode build numbers.** Read from `builds.json` or take as an
  argument. Builds change weekly during beta.
- **Product code and paths live only in `scripts/config.py`.** No scattered
  string literals.
- **Extraction must be resumable.** Checkpoint progress; never restart from zero.
- **Log, don't crash.** An unreadable file is expected. Record it with a reason
  (`encrypted`, `missing_key`, `bad_blte`, `unknown_format`) and continue.
- **Read WTL's source for route shapes.** Do not guess HTTP endpoints.
- **Every rule here is either measured or source-read, and must say which.**
  Reading the controller tells you what the code *can* do. Only a live
  response tells you what it *does*. Source-reading produces a hypothesis;
  measurement produces a rule. Label the difference and never let the first
  masquerade as the second.

  `docs/wtl-api.md` marks measured routes ✅ and keeps an explicit
  "still source-read only" list. Anything unexercised says so where it is
  written, not only in a verification log at the bottom, because the person
  who acts on a rule reads the rule and not the appendix.

  Example: the `build=` rule on `/dbc/meta/getMappings` follows correctly from
  `BuildRange.Contains`, yet read from source alone it points the wrong way.
  Only a live response shows that omitting `build=` returns two conflicting
  enum variants with the retail one first.

  Practically: source-read a route to know what to ask for, then ask. When WTL
  is not running, write the finding down as unexercised and **promote it only
  after a response confirms it**. A hypothesis recorded honestly is useful;
  a hypothesis recorded as fact is a trap.
- **A fix that works does not confirm the diagnosis that motivated it.**
  Symptom, fix and mechanism are three separate claims. A fix landing and the
  symptom going away establishes the first two. The **mechanism has to be
  measured on its own**, because a correct fix sitting next to a wrong
  explanation looks exactly like a correct fix sitting next to a right one —
  and it is the explanation that gets reused on the next problem.

  Example: identical `files.csv` hashes across 69876/69893/69913 are real
  data, not a broken inventory. `ListfileController.cs:208` intersects
  `Listfile.NameMap` with `CASC.AvailableFDIDs` before paging, so the route
  **only ever returns in-build files**, and only 2 of 610 shipped DB2 tables
  differ across those builds. An assertion that fails on identical hashes
  fires on correct data.

  Practically: when a symptom motivates a fix, **state the mechanism as a
  separate, testable claim and measure it separately.** "Does the fix remove
  the symptom" and "is my explanation of the symptom correct" are different
  experiments.
- **Match on declared FK relations, not on raw values.** Scanning every column
  of every table for a numeric value finds real references and coincidental
  collisions in the same pass, with nothing to tell them apart. Looking for
  spell 6343 that way returns `WMOMinimapTexture::ID`, `TaxiPathNode::ID` and
  `UiTextureAtlasMember::ID` alongside the genuine `SkillLineAbility::Spell` —
  those tables simply have a row whose own ID is 6343.

  Resolve the relation instead: `GET /dbc/relations/<Table>::<Column>` returns
  every column DBD declares as a foreign key to that one (157 for a spell at
  1.60.1.69913), and only those columns are worth reading. The difference is
  not cosmetic — a raw-value scan reports ~47 "references" for a classic
  Thunder Clap rank against a true count of 17.

- **An unscanned zero is not a clean result.** A detector that could not read
  the data returns exactly what a detector that read it and found nothing
  returns. Before reporting "0 findings", establish that something was
  actually capable of firing — and report coverage next to the count so a
  reader can tell the two apart without going to the source.

  `contamination.py`'s rules are narrow: `light_absent_map` reads only
  `Light`, `orphan_removal` only removed `Item` rows, and `dangling_map_ref`
  only columns named `instance_id`, `instanceid`, `continentid`, `mapid` or
  `map_id`. No spell table carries a map-reference column, so spell rows
  produce **0 findings and 0 applicable rules**. `scan()` returns
  `(findings, ref, coverage)` with a verdict of `scanned`, `not_scanned` or
  `no_data`, and both diff scripts render **NOT SCANNED — this is not a clean
  result** with a per-rule reason table. When rules *did* run, the report
  names which ones and which were never applicable, so even a genuine zero
  states its own scope. **A detector's output is only meaningful once you have
  shown the detector could have said otherwise.**
- **Diffs are the deliverable.** Raw extraction is a means to an end; the
  markdown reports are what this project produces.
- **Extract every table twice: with and without the hotfix overlay.** Plain
  DB2 output goes to `db2/`, hotfix-applied output to `db2_hotfixed/`.
  Keeping them separate is what makes a client patch distinguishable from
  live tuning — a value that moves only in `db2_hotfixed/` was hotfixed, one
  that moves in both shipped in the build. Collapsing them into a single
  output loses that distinction permanently.
- **Compare variants by content hash, not row count.** A hotfix can change a
  value without adding or removing a row — 5 of the 15 deltas in
  1.60.1.69913 (`GlobalStrings`, `Light`, `LightData`,
  `LightDataGlobalVolumeFog`, `LightParams`) have identical row counts and
  different data. A count-based check misses every one of them.
- **Key rows on the `ID` column, never on column 0.** DBCD emits CSV columns
  in DBD-definition order, so the ID column is **not reliably first** —
  `Achievement.csv` starts with `Description_lang` and has `ID` at index 3.
  A script keying on column 0 produces **silently wrong** diffs: keying
  `Achievement` that way collapses 114 of its 233 rows onto duplicate
  description strings and reports no change against a real 233 → 232
  delta. Always key on the column literally named `ID`, fall back to column 0
  only when absent, and state which key was used in the output. This applies
  to `diff_builds.py` as much as to `diff_hotfixes.py`.

  **A newly generated layout may have no column named `ID` at all.** WoWDBDefs
  names every column of a fresh layout `Field_<build>_NNN` and marks the ID
  with a `$id$` annotation instead. `UiModelSceneActor` at 1.60.1.70009 is
  the case: layout `B777EC3A`, ID column `Field_1_60_1_70009_002`. Keying on
  column 0 there collapses 1,007 rows onto 128 unique `ScriptTag` strings.
  `key_index(header, table)` tries three things in order: a column named
  `ID`, then the DBD's `$id$` column, then column 0. `diff_builds.py` keys
  each side on its own header, because the two sides of a schema change can
  name the ID column differently. Pass the table name at every call site.
- **Never field-compare across a schema change.** Two **independent** signals
  govern whether a positional comparison is valid, and they move separately:

  | Signal | Source | Tracks |
  |---|---|---|
  | layouthash | the DB2 header (`manifest.json`) | the **client's** record layout |
  | CSV header | WoWDBDefs definitions | the **definition** |

  A definition update can rename or add columns with **no layouthash change** —
  an `unk_<offset>` becoming a real name does exactly that — so checking the
  layouthash alone is not enough. Two gates:

  - `fields_comparable` requires **neither** to have changed.
  - `rows_comparable` requires neither the **layouthash** nor the **column
    count** to have changed.

  The gates differ because a column **renamed in place** keeps every value in
  its position: row-level change detection survives, while field names become
  ambiguous. A layout change or a column insert invalidates both — a positional
  diff then reports every row as changed and every field as different, which is
  noise dressed up as signal.

  **A table with a schema change must stay in the report even when its
  magnitude is zero.** Suppressed field diffs leave added/removed/changed all
  at 0, and a "skip anything with magnitude 0" rule would silently drop the
  tables that most need flagging. Report the change in the summary, in a
  dedicated section, and in the table's own section, stating which
  comparisons were suppressed and why. Keep a schema change with no row
  changes in the test fixtures.

  **A layout change with byte-identical data is still a schema change.**
  `diff_table()` checks the layouthash before skipping identical CSVs, so a
  table like `SpellDispelType` at 1.60.1.70009 (layout `47AA7AEB` →
  `3B574D4B`, retail 12.1.5's layout, `Mask` u8 → u16, all 11 rows identical)
  is reported as "data byte-identical (storage-only change)", with nothing
  suppressed.

  `diff_hotfixes.py` does **not** have this guard. It compares two exports of
  the same build, so the schema is normally identical on both sides — but if
  definitions shift mid-build (a `sync_refs.py` run plus `/dbc/updateDefs`
  between the two extractions), the same trap applies and the guard should be
  added there too.
- **Treat push IDs of `2^24 + recordID` as synthetic.** Not every push ID in
  the hotfix table is a real push. IDs equal to `16777216 + recordID` are
  derived arithmetically from the record ID by a bulk-injection path, and
  counting them as distinct pushes invents authoring events that never
  happened — the 4,218 `ItemSparse` additions in 1.60.1.69913 produce 4,218
  phantom "pushes" that way, which reads as incremental authoring when it was
  a single bulk load. **Real pushes for this build are in the 112xxx range;
  anything above ~16.7M is generated.** Label those records
  `bulk injection (N records, no real push attribution)` and report genuine
  push IDs separately. The two overlap: the hotfix table holds 4,218 synthetic
  and 194 real records for each of `ItemSparse` and `ItemSearchName`, but only
  114 of the real ones (all in `ItemSparse`) attach to rows the diff sees as
  added — those 114 were bulk-loaded *and* later hotfixed for real. Re-verify
  the base if a future build changes the scheme.
- **Cap concurrency at ~8.** CASC reads are IO-bound; oversubscribing thrashes
  the disk.
