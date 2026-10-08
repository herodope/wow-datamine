# WoW Forever Datamining

Local pipeline for extracting, parsing, and diffing World of Warcraft: Forever
client data across beta builds.

---

## Current state

WTL (wow.tools.local) builds and runs, and has successfully loaded the current
build from local CASC. CASC reading is **verified working** — do not re-litigate
this layer.

Outstanding:
- [x] DB2s extracted in WTL
- [x] DBD directory configured — `definitionDir` points at
      `vendor/WoWDBDefs/definitions`, no longer the remote manifest
- [x] `builds.json` seeded with 1.60.1.69913 (5a6cc43)
- [x] Build filter corrected — `FOREVER_MIN_BUILD_ID` lowered to 69876 and
      near misses now warn loudly instead of being dropped. See
      **CRITICAL: build filtering**
- [x] The two earlier builds, 69876 and 69893, are extracted and in
      `builds.json`, so every Forever build seen has a diff.
- [x] **1.60.1.70009 (2026-09-24) is the first content build.** The builds
      before it changed 0–2 tables each. 70009 changes 155 of 611 tables,
      moves the file set (+240 / −401), and moves encryption (+13). See
      `reports/patchday_1.60.1.70009.md`. It was extracted **before the client
      was launched on it**, so its hotfix overlay is unmeasured.
- [x] **70009's hotfix overlay was measured on 2026-09-24**, from a
      `DBCache.bin` with a build-70009 header written at 21:12. That is a
      lower bound: the client was running again during the extract, and its
      `.tmp` session buffer is never read. The overlay has 11 tables and
      only **2 real pushes**. See **Hotfixes are per build** under **Key
      facts**.
- [x] **1.60.1.70058 (2026-09-29) is a client-code build.** All 610 shipped
      DB2s are byte-identical to 70009, and the FDID set and encryption did
      not move. `/casc/diff` found 79 files whose **content** changed: 52
      shaders, 17 DLLs, and 10 gamepad FrameXML files (world-map crosshair
      coords, gamepad-aware map highlights and labels, tooltip nil-guard).
      **`inventory.py` cannot see content-only changes**, because
      `files.csv` has no content hash. `diff_builds.py` now runs
      `/casc/diff` itself (see step 9 of the patch-day checklist). The overlay was measured the same day: new pushes 112262
      (weather/rain particulates), 112263 (Night Watchman's Torch), 112264
      (spam filters), 112271. See `reports/patchday_1.60.1.70058.md`.
- [x] **1.60.1.70170 (2026-10-02) is the second content build.** 174 of 610
      tables changed (+4,740 / −5,464 / ~3,060 rows), 4,210 files changed
      content, the file set moved (+346 / −168), and encryption moved (+9,
      of which +5 are unknown keys). Three schema changes:
      `ChrRacesCreateScreenIcon`, `CreatureImmunities`, and
      `UiModelSceneActor` (columns named, not moved). The overlay was
      measured from a `DBCache.bin` written at 10:31 local with the client
      **closed**, so it is not a mid-session lower bound. It has 24 tables
      and 5 real pushes: 112078, 112323, 112340, 112347, 112349. 112347 is a
      live talent-tree pass covering `TraitNode*`, `TraitDefinition` and
      `TraitEdge`. **The first summary of this build reported SoD and
      unreachable spells as class changes.** See *Reporting gap and
      liveness* under **Season of Discovery tags**.
- [x] **1.60.1.70245 (2026-10-07) is a hotfix build.** All 610 shipped DB2s
      are byte-identical to 70170. `PlayerExpectedStat` shows only a
      WoWDBDefs rename. The file set and encryption (5,057) did not move.
      519 files were rewritten: 500 shaders, 17 DLLs, and 2 Lua bug fixes
      (the whisper target pattern `%w+` → `%S+`, and `GenerateFlatClosure`
      in talent tooltips). The overlay was measured with the client closed.
      All 5 of 70170's real pushes carried over, unlike 70009. About 11 new
      pushes, 112369–112463:
      - Gnomeregan and BFD `DungeonEncounter.DifficultyID` 201/1 → 0/1.
      - The Booty Bay Bruiser's Buckshot item is unhooked from its spell.
      - New `Cfg_GameRules` 251–253 and `Cfg_SuperDistrict` 14.
      - `ModifierTree` 459192 (beta/PTR/QA/entitlement any-of) gains a
        `WORLD_STATE_EXPRESSION` branch.

      **Diff a new build's overlay against the previous build's overlay**
      with `render_patchnotes.py --since out/<prev>/db2_hotfixed`. The
      hotfix report re-lists every carried-over push. See
      `reports/patchday_1.60.1.70245.md`.
- [x] **1.60.1.70291 (2026-10-08) is the third content build.** It was
      first extracted **before anyone could log in**, with an empty overlay.
      The overlay was **measured the same day** from a `DBCache.bin` with a
      build-70291 header, written at 18:02 local with the client **closed**,
      after `/dbc/reloadHotfixes` and `extract_db2.py --restart`. It has 7
      tables and 4 real pushes. 112078 carried over, and the new ones are:
      - 112469: Rend Flesh 10.5 → 12.5. Highland Venom −15 → −20, with its
        duration going from 15 min to 10 min.
      - 112486: Sacred Cleansing's radius moves from the min column to the
        max column.
      - 112504: twelve Magram/Necrokhan quest weapons go from ilvl 43 to 42.

      It also adds 3 `TactKey` rows (8341–8343) and bulk-injects about 4.5k
      `ItemSparse`/`ItemSearchName` records. The October 8 dungeon-XP change
      is server-side and invisible here. **Use `hotfix_<build>.md` for a
      content build's hotfixes, not the cross-build `--since` wave.** The
      wave mixes in client changes, and its banner now says so. 157 of 611
      tables changed (+4,966 / −321 /
      ~2,283 rows). There is one new table, `UICinematicIntroInfo`, and no
      schema changes. 5,504 files changed content. Encryption moved by 36
      files, from `EncryptedUnknownKey` to `EncryptedButNot`. **193,186 FDIDs
      left the build** (+201): retail WMOs, `item/objectcomponents`,
      `item/texturecomponents` and `world/expansion01`–`11`. That is a real
      retail-asset strip, not a listfile artifact. WTL loaded the full
      listfile.
      - **The 70170 warrior hotfix pass (112347) is folded into the client.**
        `plain_` 70291 equals live 70245 byte for byte for Bloodthirst,
        Booming Voice, Raging Blows, Gore Drinker and the rest.
        `diff_builds.py` compares client to client, so it reports them as new.
        Of 179 mechanically changed player-facing spells, **16 are folded
        hotfixes and 163 are new**. To separate the two, diff the new
        `plain_` against the previous build's **live** tables.
      - New in 70291: ranks 1–5 of the base nukes and heals are re-curved
        (Fireball rank 1 per level goes 0.6 → 0.2, and ranks 6+ are
        untouched). Every armor aura moves from `EffectAura` 22 to 674:
        Devotion Aura, Sunder Armor, Faerie Fire, Expose Armor, Curse of
        Recklessness and Mark of the Wild. 674 is not named in
        `SpellAuraNames`, so do not decode it. Mana Tide Totem is trained at
        25 instead of 40. Penance mana costs go up. Water Shield loses its
        15 s category cooldown.
- [x] **Talent values are now attributed to their spells.** The gap was
      found against foreverchanges.pro's 70170 notes on 2026-10-08.
      Redoubt (6 → 4% per rank), Deflection (2 → 1%) and Improved Slam
      (effect-2 curve deleted live) changed only in
      `TraitDefinitionEffectPoints` / `CurvePoint`. `player_notes.py` reads
      them as a "value per rank" field. A deleted curve falls back to the
      spell's own points.
- [x] **Build-diff pages lead with a player edition** (`player_notes.py`,
      2026-10-08). It goes class → spec → spell, with Buff / Nerf / Change /
      New / Removed, before → after values, provenance, and a per-class
      summary. The old table audit sits in a collapsed appendix.
      Contamination stays outside it. It is judged **live against live**
      across four states (old/new × client/live). A client change that
      matches the old live value is **FOLDED**: it is listed as "already
      live" and kept out of the counts. Buff and nerf are inferred only for
      fields with an unambiguous direction. Raw flags and masks are dimmed
      and never headline a class. The hotfix and `--since` pages are not
      converted yet.
- [x] **Rage mechanics, traced 2026-10-08 across 70058–70291.** Warrior
      crit rage is in the data. The bear version is not.
      - **Warrior crit rage is `Rule of Rage (DND)` 1322574.** It is new at
        70170, on skill line 95 Defense with `ClassMask` 1 (warrior only), and
        has one effect: `EffectAura` 4 (dummy). The server applies it, so the
        formula is server-side, but the number is client data. 70170 shipped
        **10**, the 10-02 hotfix set it to **100**, and the 70291 client
        ships 100 (folded). 100 matches "100% more Rage". The shipped 10 is
        unexplained and matches neither 75 nor 100. Do not assert units.
      - **Dual Wield Specialization 23584, off-hand rage** (talent effect 1):
        70058 had 20/40/60/80/100. The 70170 client shipped **2/4/6/8/10**,
        a copy of effect 2's hit curve. The hotfix corrected it to
        10/20/30/40/50, and 70291 ships that. This is the value
        foreverchanges.pro could only source to Aidan Moon.
      - Lingering Rage 1323964: the decay delay is a talent value,
        2000–10000 ms. The decay itself is server-side.
      - Unbridled Wrath: "1 Rage whatever you wield" is **not visible**. Its
        energize spell 12964 gave 10 (1 Rage) in every build, so the old
        two-hander rule was server-side.
      - **Bear crit rage (75%) is not in the client.** No bear counterpart to
        Rule of Rage exists in any build. `EffectAura` 668, added at 70170
        with value 0 to Bear Form 5487, Dire Bear Form 9634, Defensive
        Stance 71 and Righteous Fury 25780, is **not** a rage aura:
        Righteous Fury is a paladin aura. 668 is unnamed, so do not decode
        it.
      - Unannounced at 70291: Bear Form (Passive2) 21178 goes from
        `EffectAura` 10 value 30 to **50**. That is bear threat, not rage.
- [x] **`spell_reach` dropped class passives whose acquire method moved to
      3.** Rule of Rage is `AcquireMethod` 2 at 70170 and 3 at 70291, and
      nothing teaches it. Fixed with the narrow `class_passive` root (see
      **Player reachability**). `player_notes.py` now files a spell under the
      class its `SkillLineAbility.ClassMask` names when its skill line has no
      class of its own, so Rule of Rage goes under Warrior and not
      "Professions & other".
- [x] **`check_findings.py` at 70291 with the overlay measured:** #1
      FALSIFIED (`TimeEventData` empty in client and live; decide after
      2026-10-12), and #3, #4 and #5 UNRESOLVED. #3 is still 481
      live-only, and #5 still has all 75 stubs. #2 and the fixed parts of
      #5 moved to **Closed findings**. #5's old check called a partial fix
      RESOLVED.
- [ ] **`spell_reach` drifts with the client's item cache.** 9,070 → 9,098
      reachable at 70245 on identical client data. All +28 are `item` roots
      (3,041 → 3,069), from 163 `ItemSparse` rows the client cached under
      synthetic push IDs. Consider rooting items on shipped `ItemSparse`
      plus real-push rows only.

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

### Known builds

| Version | Build | First seen | Build config | CDN config |
|---|---|---|---|---|
| 1.60.1 | 69876 | 2026-09-16 18:23 | `e7fab7248766e9e7daddb3b6083c9c3c` | `272d201d5b2d6fec8fdb4aa59b2a9eea` |
| 1.60.1 | 69893 | 2026-09-16 23:14 | `5aa0eecfa8d49f5ad01221dbc8601144` | `c39a363b4a67449d8f16736dba987a43` |
| 1.60.1 | 69913 | 2026-09-18 03:02 | `6c0df97e8e481a9a41600e373367c200` | `5525ea1ce6668e895569c89c2d6a154c` |
| 1.60.1 | 69977 | 2026-09-23 (captured) | `3bd89ce2721f7c75e7525dc83741076f` | `ed440cc894be6d92a02f076fa00ce2f5` |
| 1.60.1 | 70009 | 2026-09-24 (captured) | `05215079e3905ef5922ae0b03ffefb73` | `9b3c456dbb837d133a026d380c7c13e9` |
| 1.60.1 | 70058 | 2026-09-29 (captured) | `8f8ffb0634e955e8ff585ebaf9727509` | `a9028f7cf71de20b3915a042b23afd83` |
| 1.60.1 | 70170 | 2026-10-02 (captured) | `d3f2837397a016e380ea51c4e1e78d1d` | `032ffa3587e5f762df7c6ef823e17596` |
| 1.60.1 | 70245 | 2026-10-07 (captured) | `0bf141260c698dfc9cb3b5b7947b78c3` | `f39eaf0d23ad9a0f85fdbfc2713d3ed3` |
| 1.60.1 | 70291 | 2026-10-08 (captured) | `e8dd824cf6c3d96cd01f804ca2ea5a63` | `00df9e43531518b653a35289488c9bbb` |

These hashes are the only way to reach a build after Blizzard rotates it off the
live version list. Capture them every patch day, before anything else.

**Hotfix waves on 69913** (realm downtime, no client patch — see *Hotfix-only
day* under the patch-day checklist):

| Wave | Records | Real pushes | Tables moved | Report |
|---|---|---|---|---|
| 2026-09-19 18:08 / 19:07 | 26,542 | (baseline) | — | `reports/hotfix_1.60.1.69913.md` |
| 2026-09-21 17:45 / 19:33 | 571 | 112156, 112189, 112200, 112201, 112203, 112209 | 8 | `reports/hotfixwave_1.60.1.69913_since_20260919.html` |
| 2026-09-22 12:18 / 13:31 | 75 | 112210 | 3 | `reports/hotfixwave_1.60.1.69913_since_20260921.html` |

The 09-22 wave is small and worth reading as a shape rather than as content:
4 items given display data, 5 BroadcastText gossip rows, and one real push
(112210) that invalidated 51 QuestObjective / QuestPOIBlob / QuestPOIPoint
records **this build does not carry** — `QuestObjective` exports 204/empty
and `QuestPOIBlob` holds 54 rows nowhere near the 564183+ range invalidated.
Inert here. Per the ID-range rule under *Retail contamination*, that is a
measurement and not a contamination verdict.

It was also captured with the client still running. WTL's `HotfixManager`
globs the exact filename `DBCache.bin` and never the session buffer
`DBCache.bin<pid>.tmp`, which the client holds under an exclusive lock, so a
wave measured mid-session is a lower bound. Log out before the extract.

The 09-21 wave is the first measured on this repo: 31 records under six real
push IDs, 540 bulk-injected item records under synthetic IDs, and 8 tables
whose exported rows actually moved.

69876 and 69893 come from WTL's archive (`GET /build/list`) and are **already
off the live version list** — measured 2026-09-20, the versions endpoint
returns only 69913, once per region. They are reachable now solely because WTL
recorded them.

> **A `cdnConfig` rotates independently of the build.** WTL recorded
> `1f946798ccc0e9281f9ac94e3539ada9` for 69913 on 2026-09-18; the live
> endpoint served `5525ea1ce6668e895569c89c2d6a154c` for the same build on
> 2026-09-20. Both are real. The `buildConfig` is the stable identity; treat a
> changed `cdnConfig` for an unchanged `buildConfig` as normal, and keep
> whichever you captured rather than assuming one supersedes the other.

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

**Why this matters more than the threshold.** The gate was `69900` until
2026-09-20 — a round number chosen below the only build then known. It rejected
builds **69876 and 69893**, which are real Forever builds, and it did so on the
same code path and with the same silence as a Mists of Pandaria beta build. The
two were invisible for four days, and with them the possibility of producing
the first diff, which is this project's deliverable. They have since rotated
off the live version list and survive only in WTL's archive.

The defect was never the number. It was that a near miss and a different game
were indistinguishable in the output. **If the threshold ever needs raising,
something is wrong — lower it to match reality instead**, capture the build's
`buildConfig`/`cdnConfig` immediately, and note that a near miss on a *higher*
ID than anything seen is impossible by construction.

> **The `>= 69900` half of this rule is measurably wrong and excludes real
> Forever builds.** ✅ measured 2026-09-20 against `/build/list`: three builds
> match `^1\.6\d\.` — 69876, 69893 and 69913 — and the threshold rejects the
> first two. The version lines present on `wow_classic_beta` are 1.13, 1.60,
> 2.5, 3.4, 4.4 and 5.5, so the version pattern already discriminates
> completely; the build-ID gate adds only false negatives, and it silently
> discarded the two builds that would have made the first diff possible.
>
> The threshold was chosen as a round number below 69913 before any earlier
> build was known to exist. The safe correction is to lower
> `FOREVER_MIN_BUILD_ID` to **69876** — keeping a secondary check against a
> future 1.6x collision while admitting every build actually observed — rather
> than deleting the gate. **That is a decision for the repo owner**, and
> `scripts/config.py` is unchanged pending it. Until then, `diff_builds.py`
> will refuse both earlier builds, which is the documented behaviour working
> as specified against a specification that is wrong.

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
`hashlib` and `concurrent.futures`. Audited across all 16 scripts: `mcp` is
the single third-party import in the repo, and it is needed only by
`scripts/mcp_server.py`.

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

## WTL API gotchas

Full route reference: [`docs/wtl-api.md`](docs/wtl-api.md). These are the traps
that produce **silently wrong output** rather than an error. All measured
against a live instance on 1.60.1.69913.

- **The hotfix parameter is `useHotfixes=true` on API routes.** The browse page
  URL spells it `hotfixes=` and rewrites it client-side before calling the API.
  Sending `hotfixes=` to an API route binds nothing, returns **200**, and
  silently yields non-hotfixed data — measured at **10,556 rows with
  `useHotfixes=true` vs 6,622 without** on `itemsearchname`. **NEVER use the
  page's spelling in scripts.** This is the failure that would fill
  `db2_hotfixed/` with plain DB2 data and never report a problem.
- **Iterate the build-filtered table list.** `/listfile/db2s` unfiltered returns
  **1342** tables — everything WoWDBDefs defines. Build-filtered
  (`?build=<version>`) returns **1161**. Use the filtered list or ~181 exports
  come back empty.
- **Two distinct DataTables envelope shapes exist.** `/dbc/info` and `/dbc/data`
  carry an `error` key; `/dbc/hotfixes/list`, `/listfile/files` and
  `/build/table` do **not** — reading `d["error"]` on those raises `KeyError`.
  Use `.get("error")` for a single code path.
- **204 and 404 mean different things.** `204 No Content` is
  defined-but-zero-rows in this build (e.g. `modifiedcraftingitem`); `404` is
  not in this build at all. Handle them distinctly — a 204 is not a failure.
  **The two variants of a table can disagree:** a table can return 204 with
  `useHotfixes` off but 200 with rows when on — it exists *only* as live
  hotfix data and was never shipped in the client build. `extract_db2.py`
  records this as `resolution: "hotfix_only"`, distinct from `empty`.
  `TimeEventData` in 1.60.1.69913 is the reference case, and the only one in
  1161 tables — which is exactly why it is easy to miss. Never decide a table
  is empty from the plain request alone.
- **`hotfix_delta` is computed from a content hash, not row counts.** Five
  tables in 1.60.1.69913 — `GlobalStrings`, `Light`, `LightData`,
  `LightDataGlobalVolumeFog`, `LightParams` — have **identical row counts in
  both variants but different values**. A count-based comparison reports no
  change for all five.
- **`/dbc/hotfixes/list` returns zeros when given no query string.** It
  short-circuits on `!Request.QueryString.HasValue`, so a bare request looks
  like "no hotfixes exist". Always pass `?length=N`.
- **Default locale on DB2 routes is `All_WoW`, not `enUS`.** Pass `locale`
  explicitly rather than relying on the default.
- **`type:unk` returns 0 despite 99,348 rows carrying that type.** The `type:`
  search token looks up `Listfile.TypeMap`, which has **no `unk` bucket**, so
  the lookup fails and the search falls through to a plain substring match on
  filenames — matching nothing. The `content_type` column gets `unk` from a
  separate `TryGetValue` fallback. Trusting the search token would report
  nothing to classify when in fact 99,348 files needed it. Read the column,
  never the token.
- **`recordsFiltered == recordsTotal` proves NameMap *membership*, not a
  non-empty name.** `/listfile/files` iterates `Listfile.NameMap`, so its
  unfiltered `recordsFiltered` counts rows that are *in* the map — including
  those whose name is the empty string. At 1.60.1.69913 both counts are
  1,441,771, which reads as "nothing is unnamed", but 19,583 of those rows
  carry an empty filename. **Count empty values in the filename column
  (index 1); do not infer naming coverage from the two totals matching.**
  This exact wrong inference was made here once and committed.
- **WTL exposes no per-file size over HTTP.** `/size/data` only aggregates
  (`results[type] += fileSize`), and every other route works from
  `Listfile.NameMap`, which carries no size. Real per-file sizes live in the
  CASC indices and require parsing `Data/data/*.idx` directly — that is a
  separate piece of work, not an API call. `files.csv` emits an empty `size`
  column for schema stability.
- **`/dbc/updateDefs` is the "Update WoWDBDefs & clear cache" button.** With our
  local `definitionDir` it skips the download half and only does
  reload-and-clear — which is exactly the half needed after `sync_refs.py` has
  refreshed the clone on disk.
- **Always pass `build=` to `/dbc/meta/getMappings`.** Without it, columns
  whose enum was re-versioned return **both** variants and the retail one
  comes first. Measured live on 1.60.1.69913: unfiltered, exactly 2 of 606
  mappings carry colliding values — `Weather::Type` returns 12 entries for
  values 0–5 (retail `0 None, 1 Clear, 2 Rain…` ahead of Classic
  `0 Clear, 1 Rain, 2 Snow…`) and `SpellEffect::Effect` returns 361 with one
  duplicate. A decoder taking the first match on value silently labels every
  Forever weather row with **retail** names. With `build=1.60.1.69913`:
  **0 colliding mappings and 0 empty ENUM/FLAGS mappings** — the filter is
  what selects the Classic-era variant.

  The mechanism is worth knowing because it constrains what the filter can do.
  `BuildRange.Contains` compares **componentwise**, not lexicographically:
  `major >= min.major && major <= max.major`. Forever is `1.60.1.69913`, so
  `major = 60` fails `<= 12` against the `Vanilla` preset and `expansion = 1`
  fails `>= 2` against every TBC-and-later preset. **A 1.60.x build matches no
  preset range that exists**, so `build=` can only ever *drop* build-tagged
  entries, never select one. That gives the right answer here only because the
  era-specific variants are the tagged ones and the Classic defaults are
  untagged — an authoring convention, not a guarantee. If a future definition
  sync tags the Classic variant instead, the filter would strip it and leave
  the column empty. **Re-run the collision check after every `sync_refs.py`:**
  with `build=` set, no ENUM/FLAGS mapping should come back with zero entries.
  (An earlier revision of this file said the opposite — omit `build=` — from
  reading `WeatherType.dbde`'s six build-tagged lines without noticing the six
  untagged ones below them, and without checking a live response. Corrected
  2026-09-20 against a running instance.)
- **`output=png` on `/map/tile` is read only inside the `type == "adt"`
  branch.** Every BLP falls through to the tail path, which unconditionally
  returns `application/octet-stream` holding raw RGBA bytes — `targetSize ×
  targetSize × 4`, no header — with a **200**. Requesting a PNG minimap tile
  succeeds and yields something no image decoder will open. Use
  `/casc/blp2png` for a PNG; use `/map/tile` only for pixels to composite.
- **A 500 from WTL usually means bad input, not a dead server.**
  `Startup.cs` registers `UseDeveloperExceptionPage` only under
  `IsDevelopment()`, and there is no handler registered for any other
  environment. `launchSettings.json` sets `ASPNETCORE_ENVIRONMENT=Development`,
  so launching through `run-wtl.ps1` (`dotnet run`) **does** get the dev
  exception page — a 500 arrives as `text/plain` with a stack trace, which is
  worth reading. Run the built exe without that variable and the same throw
  returns a bare 500 with an empty body instead. Either way the reason is
  never a structured error field. `/casc/blp2png` on a non-BLP, `/dbc/tooltip/item` on a
  `RandPropPoints` miss or unknown `SubclassID`, `/map/wdtMask` on an unknown
  layer and `/map/download?layer=5` all reach it. Scripts must treat 500 as
  possible bad input and keep going, not as "WTL is down" — probe
  `/casc/buildname` to tell the two apart.
- **`/casc/blp2png` 404s on encrypted files.** It reads the first four bytes
  and returns `NotFound()` when all four are zero, which is exactly what a
  file with a missing key decodes to. A 404 there means *unavailable*, not
  *absent* — cross-check the `encryptionStatus` column on `/listfile/files`
  before reporting a texture as missing from the build.
- **The tooltip controller is non-hotfixed and cannot be pointed at another
  build.** Every load is the two-argument `GetOrLoad(name, CASC.BuildName)`,
  i.e. `useHotfixes: false`, with no parameter to change it.
  (`FindRecords(…, true)` looks like a hotfix flag but that fifth argument is
  `single`.) So `/dbc/tooltip/item/<id>` on any of the hotfix-only `ItemSparse`
  additions falls through both `ItemSparse` and `ItemSearchName` and returns
  `Name: "Unknown Item"`, `HasSparse: false` — a plausible-looking answer for
  an item that exists. Anything hotfix-only must be read through
  `/dbc/export`, `/dbc/data`, `/dbc/peek` or `/dbc/find` with
  `useHotfixes=true`.

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

Encrypted-file history (the `EncryptedUnknownKey` / `EncryptedButNot` split
matters as much as the total; see finding #6):

| Build | Files | Encrypted | UnknownKey | ButNot |
|---|--:|--:|--:|--:|
| 69876 – 69977 | 1,441,771 | 5,035 | 3,371 | 1,664 |
| **70009** | 1,441,610 | **5,048** | **3,385** | 1,663 |

### Known benign errors

These FDIDs throw "Specified argument was out of the range of valid values"
during analysis. Malformed or placeholder entries, non-blocking:

```
5014017, 5014019, 5014022, 5014025, 7018189,
7079572, 7576057, 7576058, 7576059, 7576060
```

Log and continue. Only investigate if the set changes between builds.

---

## Retail contamination

`wow_classic_beta` is a recycled product code and Forever shares tooling with
retail, so retail-era rows leak into tables that should hold only Classic
content. They get pruned over time. **Track them per build: a new one appearing
is a signal, and one disappearing tells you Blizzard noticed.**

`scripts/contamination.py` runs in three places, all through `scan()`:
`diff_hotfixes.py` (the hotfix report), `diff_builds.py` (the build diff) and
`render_patchnotes.py` (every HTML mode, including `--since` waves).
`diff_builds.py` hands it the **shipped** `db2/` tables; the other two hand it
the **live** overlay first. The rules' reference data, and the orphan base rate
below, differ by variant accordingly. Rules, ranked by how much they actually
discriminate:

| Rule | Confidence | What it catches |
|---|---|---|
| `dangling_map_ref` | **MEDIUM** (added rows) / HIGH (removed rows) | A row references a Map ID absent from this build. Was HIGH on the 69913 measurement (1 of 233 `Achievement` rows, near-zero false positives). **1.60.1.70009 broke that**: 8 added `Achievement` rows hit it, and the Shaper's Terrace, Alcaz Prison, Hyjal Summit and Barrow Deeps boss statistics are unreleased **Forever** dungeons whose `Map` rows are withheld, not retail leftovers |
| `withheld_map_ref` | LOW | The same, but the absent map has `MapDifficulty` rows in this build, so the map exists and only its definition is withheld. Maps 2994 and 3001 at 70009. For watching, not a contamination verdict |
| `light_absent_map` | HIGH | A `LightParams` ID whose only referencing `Light` rows sit on absent maps |
| `orphan_removal` | MEDIUM | Any removal of **≥ 1** `Item` row with no `ItemSparse`/`ItemSearchName` row. No threshold; the finding states the count, and the reader judges whether it was coordinated (push 112078 pulled 75 at once) |

**Appearing vs leaving.** Both reports now split findings in two. Rows that
were **added** (or values that are newly referenced) are *appearing*. Rows that
were **removed** (or `LightParams` values being **replaced**) are *leaving*:
contamination being cleaned out, which is a fix, not a new finding. The first
70009 report mixed the Zaela removal into the same table as the new dungeon
statistics, which made the cleanup read like new contamination.

**An absent map no longer means retail.** Before 70009, every reference to a
missing map was retail. From 70009 on, Blizzard withholds Forever's own `Map`
rows too, so the two cases look identical to this rule. Judge each hit by
name and context. Treat a MEDIUM `dangling_map_ref` as a lead to check, not a
verdict.

**Three rules deliberately not implemented**, because measurement showed they
do not discriminate in this build:

- **ID falls in a "modern retail range".** `Achievement` IDs run 627–64159 with
  160 of 233 rows above 61000, so an ID-range test flags most of the table. ID
  ranges are supporting evidence only, never a trigger.
- **Item row has no ItemSparse/ItemSearchName data.** Measured at
  **1.60.1.70009**: **12,594 of 31,818** `Item` rows (39.6%) in the shipped
  `db2/`, **8,257 of 31,818** (26.0%) live. At 69913 it was 12,504 of 31,675
  shipped and 8,124 of 31,603 live. `ItemSparse` ships incomplete and arrives
  by hotfix, so orphanhood alone would flag a quarter to two fifths of the
  table. The rule fires only on orphans being **removed**, from one row up,
  and reports how many. (An earlier "8,286 of 31,675" matched neither variant
  and has been withdrawn.)
- **The row is unreferenced.** Proposed after the 461xxx Thunder Clap ladder
  turned out to be defined but unconsumed (finding #7). Measured before
  writing it, across four ways of deciding which FK columns count as the
  entity's own definition tables:

  | Framing | Flags | Separates the two ladders? |
  |---|---|---|
  | coverage ≥ 50% of spells = definition | 10.4% | **no** — 461xxx scores 7 each |
  | table name starts with `Spell` | 63.9% | yes |
  | hand-picked player-reachability columns | 64.2% | yes |
  | `SkillLineAbility` membership alone | 80.3% | yes |

  Every framing that discriminates flags **two thirds to four fifths** of the
  table — worse than the orphan test it was meant to generalise. The one with
  a tolerable rate does not discriminate at all, and the coverage distribution
  has no natural break to pin a threshold to: it runs smoothly from 99.95%
  down to 0.00%.

  The decisive measurement is that the rate is **flat across ID ranges**:
  **63.5%** of classic-era spells (ID < 100k) are unreferenced and **63.5%** of
  modern-ID spells (≥ 400k) are too. Being unreferenced carries no information
  about whether a row is retail-era. Most spells in any build are NPC
  abilities, triggered effects, item procs and internal auras that nothing is
  supposed to reference — unreferenced is the **normal** state.

  What is informative is a **paired** comparison: two rows with the same name
  and rank where one is fully wired and the other is not. That is a judgement
  about a specific pair, not a population test, and it does not reduce to a
  confidence level. `contamination.inbound_references()` exposes the
  measurement — declared-FK counts split into definition and external — and
  deliberately assigns no confidence and produces no findings.

### Known cases in 1.60.1.69913

| Record | Evidence | Push |
|---|---|---|
| `Achievement` 9275 — "Warlord Zaela kills (Upper Blackrock Spire)" | `Instance_ID` 1358, a map not in this build. Warlord Zaela is a Warlords of Draenor boss. Removed together with its `Achievement_Category` 15233 | 112039, invalidated |
| `LightParams` 453 | Referenced only by `Light` 16161 on map 3064, which does not exist here. Replaced by 7641 (Kalimdor) in `Light` 269 | 112132, valid |
| 75 `Item` stubs | All `ClassID` 4 / `SubclassID` 0 — 32 trinkets, 23 rings, 20 necks — with no display data, pulled in one push | 112078, invalidated |

**Status at 1.60.1.70009** (client data, measured 2026-09-24):

| Record | 70009 client |
|---|---|
| `Achievement` 9275 + category 15233 | **gone.** Removed in the client, so the hotfix was a stopgap |
| `LightParams` 453 | **Kalimdor use fixed in the client**: `Light` 269 now carries 7641, as the hotfix did. The `LightParams` row survives, still used by the retail-map `Light` 16161 (map 3064) |
| `LightParams` 495 | new case, same pattern: `Light` 253 (Kalimdor) moved 495 → 7831 in the client. 495 is still used by `Light` 15752 on the absent map 3008 |
| 75 `Item` stubs | all 75 still in the client |
| `AreaTable` 16870 "Archimonde's Fall" (map 3049) | removed in the client |
| `Map` 451 "Development Land" + `MapDifficulty` 38 + 335 `world/maps/development` files | removed in the client |

The 75 IDs, so this can be checked exactly rather than by population count
(`scripts/check_findings.py` reads them from here):

```
833, 942, 1315, 1443, 1447, 1980, 2246, 5004, 5005, 5010, 7549,
7550, 7551, 13001, 13002, 13089, 13091, 13096, 14557, 14558, 17063,
17065, 17082, 17108, 17109, 17110, 17982, 19856, 19863, 19871,
19873, 19876, 19885, 19893, 19898, 19905, 19912, 19920, 19923,
19925, 19930, 19947, 22721, 22722, 209681, 209816, 211420, 211449,
211450, 211451, 213347, 213348, 213349, 213350, 215461, 220632,
220633, 220634, 223327, 227967, 227972, 228432, 228464, 228465,
228466, 228467, 228523, 228576, 228589, 228599, 228678, 228722,
236784, 279834, 282019
```

Counting `ClassID` 4 / `SubclassID` 0 orphans instead would measure the wrong
thing: there are **1,128** such rows in 1.60.1.69913, and orphanhood is normal
here. Check these IDs; keep the population figure as context only.

---

## Season of Discovery tags

Forever was cut from a 1.15 client that carried Season of Discovery, and SoD
content is still in every snapshot. `scripts/sod_tags.py` tags it, and
`build_db.py` materialises the result into `wow.db` as `sod_tags` and
`sod_tags_coverage`. The report is `reports/sod_tags_<build>.md`. From Python,
use `enrich.Enricher.sod_tag(id)`.

> **This is deliberately NOT a contamination rule. Do not move it into
> `contamination.scan()`.** The retail filter is diff-scoped: it reads only
> rows added or removed between two builds, emits independent per-rule
> findings, never merges them into a per-row verdict, has no allowlist, and is
> not queryable from `wow.db`. SoD content is not changing between builds. It
> is simply present. So this is a **snapshot-wide annotation**: one merged tier
> per spell, a manual allowlist, and a table you can join. Each design suits
> its question. "Fixing" one to match the other breaks it.

**Tagging, never deletion.** Nothing is removed and no existing query changes
behaviour. A query excludes SoD content only if it joins `sod_tags` (see rule
9 in the wow-query skill). The bias is toward under-tagging. A false "cut" on
a live Forever spell is worse than a missed SoD spell, so ambiguous cases take
the weaker tier.

| Tier | Meaning | Cut view? | 70009 | 70170 |
|---|---|---|--:|--:|
| `sod_rune` | full engraving chain (unless `live_in_forever`) | yes | 829 | 828 |
| `sod_book_candidate` | taught by a learn item with no `ItemSparse` row. Not proof | yes | 20 | 20 |
| `sod_ported` | SoD ability whose same-name trainer sibling is absent from the SoD-era client, so Forever re-added it | no | 23 | 24 |
| `sod_variant` | SoD ID of a spell whose base version is an untagged trainer spell | no | 94 | 94 |
| `sod_flag` | weak signal only | no | 87 | 87 |

### Detection, measured on 1.60.1.70009

1. **Rune chain.** Each hop is gated on its exact Effect/EffectAura value:
   - `SpellName 'Engrave %'`, via `SpellEffect` Effect=54 and `MiscValue_0`,
     leads to `SpellItemEnchantment`.
   - The enchant's `Effect_N`=3 and `EffectArg_N` lead to the wrapper spell.
   - The wrapper's `EffectAura`=332 and `EffectBasePointsF` lead to the
     granted ability.

   Only `EffectArg` is a declared FK. **270 chains from 271 names.** Cutty's
   Rune 401488 has no Effect=54 and gets `sod_flag`. 13 chain IDs have no
   `SpellName` row (`target_missing`), including Crusader Strike 407676 and
   Avenger's Shield 407669. Four of the 13 are wrappers, not abilities.
2. **Book set.** `ItemEffect` TriggerType=6 leads, via `ItemXItemEffect`, to
   an item with no live `ItemSparse` row that teaches a spell with
   SpellClassSet > 0.
   - 343 of 3,314 learn items lack `ItemSparse`, and 321 of those are
     excluded.
   - 312 teach nothing with a class set.
   - 9 are `Item` ClassID 9 (Recipe): vanilla class books teaching ranked base
     spells (Shadow Bolt 1088, Purify 1152). Every SoD book is 0/8.
3. **Propagation.** Via `EffectTriggerSpell` only (declared), one hop at a
   time; the child inherits the parent's tier.
   - A child referenced by an untagged spell through a declared cross-spell
     column, or by `Talent`, is shared with live content. The edge is blocked
     and the child stays untagged. Forbearance 25771 and Dummy Trigger 18350
     are the measured cases.
   - Never propagate through `SpellClassMask` / `EffectSpellClassMask`:
     talents hit base spells too.
   - One effect-typed edge, **`seal_dummy_bp`** (`source_rule =
     'seal_dummy_bp'`, tier inherited). It is how a seal names its Judgement:
     an `EffectAura`=4 effect whose `EffectBasePointsF` is an existing spell
     of the same SpellClassSet. Two more conditions apply:
     - (a) the target is not a trainer spell: it has no AcquireMethod-0
       `SkillLineAbility` row.
     - (b) **if** the target has any `SkillLineAbility` row, one shares a
       SkillLine with the source.

     It reaches Judgement of Martyrdom 407803 from Seal of Martyrdom 407798.
     Measured over all 54 classed aura-4 same-family landings at 70009: 35
     are a seal naming its own Judgement, and 19 are value collisions.

     | Condition | Judgements kept | Collisions through |
     |---|--:|--:|
     | same family only | 35/35 | **19/19** (Blizzard 10 tagged `sod_rune` from Enlightenment 412324) |
     | (a) alone | 35/35 | 0/19 |
     | (a) + (b) strict | 32/35 | 0/19 |
     | **(a) + (b) conditional, in use** | **35/35** | **0/19** |

     Strict (b) loses 21082 → 21183 (Crusader), 1311649 → 1311650 and
     1311656 → 1311655 (Fury). Those Judgements have no `SkillLineAbility`
     row at all, which is why (b) applies only when a row exists.
     `test_sod_tags.py` re-checks both numbers on every run.
4. **Variants.** Two routes:
   - (a) **Aura 332 is an override.** `MiscValue_0` names the spell replaced,
     `EffectBasePointsF` the replacement. So the base of a pair is named, not
     guessed. Exorcist 415076 overrides Exorcism 879…10314 with
     415068…415073. Rune targets that override a same-name trainer spell
     (Fire Blast, Renew, Raptor Strike ranks) are variants, not runes.
   - (b) Same `Name_lang` and SpellClassSet as an untagged trainer spell
     (AcquireMethod 0 plus `SpellLevels`), where the SoD side is tagged by
     steps 1–3 or carries label 3096/3100. When the SoD side was tagged and
     the sibling is **absent from the newest extracted 1.15 build**
     (`config.SOD_REFERENCE_VERSION_PATTERN`; 1.15.9.69722 here), the tier is
     `sod_ported` with `forever_sibling_id`.

   The ported rule is a presence check against a real build, not an ID range.
   It splits 23 ported (Mutilate 399956 ↔ 1241582, Penance, Riptide, …) from
   3 variants whose siblings are classic (Raptor Strike 415335 ↔ 14260).
5. **Labels.** 3071 corroborates engraves (237 of 238 uses). 3096 (430
   spells) and 3100 (214) are mixed and give `sod_flag` on their own, never
   promotion.

**Class is the chain origin's**: the engrave for a rune chain, the taught
spell for a book, the parent for an edge. It is taken from
`SkillLineAbility.ClassMask` first and SpellClassSet second. SpellClassSet is
unreliable on SoD spells. Exorcist 415076 is family 5 (warlock) with
ClassMask 2 (Paladin), and a SpellClassSet-first attribution put 150 runes
under Mage against ~85 for every other class.

**Not signals**, each for a measured reason:
- **Spell ID ranges.** 400k–460k collides with retail DF/TWW IDs, and >1M
  holds Forever-native spells.
- **EffectAura=332 alone.** 510 spells carry it; the rune chain reaches 264
  wrappers.
- **AcquireMethod=3 alone.** Every Judgement of X has it.
- **Raw value scans, "unreferenced", or "no display data".**
- **Aura-4 base points without the `seal_dummy_bp` conditions.** On
  their own they are raw value matches (see the table under step 3).
- **An `ItemSparse` row, as evidence an item is live.** Measured at 70170:
  280 items whose own `ItemEffect` spell is SoD-tagged still have one. Among
  them are "Rune of Shadowstep" 210979, "Spell Notes: Brain Freeze" 208853,
  and the scrambled "Spell Notes: TENGI RONEERA" puzzle items.
- **A `SkillLineAbility` row, as evidence a spell is live.** 438 SLA rows
  point at tagged spells. 270 of them sit on skill line **2851
  Engraving**, which is the SoD rune system itself. AcquireMethod 3 means
  "learned via another spell" (205 untagged rows, such as Tiger's Fury and
  Judgement of Light), not "live".

**Known gap.** Hammer of Wrath 429151 has no label, trigger, override or
inbound declared reference. It is in `sod_manual.json` as `sod_flag`, because
its `BonusCoefficientFromAP` of 0.15 is SoD's; the base ranks have 0.

**Two hand-kept files**, both `{spell_id, reason, source, date}`, applied
after detection:
- `sod_allowlist.json` keeps the detected tier but sets
  `live_in_forever = 1`, which removes the spell from every cut view. It is
  seeded with Enhanced Blessings 435984, folded into baseline blessings in
  Forever.
- `sod_manual.json` adds a `tier` field. Its tags carry `source_rule =
  'manual'` and never override a detected tag; a clash is reported instead.

`live_in_forever` is also set **structurally**, after the allowlist. The new
`live_source` column says which route set it: `allowlist`, `trait_tree` (a
live talent tree grants the spell) or `forever_trainer` (Forever added or
changed its trainer row relative to the SoD reference). Steps 6 and 7,
`trait_tree` and `forever_trainer`, tag nothing. They collect the evidence
that `detect()` applies. See **Player reachability** under *Reporting gap and
liveness*.

**Coverage.** `sod_tags_coverage` records per step whether its source tables
exist and are non-empty in the build (rule 7 of the skill). A step that could
not run reads **NOT SCANNED** in the report and in the table, never zero.
`_build_info.sod_tags` records `ok` or the error. If detection fails,
`build_db.py` logs it loudly and still writes the rest of the database, and a
missing `sod_tags` table is then an error, not "no SoD content".
`scripts/test_sod_tags.py` pins the measured IDs.

### Reporting gap and liveness, measured on 1.60.1.70170 (2026-10-02)

**The tags were not applied to what got reported.** `diff_builds.py` and
`render_patchnotes.py` never join `sod_tags`, and the hand-written 70170
summary queried the `plain_` tables without them. That summary presented SoD
and unreachable spells as class changes. Even with the join, the tags would
not have caught them: **only 4 of the 967 spells changed at 70170 carry any
SoD tag** (one `sod_rune`, Heating Up 400624). Tagging answers "is this
SoD-derived?". The question a patch note needs answered is **"can a player
get this?"**, and today nothing answers it.

**Prototype: player reachability.** A spell is reachable when a chain leads
to it from a live root. Edges are `EffectTriggerSpell`, enchant
`EffectArg` (Effect 1/3/7 via Effect 53/54/92/156), and aura-332 overrides.
The prototype used the live (hotfixed) tables. Roots, tightened step by step
until the SoD leaks were measured:

| Root set | Roots | Reachable | `sod_rune` reachable |
|---|--:|--:|--:|
| every SLA row, every `TraitDefinition`, every item with `ItemSparse` | 10,339 | 11,451 | (most of 828) |
| SLA AcquireMethod 0/1/2 without Engraving 2851; linked trait trees only | 9,832 | 11,047 | 798 |
| as above, minus 280 items whose own effect spell is tagged | 9,545 | 10,200 | **62** |

Of the remaining 62: **29 are granted by live Forever talent trees** (below),
and the rest are a second hop of SoD items (scrambled Spell Notes whose
effect spell is untagged but triggers a rune) plus about 13 SLA rows. The
second-hop item exclusion is not implemented yet.

At the final stage, **516 of the 967 changed spells are unreachable** (111 of
the 261 with mechanical changes in `SpellEffect` / `SpellMisc`). Unreachable
does **not** mean SoD. NPC spells live server-side and are never reachable
from client data (Holy Forgefire 1322218 is reachable only through Verigan's
Fist 6953). It means "not verifiable as player-facing". Patch notes should
put these spells in a separate section, not drop them.

**Forever talent trees are live, and they grant tagged runes.** Class trait
trees are linked to class skill lines through `SkillLineXTraitTree` (ID,
SkillLineID, TraitTreeID, Variant). Nine are linked: 1082 Shaman (373), 1089
Druid (574), 1091 Hunter (50), 1100 Paladin (184), 1111 Rogue (38), 1112 Mage
(237), 1114 Priest (613), 1116 Warlock (354) and 1117 Warrior (26). They carry
currency 3820 and costs 4044/4114. Trees 1081 (Shaman) and 1083 (Druid) are
unlinked, and are probably drafts. `Blizzard_LegacySystem`'s
`LegacyTreeData` covers a different set: professions, adventure and
progression trees. Live push 112347 edits these trees, so read them from the
hotfixed tables.

**29 `sod_rune` spells and 1 `sod_flag` are talent-tree nodes on linked
trees.** They are live in Forever, so their "likely cut" tier is a false
positive:
- **Druid:** Berserk, Eclipse, Natural Reaction
- **Hunter:** Lone Wolf, Rapid Killing, Resourcefulness 440529 and 1242688,
  and Survivalist's Discipline (the `sod_flag`)
- **Mage:** Fingers of Frost, Heating Up, Missile Barrage
- **Paladin:** Infusion of Light, Purifying Power
- **Priest:** Divine Aegis, Renewed Hope, Soul Warding
- **Rogue:** Cutthroat
- **Shaman:** Lightning Overload, Maelstrom Weapon, Mental Dexterity, Water
  Shield
- **Warlock:** Decimation, Demonic Knowledge, Demonic Pact, Improved Drains,
  Pandemic, Shadow and Flame
- **Warrior:** Focused Rage 29787. A TBC-era ID, so the rune tag is suspect
  in itself. Check its chain with `--spell`.

This is the opposite error to under-tagging. The design rule says it is the
worse one.

**Forever-ID clones of SoD spells are invisible to the tagger.** Rule 4b
pairs a SoD spell only with an untagged *trainer* sibling. A new Forever-ID
spell that shares a name with a tagged SoD spell, and is not on any skill
line or tree, falls through:
- Starfall 1300361 shares its name with the SoD Druid rune Starfall
  (439748/439755/439768). Its description is `$@spelldesc1300354`, and spell
  1300354 **does not exist in the client**.
- Renew 1289450 shares its name with the SoD Priest variant Renew
  (425268-425271).
- Soul Harvest 1242853 shares its name with the SoD Warlock book 437032.

All three are unreachable, and all three were reported as class changes.
Separately, Coward! 422978 and Cryoblast 440212 are SoD-era IDs present in
1.15.9 and untagged. Cryoblast is reachable only through Scroll of Cryoblast
217495, which also has a SoD-era ID, and its origin cannot be checked (next
point).

**The 1.15 reference is too thin for item checks.** `out/1.15.9.69722/`
extracted 15 tables, and `wow.db` there loads only `SpellName`. There is no
`ItemSparse` or `ItemEffect`, so "was this item in the SoD client?" cannot be
asked.

**Changes.** Items 1, 2 and 5 were built on 2026-10-02 (see **Player
reachability** below), plus a rule found while verifying them
(`forever_trainer`). Items 3, 4 and 6 are still open.
1. **Done.** `spell_reach` in `wow.db`, from `scripts/spell_reach.py`.
2. **Done.** `sod_tags.py` sets `live_in_forever = 1` structurally for a
   tagged spell on a live talent tree, with `live_source = 'trait_tree'`
   (a new column). The tier and `source_rule` are kept.
3. Add a weak `sod_clone` tier: an untagged spell that shares its
   `Name_lang` with a tagged rune, book or variant, is unreachable, and
   (optionally) has a description that references a missing spell. Do not
   use the `>1M` ID range as the signal (see **Not signals**).
4. Tag SoD items, not just spells: items whose `ItemEffect` spell, or
   anything that spell triggers, is tagged. Materialise them as
   `sod_item_tags`.
5. **Done.** `diff_builds.py` and `render_patchnotes.py` partition spell
   changes into **player-facing**, **Season of Discovery (cut tiers)** and
   **unreachable / server-side**, ahead of the per-table detail.
6. Re-extract 1.15.9.69722 with `ItemSparse`, `ItemEffect`,
   `ItemXItemEffect` and `SpellEffect`.

The numbers above came from a scratchpad prototype. The committed module
differs slightly: its counts are in the next section.

### Player reachability (`spell_reach`), built 2026-10-02

`scripts/spell_reach.py` answers "can a player get this spell?". `build_db.py`
runs it after `sod_tags` and materialises **`spell_reach`**: one row per
`SpellName` ID with `reachable`, `root_kind`, `root_id`, `root_spell`,
`via_spell`, `via_edge` and `depth`. An absent row means "not a spell in this
build", never "not examined". **`spell_reach_coverage`** records which root
sources could be read, and `_build_info.spell_reach` records `ok` or the
error.

- **Roots:**
  - `skill_line`: AcquireMethod 0/1/2, excluding 2851 Engraving.
  - `class_passive` (added 2026-10-08): AcquireMethod 3, a single-class
    `ClassMask`, on a skill line that is not a class line (`CategoryID` <> 7).
    `root_id` is the skill line. It matches 1 spell at 70291, Rule of Rage
    1322574 on 95 Defense, and 0 at 70170. AM 3 with a single-class mask on
    **any** line matches 262 at 70291: effect copies plus Tiger's Fury,
    which was removed from the game. Do not widen it without measuring.
  - `talent_tree`: `TraitDefinition` SpellID/VisibleSpellID on a live tree.
  - `talent`: `Talent.SpellRank_0..8`. `SpellID` is 0 on all 432 rows.
  - `item`: an `ItemSparse` item that is not a SoD item.
- **Edges:** trigger, enchant and aura-332 override.
- **Live trees** (`spell_reach.live_trait_trees`, shared with `sod_tags`): a
  tree is live if `SkillLineXTraitTree` links it, or if its TraitSystem is one
  no linked tree uses. At 70170 that is the 9 class trees plus the Legacy
  trees 1187/1188/1189, which `LegacyConsts` in the client's API
  documentation names. Tree 1118 also counts: it is on the Legacy system 45
  but has no nodes. Trees 1058, 1066, 1081 and 1083 do not count.
- **SoD items** are excluded as roots when their effect spell, or anything it
  reaches, is tagged `sod_rune`/`sod_book_candidate`/`sod_flag` and not live.
  322 at 70170, including the second-hop scrambled Spell Notes.
- **Measured at 70170:** 9,070 of 31,744 spells reachable from 9,539 roots.
- **`spell_reach.load(build)`** computes reachability on the fly, read-only,
  for a database built before the table existed. That is how 70058 is read
  while `mcp_server.py` holds its `wow.db` open (WinError 5; a stale
  `wow.db.tmp` is left beside it). It also applies the talent-tree rescue to
  that database's older `sod_tags`. It does **not** apply `forever_trainer`
  there, so a removed spell in such a build can still show as SoD.
- **`scripts/test_spell_reach.py`** pins the measured IDs.

**`forever_trainer`**, a `sod_tags` step found while verifying the above:
- **The finding.** The regenerated 70170 notes put Fire Nova 408341–408345
  under SoD. Forever deleted Fire Nova Totem (1535/11315 are gone from
  `SpellName`) and made the SoD rune Fire Nova the Shaman trainer spell:
  AcquireMethod 0 on 375 Elemental Combat, Ranks 1–5 at levels 12–52. In
  1.15.9 the same IDs were AcquireMethod 3 on 373 Enhancement.
- **The rule.** A tagged spell with a trainer row (AcquireMethod 0,
  non-Engraving, with `SpellLevels`) that the SoD reference's
  `SkillLineAbility` lacks is marked `live_in_forever` with
  `live_source = 'forever_trainer'`.
- **Not evidence:** a row identical to SoD's. Aspect of the Viper 415423,
  Shadowfiend 401977, Redirect 438040, Totemic Projection 437009 and Heart of
  the Lion 409580 carry the same AcquireMethod-0 row in 1.15.9, so they stay
  cut.
- **Result at 70170:** 12 spells qualify. 7 are runes (Fire Nova ×5, Victory
  Rush 402927, Hammer of the Righteous 407632). The other 5 are already
  `sod_ported`, Mutilate 399956 among them, so the two rules agree
  independently.
- `load_reference_sla()` reads the reference's `SkillLineAbility.csv`.
  Without it the step reads **NOT SCANNED**.

**Totals at 70170:** `trait_tree` marks 38 tagged spells live (27
`sod_rune`, 8 `sod_ported`, 2 `sod_book_candidate`, 1 `sod_flag`), and
`forever_trainer` marks 12 more.

**The regenerated 70058 → 70170 notes:** 1,018 spells touched by `Spell*`
tables. 494 are player-facing, 0 are SoD, and 524 are unreachable. Every spell
the first summary misreported lands in the unreachable group: Starfall
1300361, Renew 1289450, Soul Harvest 1242853, Coward! 422978, Totemic Recall
1323420 and others.

**Still wrong in the same direction:**
- `ItemSparse` carries test and GM items, so their spells read as
  player-facing. Area Death (TEST) 265 is reachable via item 5417.
- Cryoblast 440212 is player-facing only through Scroll of Cryoblast 217495,
  which has a SoD-era ID. Item 6 above is what would settle it.

---

## Findings to verify

Dated predictions from 1.60.1.69913, recorded **2026-09-20** (finding 8 added
**2026-09-21**) so the next build can confirm or kill them. Each entry states what was observed, what would
confirm it, and what would falsify it. **Resolve these before adding new ones**
— an unresolved prediction is worth more than a new guess.

> **Checked against 1.60.1.70009 on 2026-09-24**, from the client data only:
> the client had not been launched on 70009, so there was no hotfix overlay.
> Resolved: #2 (shipped) and #6 (first real test). Partly resolved: #5 in
> the client. Unchanged: #4 and #7.
>
> The 70009 overlay was measured later the same day. Results: #1 is gone
> from live data, #3 is still staged (481 live-only), #5 regressed live, and
> #8 gained supporting evidence.
>
> **`check_findings.py` used to misread a build with no overlay** (fixed
> 2026-09-24). With no overlay, the live data *is* the client data, and it
> reported #3 as RESOLVED while the client held the same 5 items. Separately,
> #2 could never resolve: it compared the count of "refresh the world"
> strings (1) against the number of renamed IDs (3). It now reads the
> overlay's presence from the manifest, reports **UNMEASURED** for live-side
> checks when there is none, and checks #2 per ID against the client text.
>
> The parser reads these entries by regex. The first backticked `Field_…`
> name in this section must stay finding 4's own value. The first
> `| ID | TimeEventID | Timestamp |` rows must stay finding 1's. Put new IDs
> *after* them.
>
> **Cleaned up 2026-10-08.** #2 and the fixed parts of #5 moved to **Closed
> findings** below, and `check_findings.py` no longer runs them. #5 is now
> only the 75 Item stubs. Its check used to return RESOLVED when any one of
> three cases cleared, so it read "Resolved" for a month while every stub
> stayed in the client.

### 1. A weekly event schedule starting 12 October 2026

`TimeEventData` is **hotfix-only** (ships empty, push 112079) and holds exactly
three rows:

| ID | TimeEventID | Timestamp | UTC |
|---|---|---|---|
| 25930 | 3162 | 1791824400 | 2026-10-12 17:00 |
| 25943 | 3163 | 1792429200 | 2026-10-19 17:00 |
| 25956 | 3164 | 1793034000 | 2026-10-26 17:00 |

Exactly 7 days apart, sequential event IDs, same region group (5). 17:00 UTC is
Blizzard's usual test-window slot.

**Watch:** whether the dates shift, whether a fourth row appears (extending
the cadence), and whether the table ships populated in the client rather than
arriving by hotfix. A shifted date means the schedule slipped; a fourth row
means the cadence is ongoing rather than a three-week run.

**Status at 70291 (2026-10-08): not live on any build since 69977.**
`check_findings.py` reports FALSIFIED: the table is empty in the client and
in the live overlay.

| Build | Client | Live overlay | Overlay captured |
|---|---|---|---|
| 70009 | empty | empty; push 112079 not among its pushes | mid-session, lower bound |
| 70170 | empty | empty | client closed |
| 70245 | empty | empty | client closed |
| 70291 | empty | empty | client closed |

Read FALSIFIED as "not live on this build", not "cancelled". Hotfixes do not
carry across builds (see **Key facts**), so the rows could be re-pushed.
**Decide after 2026-10-12 17:00 UTC.** If the first recorded date passes
with the table still empty, close this as falsified. If rows reappear, check
whether the timestamps match.

### 3. The vanilla PvP rank ladder arrived by bulk injection

1,385 modern-ID `ItemSparse` additions, of which **481 carry vanilla PvP rank
titles** across both factions (Blood Guard 49, Knight-Lieutenant 47, General 43,
Warlord 42, Marshal 40, Field Marshal 39, Grand Marshal 26, High Warlord 26,
Stone Guard 20, Sergeant Major 20, …). 689 of the modern-ID additions require
level 60. All arrived with **synthetic push IDs**, i.e. one bulk load rather
than incremental authoring.

**Watch:** whether these ship in the client next build instead of arriving by
hotfix. Shipping in the client means the feature is settled; arriving by hotfix
again means it is still being staged. Also watch whether the ladder grows —
the vanilla honor system has 14 ranks per faction, so an incomplete set now
implies more to come.

**70009: not shipped, still staged.** The client still holds **5**
modern-ID rank-titled items, the same as 69977. With the overlay measured,
**481** are live-only, exactly the recorded count. The bulk injection was
carried over to the new build unchanged: 4,209 `ItemSparse` rows are
identical to 69977's live values. (Before the overlay was loaded,
`check_findings.py` called this RESOLVED. That verdict was the no-overlay
artifact, since fixed.)

### 4. LightData column 055 — LEANING FALSIFIED, do not assert

Twelve `LightData` rows (74945–74956, `LightParamID` 7742, Kalimdor) covering
the complete day cycle each changed `Field_1_60_1_69876_055` from `0` to
`13533183`, under push 112132.

`13533183` = `0xCE7FFF` = bytes (206, 127, 255), which is *consistent with* a
packed RGB value — a light lavender. **This is a guess and must not be stated
as fact.** The column is unnamed in WoWDBDefs for this build, and per the
conventions above an unknown column's meaning is never asserted in committed
output.

**Evidence against it, added 2026-09-20.** WoWDBDefs' `meta/mapping.dbdm`
marks 23 `LightData` columns as `COLOR`, and `Field_1_60_1_69876_055` is not
among them. On its own that would be weak — the meta tree might simply not
cover unnamed columns. It does: four of the 44 `COLOR` mappings are on
**unnamed columns in this very build**, under the same generated naming
scheme —

```
COLOR LightDataGlobalVolumeFog::Field_1_60_1_69876_001
COLOR LightDataGlobalVolumeFog::Field_1_60_1_69876_002
COLOR LightDataGlobalVolumeFog::Field_1_60_1_69876_003
COLOR LightDataGlobalVolumeFog::Field_1_60_1_69876_004
```

So contributors have gone through Forever's unnamed light columns and tagged
the ones they read as colours, and did not tag this one. That is a negative
signal, not merely absence of evidence — hence **leaning falsified**. It is
not conclusive: the four tagged columns are in a different table, and nobody
may have worked through `LightData`'s unnamed columns at all.

**The gate is unchanged.** The reading becomes reportable only if WoWDBDefs
names the column as a colour in a later definition sync — the meta mapping
does not make it reportable now, and the absence does not make it refutable in
committed output either. If named as something other than a colour, discard
the reading entirely.

**Watch:** whether a definition sync names `LightData::Field_1_60_1_69876_055`,
and whether `mapping.dbdm` gains a `COLOR` entry for it. Re-check both after
every `sync_refs.py` run.

**70009:** WoWDBDefs merged the build (`cf84e01`). The column is still
unnamed, and `mapping.dbdm` still has no entry for it. Unchanged.

### 5. 75 retail Item stubs still in the shipped client

75 `Item` stubs (ClassID 4 / SubclassID 0, no `ItemSparse` row) are retail
contamination: the IDs are listed under **Retail contamination**. They were
removed by hotfix on 69913 and 69977 (push 112078 removed 71 of them live), and
**every one of them has stayed in the shipped client** through 70291.

**Live, the removal lapsed.** In the 70009 overlay push 112078's removal does
not apply, and all 75 are present live as well as in the client. The fixes
that mattered moved into the client (see #5 under **Closed findings**). The
one that stayed a hotfix lapsed with the build. That is the per-build overlay
rule under **Key facts** at work.

**Status at 70291 (2026-10-08): UNRESOLVED.** All 75 recorded IDs are still
in the client.

**Watch:** whether any are gone from the shipped `Item` DB2. A partial prune
is reported with the IDs that went. `scripts/contamination.py` still reports
new contamination automatically; a *new* HIGH-confidence finding is the thing
to look at.

### 6. The encrypted-count detector — TESTED at 70009, and it moved

`inventory.py` compares the encrypted-file count against a hardcoded baseline
of **5035** and prints `MATCH` or `DIFFERS`. It has now passed on three builds:

| Build | files | encrypted | UnknownKey | ButNot | files.csv SHA-256 |
|---|---|---|---|---|---|
| 69876 | 1,441,771 | 5,035 | 3,371 | 1,664 | `86733ec34aee…` |
| 69893 | 1,441,771 | 5,035 | 3,371 | 1,664 | `86733ec34aee…` |
| 69913 | 1,441,771 | 5,035 | 3,371 | 1,664 | `86733ec34aee…` |

**All three file sets are byte-identical**, so the detector has never been
shown data that could make it fail. Three passes is one observation repeated
three times. CLAUDE.md calls a drop in this count "one of the highest-value
early signals" — on the evidence so far it is an **untested detector reporting
MATCH**, which is precisely the category the identical-hash check in
`inventory.py` was in until it was pointed at real data and turned out to fail
on every correct run.

A `MATCH` is only a measurement if the file set moved and the encrypted count
did not. If neither moved, `MATCH` carries no information about encryption at
all — it is restating that the build did not change.

**The first real test is 1.60.2.** What to record when it lands:

- whether `files.csv` differs from 69913's at all. If it does not, the
  encrypted result is still untested and must be reported as such rather than
  as a pass.
- whether the encrypted total moved, and **which way**. A drop means keys
  leaked or content unlocked; a rise means new encrypted content shipped.
- the `EncryptedUnknownKey` / `EncryptedButNot` split separately from the
  total. The two can move in opposite directions and cancel — 3,371 / 1,664
  summing to 5,035 could become 3,300 / 1,735 with the total unchanged, and a
  total-only check reports `MATCH` through a real key release.

**Implemented 2026-09-20.** `inventory.py` now:

- takes the baseline from the **previous extracted build's manifest**
  (`previous_build()` / `baseline_from()`), not a constant. The comparison is
  now "did this move since last time" rather than "does this still equal a
  number someone typed in September".
- reports the **file-set delta alongside** the encrypted count, hashing the
  sorted FDID set rather than the whole file — a rename or a retype changes
  the file without changing the set, and the set is what the question needs.
- compares the `EncryptedUnknownKey` / `EncryptedButNot` split against the
  previous build per status, so the two cannot move in opposite directions and
  cancel unnoticed.
- prints, when the file set did not move:

  > NOTE: the encrypted-file count above is NOT a measurement this run.
  > The file set did not move, so the count could not have moved either.

  and records `encrypted_result_is_measurement` in the manifest so a report
  can render the distinction without re-deriving it.

At 1.60.1.69913 this correctly reads: `encrypted 5,035 (vs 5,035 in
1.60.1.69893: MATCH)`, `file set unchanged`, followed by the NOTE. **The
detector is still untested** — that does not change until a build arrives whose
file set actually moves. 1.60.2 remains the first real test.

**Resolved 2026-09-24: 1.60.1.70009 was the first real test.** It came as a
1.60.1 build, not 1.60.2. The file set moved (1,441,771 → 1,441,610; +240
added, −401 removed), so the count was a real measurement:

| Status | 69977 | 70009 | Delta |
|---|--:|--:|--:|
| `EncryptedUnknownKey` | 3,371 | 3,385 | **+14** |
| `EncryptedButNot` | 1,664 | 1,663 | −1 |
| Total | 5,035 | 5,048 | +13 |

The +14 accounts exactly:
- **+7**: new unnamed files, FDIDs 8473060 and 8473797–8473802.
- **+6**: DB2s that gained unknown-key sections: `Mount`, `MountXDisplay`,
  `Vehicle`, `VehicleSeat`, `GlobalStrings` and
  `CreatureDisplayInfoGeosetData`. These are hidden rows.
- **+3 / −2**: files that moved between the two statuses.

A new `TactKeyLookup` row, 8347, also shipped. It moved **up**, so new
locked content shipped and no keys leaked. The per-status split was needed
here: the two statuses moved in opposite directions.

**The detector nearly failed silently on its first real test.**
`extract_db2.py` rewrote `manifest.json` wholesale and erased the inventory
section. Every hotfix-day re-extract did this, so 69913 and 69977 had none.
`inventory.py` then printed "no previous build to compare against" for the
encrypted count, while comparing the file set against 69977 on the next
line. It was fixed on 2026-09-24:
- `write_manifest()` now carries forward any key it does not own.
- `baseline_from()` recomputes the baseline from the previous build's
  `files.csv` when its manifest has no inventory section.

Re-run, 70009 reads `encrypted 5,048 (vs 5,035 in 1.60.1.69977: DIFFERS)`.
Close this finding out; the encryption table under **Baseline metrics**
carries it forward.

### 7. A parallel Thunder Clap rank ladder at modern IDs

`SpellName` holds 15 rows named "Thunder Clap". Two of them are complete R1–R6
ladders carrying the same `NameSubtext_lang` rank strings:

| Rank | Classic | Modern |
|---|---|---|
| 1 | 6343 | 461830 |
| 2 | 8198 | 461829 |
| 3 | 8204 | 461828 |
| 4 | 8205 | 461827 |
| 5 | 11580 | 461826 |
| 6 | 11581 | 461810 |

(Plus 11582, 13532 and 413589, which carry no rank or no description.)

**The two ladders are identical where a copy would be, and diverge where it
counts.** Measured on 1.60.1.69913.

The modern ladder has an identical supporting-row profile across eleven
tables — 1 row each in `SpellName`, `Spell`, `SpellMisc`, `SpellLevels`,
`SpellPower`, `SpellCategories`, `SpellClassOptions`, `SpellCooldowns`,
`SpellTargetRestrictions`, `SpellShapeshift` and 2 in `SpellEffect` — with
0 dangling foreign keys, matching the classic ranks exactly. These are **not**
orphan stubs, the opposite of the 75 `Item` cases.

> An earlier revision of this entry called that "structurally indistinguishable
> from the classic ladder". **That claim was too strong.** All eleven tables
> are the spell's *own definition* tables, where a copied spell looks identical
> by construction — the profile was never capable of telling the two apart.
> The divergence is in **inbound** references, which had not been checked.

#### The discriminator: inbound references

`SkillLineAbility` is **populated** — 7,824 rows, `resolution: ok`. This is a
real test, not a null result.

| | Classic | Modern |
|---|---|---|
| `SkillLineAbility` rows | **6** (SLA IDs 6073–6078, sequential) | **0** |
| `SkillLine` | 26 = **Arms** | — |
| `ClassMask` | 1 (Warrior) | — |
| `SupercedesSpell` chain | complete: 6343 → 8198 → 8204 → 8205 → 11580 → 11581 | — |
| `SpellLearnSpell` rows | 0 | 0 |
| **Inbound FK references, each rank** | **15–17** | **exactly 10** |

All ten of the modern ladder's references are from the spell's own definition
tables. **Eight reference classes the classic ladder has and the modern one
entirely lacks:**

```
SkillLineAbility::Spell            CooldownSetSpell::SpellID
SkillLineAbility::SupercedesSpell  CooldownSetLinkedSpell::SpellID
SpellLabel::SpellID                PlayerCondition::SpellID
ItemEffect::SpellID                SpellEffect::EffectTriggerSpell
```

Zero go the other way — the modern ladder has no reference class the classic
one lacks.

**So the 461xxx ladder is defined but unconsumed:** unreachable from any skill
line, unlabelled, not in a cooldown set, not gated by a player condition, not
taught by anything, and triggered by nothing.

> **Being unreferenced is not itself the signal.** Measured: 63.5% of all
> spells in this build have no external reference, and the rate is identical
> for classic-era and modern IDs. An unreferenced spell is unremarkable. What
> is informative here is the **pairing** — two ladders with the same name and
> the same six rank subtexts, one fully wired and one not. See the rejected
> `unreferenced_entity` rule under **Retail contamination** for the base rates
> that rule this out as a general test.

`SpecializationSpells` is 204-empty, as expected for Classic, so it
contributes nothing either way.

#### Where the evidence came from

The FK walk, resolving the **157 columns declared as FKs to a spell** via
`/dbc/relations` — not from `contamination.py`. Submitting all 12 tables and
150 rows to `contamination.scan` returned `not_scanned`: **0 of 3 rules could
read any of them**. `SkillLineAbility` and `SpellLearnSpell` carry no
map-reference column either, and the other two rules stay pinned to `Light`
and `Item`. Per the convention above, that zero is not a clean result and is
not evidence in either direction.

#### Verdict: watch

Still **watch, do not conclude**. An unreferenced definition is consistent with
*both* staged-but-unfinished Forever content and retail leftovers, and nothing
here separates those two. ID range alone remains explicitly not a trigger per
**Conventions**; an earlier note calling these contamination on that basis is
withdrawn.

**Watch:**

- whether `SkillLineAbility` rows appear for them in 1.60.2 — that would make
  them intended content being wired up
- whether they are pruned instead, the way the `Achievement` and `Item`
  contamination was
- whether any hotfix touches them; nothing does at 1.60.1.69913

**70009:** unchanged. The same 8 `SpellName` rows are in 461800–461835, and 0
`SkillLineAbility` rows point at 461810–461830. The ladder was not wired up,
and not pruned either.

### 8. Six `BroadcastText` rows arrived with no hotfix record — UNEXPLAINED

The 2026-09-21 hotfix wave (no client patch; same `buildConfig`) added **11**
rows to `BroadcastText`. `/dbc/hotfixes/list` accounts for **5** of them —
304793–304797, push 112203, the Lorthuna/Belathaan conversation.

The other six have **no entry in the hotfix list at all** — not under
`BroadcastText`, not at any push ID:

| ID | Text (truncated) |
|---|---|
| 2660 | "Naralex sleeps again!…" |
| 8111 | "My wind riders are trained to fly quickly through the hot Ba…" |
| 8112 | "The Barrens, with its hot sun and hostile denizens…" |
| 8122 | "You haven't lived until you've looked down on the world from…" |
| 10031 | "Many are the paths of the Earth Mother…" |
| 10032 | "Treat the wind rider well as it takes you to your destinatio…" |

**They are not shipped client data surfacing late.** Checked individually
against all three exports: absent from `db2/BroadcastText.csv` (plain), absent
from the 09-19 overlay snapshot, present in the 09-21 overlay. So the overlay
genuinely gained them in this window.

Coincidence worth ruling out before theorising: 8111, 8112 and 8122 also exist
as **`Item`** hotfix records detected 09-19. Same integers, different table —
`Item` 8111 tells you nothing about `BroadcastText` 8111.

**Do not assert a mechanism.** Plausible-but-unverified readings include a push
whose table attribution WTL resolved elsewhere, or overlay content reaching the
client by a path that does not register a `DBCache` record. Both are guesses.
Nothing was measured that distinguishes them, and `tableIsKnown` is 1 for every
record in the window, which argues against a simple unknown-table-hash story.

**Watch:**

- whether the next wave shows the same gap, and in which tables
- whether these six ever acquire a hotfix record retroactively
- whether the count of "changed rows with no hotfix record" on the wave page
  stays at 6 or grows — `render_patchnotes.py --since` reports it per wave

**70009: supporting evidence, not proof.** After one play session on
70009, the overlay gained **13** `BroadcastText` rows with **no hotfix
record**. They are ordinary NPC lines:
- 2545, "It is not yet your time…", a spirit healer.
- 4857, "What are you looking for?".
- 8116 and 8122, the Barrens wind-rider master. 8122 is one of this
  finding's six.
- Unnamed rows 7214–7265.

The Lorthuna/Belathaan rows from push 112203 (304793–304797) and 327642 are
absent from the 70009 overlay. That fits the 69977 report's hypothesis: the
client caches `BroadcastText` on demand as it meets NPCs, and no hotfix
record is created. The test is still the one proposed there. Talk to a
specific NPC whose row is absent, log out, re-extract, and check that the
row appears with no push ID.

### 9. Four Forever dungeons named, with their `Map` rows withheld (added 2026-09-24)

1.60.1.70009 added boss-kill statistics (`Achievement` category 14821) for
instances that have **no `Map` row and no `AreaTable` rows** in the client:

| Map | Instance | Statistics | Other data in the client |
|--:|---|---|---|
| 3001 | Shaper's Terrace | 63574, 63590, 63592, 63593 (Nanaya, Cinder, Bolt, Snowtalon) | `MapDifficulty` 6015, 6345 |
| 2994 | Alcaz Prison | 63575 (Blazeroar) | `MapDifficulty` 6008, 6343 |
| 2981 | Hyjal Summit | 63580 (The Wild King) | none |
| 3052 | Barrow Deeps | 63581 (Sonya Darkhallow) | none |

These map definitions are being **withheld**, not forgotten: two of the four
already have difficulty rows. In the same build, six DB2s gained sections
encrypted under an unknown key, and a new `TactKeyLookup` row shipped (see #6).
Reading, **not measured**: the `Map` rows may be among the encrypted
content. Nothing ties a specific encrypted section to these maps.

**Watch:**

- whether `Map` rows for 2981, 2994, 3001 and 3052 appear, whether in the
  client, by hotfix, or through a key release that lowers
  `EncryptedUnknownKey`
- whether the `MapDifficulty` footprint grows to 2981 and 3052
- whether `contamination.py` still labels them `withheld_map_ref` /
  `dangling_map_ref`. When the `Map` rows arrive, those hits should
  disappear. If they do not, the rule has another hole.

---


## Closed findings

Resolved entries moved out of **Findings to verify** so `check_findings.py`
stops re-checking them. Kept for the evidence. Numbers are the original ones.

### 2. A shard/world mechanic being repositioned — CLOSED 2026-10-08 (shipped at 70009)

Three `GlobalStrings` rows changed under one push (112128), all the same rename:

```
60077  "Transfer Now"                                  -> "Refresh Now"
60078  "Your character will be transferred to another  -> "The world around you will
        shard in %s %s."                                   refresh in %s %s. ..."
60175  "Transfer to a new shard now."                  -> "Refresh the world now."
```

Nothing else in `GlobalStrings` changed. This is user-facing wording for a live
mechanic being settled *after* the build shipped, which suggests the system
itself is still being positioned.

**Watch:** supporting UI strings using "refresh" language, new tables or columns
for the mechanic, and whether "shard" wording survives anywhere. If the rename
is cosmetic, expect nothing further; if the mechanic is being reworked, expect
more strings and possibly a new table.

**Resolved 2026-09-24 against 1.60.1.70009.** All three rows now carry the
"refresh" wording in the **client** DB2. 69977's client still had "Transfer
Now" / "…transferred to another shard…". The hotfix was the rename landing
early, not a trial. The **cosmetic** reading won: no further "refresh"
strings arrived, and 26 strings with "shard" in them survive in both builds.
The same build did add ruleset wording that belongs to the same
server-partitioning family. `SUPER_DISTRICT_TITLE` went from "Choose Your
Gameplay Style" to "Choose Your Gameplay Ruleset", and
`SUPER_DISTRICT_DESCRIPTION` now reads "You will only be able to interact
with players who choose the same ruleset." That is a separate system, not
evidence for this finding. Close it out at the next cleanup.

### 5 (part). Achievement 9275 and LightParams 453 — CLOSED 2026-10-08 (fixed at 70009)

Two of the three retail-contamination cases recorded on 69913 were removed by
hotfix and stayed in the client until 1.60.1.70009 fixed them there:

| Record | 69913 | 70009 client |
|---|---|---|
| Achievement 9275 (Warlord Zaela, WoD) + category 15233 | removed by hotfix, still in client | **gone. The hotfix was a stopgap** |
| LightParams 453 (map 3064) | replaced by hotfix, still in client | **Kalimdor fix shipped**: Light 269 carries 7641, as the hotfix did. The row survives, used only by the retail-map Light 16161 |

The same build also pulled AreaTable 16870 (map 3049, Development Land) and a
second light case (495, see **Known cases**). The contamination rule itself
changed for 70009; see **Retail contamination**. The third case, the Item
stubs, is still open as finding #5.

## Key facts

- **FDIDs share the retail namespace.** `wowdev/wow-listfile` applies directly.
  No Forever-specific listfile exists.
- **DB2 layouts are Classic-flavor, not retail.** WoWDBDefs resolves via
  layouthash, but Forever is new — expect unknown columns in new tables. Name
  them `unk_<offset>`; never guess a meaning in committed output.
- **Typing is the bigger gap, but naming is not zero.** This build contains
  **1,441,771** files. **19,583 of them (1.4%) have an empty name** despite
  being members of `Listfile.NameMap` — of those, 11,842 remain `unk` and
  7,721 were classified as `blp` by the magic-byte pass. An earlier revision
  of this file claimed zero unnamed files; that was wrong, and the mistake is
  recorded as a gotcha below because the API makes it easy to repeat.
  (2,274,258 is the *listfile's* total size across the whole retail FDID
  namespace, not a count for this build. Do not conflate the two — though
  note **2,274,258 − 2,254,675 = 19,583**, exactly the empty-name count, which
  is what the discrepancy between the listfile release's row count and the
  named-file figure was all along.)
  On typing: **99,348 files had no `content_type`**, and a magic-byte pass
  recovered **87,506** of them — 79,784 as `wmo_or_adt` — leaving 11,842
  still unknown. **Magic-byte classification is valuable and worth
  maintaining.** Filename recovery is the lower-value half, but it is not a
  solved problem either.
- **Hotfixes are a separate diffable source.** WTL loads live hotfix data
  from the client's `Cache/ADB/enUS` directory and tracks push IDs. This is
  distinct from the static DB2s shipped in the build: hotfixes are Blizzard
  tuning live data between client patches. Diffing them answers a different
  question than diffing DB2s, and the two must not be conflated.
- **Hotfixes are per build. A new build starts with an almost empty
  overlay.** Measured at 70009 against 69977's last overlay:
  - Only **2** real pushes applied: 112078, and 112238, a new lighting push
    that adds a `ZoneLight` "Stormwind Harbor".
  - The bulk item injection carried over unchanged.
  - Every other 69977 hotfix went one of two ways:
    - **Promoted into the client.** The Zaela achievement and category
      removal, the Transfer → Refresh strings, Faction 2758, `Light` 269,
      6 `ConversationLine`s, 16 `LightData` rows and 5 `ItemSparse` rows
      now ship in the client.
    - **Lapsed.** The Apply Poultice rework (112230), `PetPersonality` 1
      (112226), the `TimeEventData` schedule (112079), and the item-stub
      removal (112078 on `Item`). Apply Poultice is back to an instant
      `KILL_CREDIT` cast on 70009.

  So a hotfix vanishing on a new build is **not** a revert decision until a
  later wave on that build confirms it; it may simply not have been re-pushed
  yet. And a client change on a new build may already have been live for
  days. Compare the new client against the **previous overlay** as well as
  the previous client.
- **Encryption.** Blizzard withholds Salsa20 keys for unreleased content.
  Encrypted files fail to decode until keys land in `TACTKeys`. Always
  skip-and-log, never error the run.
- **GameTables are a separate data source and the DB2 pipeline cannot see
  them.** 42 tab-separated `.txt` files under `GameTables/` in CASC, holding
  the per-level curves the client interpolates at runtime — `xp.txt`,
  `NpcTotalHp*.txt`, `CombatRatings.txt`, `SpellScaling.txt` and so on.

  They have **no DBD definition**, so `/listfile/db2s` — which enumerates
  definitions — can never list one, and `extract_db2.py` can never reach them.
  WTL has a `GameTableProvider` internally but **exposes no HTTP route** for
  it (checked `Controllers/`; there is none). They are ordinary CASC files, so
  `/casc/fdid` serves them verbatim, which is what `extract_gametables.py`
  uses. Discovery is from `files.csv`, so `inventory.py` must run first.

  Output is `out/<build>/gametables/*.txt`, raw TSV byte-for-byte. No
  conversion to CSV: the header row's exact spelling is how the client names
  the columns, and diffing them as text is the point. 284 KB, ~2s per build,
  identical across 69876/69893/69913.

  **`SpellScaling.txt` is where class spell scaling would live.** The DB2
  named `SpellScaling` ships **204-empty** in this build, so nothing links a
  spell to a scaling class, and in the GameTable **every class column is zero
  across all 123 levels** — Rogue through Evoker, including Warrior — while
  `Item`, `Consumable`, `Gem1–3`, `Health`, `DamageReplaceStat`,
  `DamageSecondary` and `Mana Consumable` all carry data. The infrastructure
  is present and unpopulated. A scaling question cannot be answered without
  checking both, and before 2026-09-20 neither was being extracted.

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
├── .claude/skills/wow-query/SKILL.md   # how to query wow.db, and the rules
├── README.md
├── builds.json              # manifest index — buildConfig/cdnConfig per build
├── sod_allowlist.json       # SoD tags known live in Forever (live_in_forever=1)
├── sod_manual.json          # hand-audited SoD tags no signal reaches
├── requirements.txt         # one entry: mcp. everything else is stdlib
├── scripts/
│   ├── config.py            # product code, paths, build filter — SINGLE SOURCE
│   ├── run-wtl.ps1          # launch WTL from the correct working directory
│   ├── sync_refs.py         # pull WoWDBDefs / listfile / TACTKeys
│   ├── fetch_builds.py      # poll version endpoint, update builds.json
│   ├── extract_db2.py       # WTL HTTP -> CSV per table, plain + hotfixed
│   ├── inventory.py         # file listing + magic-byte classification
│   ├── extract_gametables.py# GameTables/*.txt -- NOT DB2s, see Key facts
│   ├── enrich.py            # ID -> human-readable context, for the reports
│   ├── sod_tags.py          # SoD annotation -> sod_tags in wow.db; NOT contamination
│   ├── test_sod_tags.py     # pins the measured SoD IDs (stdlib unittest)
│   ├── spell_reach.py       # player reachability -> spell_reach in wow.db; patch-note partition
│   ├── test_spell_reach.py  # pins the measured reachability IDs (stdlib unittest)
│   ├── build_db.py          # CSVs -> out/<build>/wow.db, one queryable file
│   ├── query.py             # read-only SQL CLI over wow.db
│   ├── mcp_server.py        # same database over MCP stdio, read-only
│   ├── render_patchnotes.py # self-contained HTML, hotfix + build-diff modes
│   ├── player_notes.py      # build-diff player edition: class -> spec -> spell, live vs live
│   ├── test_player_notes.py # pins provenance and buff/nerf direction rules (stdlib unittest)
│   └── diff_builds.py       # compare two build dirs, emit markdown
├── out/                     # GITIGNORED — extracted data
│   └── <version>.<build>/
│       ├── db2/*.csv           # plain DB2s, as shipped in the build
│       ├── db2_hotfixed/*.csv  # same tables with the hotfix overlay applied
│       ├── hotfixes.csv        # push IDs + changed rows from Cache/ADB/enUS
│       ├── files.csv           # fdid, path, size, encrypted, content_type
│       ├── gametables/*.txt    # tab-separated, NOT DB2s — see Key facts
│       ├── wow.db              # SQLite over all of the above, rebuildable
│       └── manifest.json       # row counts, layouthashes, metrics
├── reports/                 # GITIGNORED — generated diff output
│   ├── <from>_to_<to>.md
│   ├── hotfix_<build>.md
│   ├── hotfixwave_<build>_since_<date>.html
│   ├── sod_tags_<build>.md              # SoD tiers, chains, coverage
│   ├── patchnotes_<build>.html          # readable, self-contained
│   └── patchnotes_<from>_to_<to>.html
└── vendor/                  # GITIGNORED — cloned third-party tools
```

### `wow.db`

`build_db.py` loads every extracted CSV into one SQLite file per build. The
CSVs remain the source of truth; this is a query surface over them, rebuilt
from scratch on each run and never updated in place. Gitignored with the rest
of `out/`.

| Prefix | Source | Meaning |
|---|---|---|
| *(none)* | `db2_hotfixed/` | the **live** view — shipped plus hotfixes |
| `plain_` | `db2/` | **as shipped** in the client |
| `gt_` | `gametables/` | tab-separated GameTables, **not DB2s** |

The prefixes exist so the comparison this repo is built around is one query:

```sql
SELECT h.ID, h.Display_lang
FROM ItemSparse h LEFT JOIN plain_ItemSparse p USING (ID)
WHERE p.ID IS NULL;        -- 4,218 rows that exist only as live hotfix data
```

`gt_` is not decoration. `SpellScaling` exists both as a DB2 (204-empty in
this build) and as `SpellScaling.txt` in the GameTables, and they are
different things — an unprefixed load would collide the moment the DB2 stopped
being empty.

Two details worth knowing before querying:

- **Column names are sanitised.** `Corpse[0]` becomes `Corpse_0`, because
  brackets are alternative identifier quoting in SQLite and a column you have
  to escape carefully is a column nobody will query. Array foreign keys are
  still indexed: the array suffix is stripped before asking whether a column
  ends in `ID`, so `LightParamsID[3]` → `LightParamsID_3` is indexed.
- **Types are sniffed and declared as affinities**, so `WHERE ID = 6343`
  matches rather than silently finding nothing against TEXT `'6343'`. A
  mis-sniff degrades safely — SQLite stores a value that will not convert
  as-is.

Four derived tables follow the load: `sod_tags` and `sod_tags_coverage` (see
**Season of Discovery tags**), then `spell_reach` and `spell_reach_coverage`
(see **Player reachability** there). They are computed from the live tables
and annotate only.

At 1.60.1.69913: 1,263 tables, 3,844,494 rows, 4,210 indexes, ~317 MB, ~20s.
69876 and 69893 load 1,262 — they have no `TimeEventData`, which exists only
as hotfix data. `_build_info` records which build the file is for, so one
cannot be mistaken for another.

Ad-hoc questions go through `scripts/query.py`, which opens the file
read-only:

```bash
python scripts/query.py "SELECT ID, Name_lang FROM SpellName LIMIT 5"
python scripts/query.py --tables spell     # list matching tables + row counts
python scripts/query.py --schema SpellEffect
```

`.claude/skills/wow-query/SKILL.md` carries the join paths and the query rules
— FK resolution via `/dbc/relations` rather than value matching, array-suffixed
FK columns, unverified columns, context-gated enums, base rates before signals,
and that a missing table usually means "not in this build".

> **`.claude/skills/wow-query/SKILL.md` is force-added, and that is
> deliberate.** The global gitignore at `~/.config/git/ignore` excludes
> `**/.claude/` as *per-project Claude Code state* — settings, caches, session
> files — which is the right default and should stay.
>
> This file is not that. It is shared documentation: the schema's prefixes,
> the join paths, and nine rules each derived from a measurement recorded in
> this document. Anyone cloning the repo needs it to query `wow.db` without
> repeating mistakes that are already written down — resolving FKs by value
> and picking up numeric collisions, missing array-suffixed FK columns,
> asserting a meaning for an unverified column, decoding a context-gated enum
> without its gate.
>
> Committed with `git add -f`. If more files ever land under `.claude/` here,
> add them the same way and only when they are documentation rather than
> state — do not relax the global rule to cover them.

### MCP server

`scripts/mcp_server.py` exposes the same database over MCP stdio, for clients
that cannot run `query.py` themselves.

Four tools: `list_tables`, `describe_table`, `query`, `get_conventions`.
Install with `pip install -r requirements.txt` — see **Prerequisites**.

- Read-only is the **driver's** guarantee, not a SQL check: the connection is
  a `file:...?mode=ro` URI, and `ATTACH` is additionally denied by an
  authorizer so a query cannot pull in a second, writable database. The
  statement-shape check exists for clear error messages, not for safety.
- `query` caps results at **100 rows** and says so when it truncates, and
  refuses a query with neither `LIMIT` nor `WHERE` against a table over
  10,000 rows. Aggregates without `GROUP BY` are exempt, so
  `SELECT COUNT(*) FROM ItemSparse` works.
- `get_conventions` returns the nine rules from
  `.claude/skills/wow-query/SKILL.md` **verbatim**. It exists because an MCP
  client cannot see the skill file, and without those rules it will reproduce
  exactly the mistakes they were written to prevent.
- Every response names the build it came from.

Claude Desktop, in `claude_desktop_config.json` — `%APPDATA%\Claude\` on
Windows, `~/Library/Application Support/Claude/` on macOS:

```json
{
  "mcpServers": {
    "wow-datamine": {
      "command": "python",
      "args": ["C:\\path\\to\\wow-datamine\\scripts\\mcp_server.py"]
    }
  }
}
```

**Claude Desktop must be fully restarted after editing that file** — quit it,
not just close the window. It reads the config once at startup, so an edit
with the app still running does nothing, which is indistinguishable from a
broken server.

> Written against **mcp 2.x**, where `FastMCP` was renamed `MCPServer`
> (`from mcp.server.mcpserver import MCPServer`). Examples written for 1.x
> will not import; pin `mcp<2` only if you need that older code.

### `manifest.json` resolution values

One per table, describing how it resolved against the **shipped** build:

| Value | Meaning |
|---|---|
| `ok` | 200, has rows |
| `empty` | 204 — defined but zero rows, in both variants. A success |
| `hotfix_only` | 204 plain, 200 hotfixed — exists **only** as live hotfix data |
| `not_in_build` | 404 — not present in this build |
| `error` | 400, or a transport failure. `error` field carries the reason |

`hotfix_delta` is set independently, from a content hash of the two CSVs, and
is true for `hotfix_only` tables as well as changed `ok` ones.

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
- **Every rule here is either measured or source-read, and must say which.**
  Reading the controller tells you what the code *can* do. Only a live
  response tells you what it *does*. Source-reading produces a hypothesis;
  measurement produces a rule. Label the difference and never let the first
  masquerade as the second.

  `docs/wtl-api.md` marks measured routes ✅ and keeps an explicit
  "still source-read only" list. Anything unexercised says so where it is
  written, not only in a verification log at the bottom, because the person
  who acts on a rule reads the rule and not the appendix.

  This is not hypothetical. The `build=` rule on `/dbc/meta/getMappings` was
  derived correctly from `BuildRange.Contains` — componentwise comparison,
  1.60.x matches no preset range — committed as though verified, and was
  **backwards**. The real behaviour is that omitting `build=` returns two
  conflicting enum variants with the retail one first, so a decoder taking the
  first match would have labelled every Forever weather row with **retail**
  names: `0 None, 1 Clear, 2 Rain` instead of `0 Clear, 1 Rain, 2 Snow`. Wrong
  data, plausible output, no error — the same shape as every other gotcha in
  the WTL section, arrived at by the documentation process itself. One HTTP
  request settled it. Corrected in `7575f2c`; the original is `7655839`.

  Practically: source-read a route to know what to ask for, then ask. When WTL
  is not running, write the finding down as unexercised and **promote it only
  after a response confirms it**. A hypothesis recorded honestly is useful;
  a hypothesis recorded as fact is a trap with this project's name on it.
- **A fix that works does not confirm the diagnosis that motivated it.**
  Symptom, fix and mechanism are three separate claims. A fix landing and the
  symptom going away establishes the first two. The **mechanism has to be
  measured on its own**, because a correct fix sitting next to a wrong
  explanation looks exactly like a correct fix sitting next to a right one —
  and it is the explanation that gets reused on the next problem.

  The worked example. Three builds produced byte-identical `files.csv`.
  `inventory.py` was found to read row indices `0, 1, 4, 5` and never index 3,
  `availableInBuild`, and the conclusion drawn — and stated as fact — was that
  every other column comes from the global listfile, so the output could not be
  build-specific and the encrypted-file count "cannot change no matter what
  Blizzard does". Two real defects were then fixed on that basis.

  The mechanism was wrong. `ListfileController.cs:208` already intersects
  `Listfile.NameMap` with `CASC.AvailableFDIDs` before paging, so the route
  **only ever returns in-build files** and ignoring column 3 changed nothing.
  Measured after the fix: `skipped_not_in_build = 0`, and the fixed script
  reproduced the original CSV **byte for byte** — same SHA-256,
  `86733ec34aee…`. Neither defect could have caused the symptom:

  | Claim | Status |
  |---|---|
  | `availableInBuild` was unread | **true**, and worth fixing for the `showAllFiles` case |
  | `without_name` used the forbidden subtraction | **true**, reported 0 against 19,583 |
  | Either caused the identical hashes | **false** — one was a no-op, the other never touched a row |
  | The file set is identical across all three builds | **true**, and it is the whole explanation |

  The identical inventories were real data: only 2 of 610 shipped DB2 tables
  differ across 69876/69893/69913, so an unchanged file set is exactly what to
  expect. An assertion was added to fail on identical hashes, on the assumption
  that identical meant broken; it fires on correct data and is a false positive
  by construction.

  **This is the second instance of source-read reasoning committed as fact**,
  after the `build=` meta-route rule (`7655839` → `7575f2c`). Both followed the
  same shape: read the code, build a mechanism that explained the symptom, skip
  the measurement because the reasoning felt tight, write it down as fact. The
  reasoning being *sound* is what makes it dangerous — it was sound and still
  wrong, because it rested on an unread line thirty lines away.

  Practically: when a symptom motivates a fix, **state the mechanism as a
  separate, testable claim and measure it separately.** "Does the fix remove
  the symptom" and "is my explanation of the symptom correct" are different
  experiments. Here the second one cost a single request — sampling
  `availableInBuild` over 20,000 rows and finding every value `true`.
- **Match on declared FK relations, not on raw values.** Scanning every column
  of every table for a numeric value finds real references and coincidental
  collisions in the same pass, with nothing to tell them apart. Looking for
  spell 6343 that way returns `WMOMinimapTexture::ID`, `TaxiPathNode::ID` and
  `UiTextureAtlasMember::ID` alongside the genuine `SkillLineAbility::Spell` —
  those tables simply have a row whose own ID is 6343.

  Resolve the relation instead: `GET /dbc/relations/<Table>::<Column>` returns
  every column DBD declares as a foreign key to that one (157 for a spell at
  1.60.1.69913), and only those columns are worth reading. The difference is
  not cosmetic — the raw-value scan reported ~47 "references" for a classic
  Thunder Clap rank against a true count of 17, and the noise is what would
  have hidden the 15–17 vs 10 asymmetry that finding #7 turns on.

- **An unscanned zero is not a clean result.** A detector that could not read
  the data returns exactly what a detector that read it and found nothing
  returns. Before reporting "0 findings", establish that something was
  actually capable of firing — and report coverage next to the count so a
  reader can tell the two apart without going to the source.

  Measured 2026-09-20. `contamination.py` has three rules, and two are pinned
  to a single table by name: `light_absent_map` to `Light`, `orphan_removal`
  to `Item` *and* to removed rows. The third, `dangling_map_ref`, only reads
  columns named `instance_id`, `instanceid`, `continentid`, `mapid` or
  `map_id`. Submitting the six 461xxx Thunder Clap rows — 72 rows across 11
  spell tables — produced **0 findings and 0 applicable rules**. No spell
  table carries a map-reference column, so nothing was ever read. The same
  applies to the first real build diff: `Cfg_GameRules` and
  `Cfg_SuperDistrict` are also unreadable by every rule.

  `scan()` now returns `(findings, ref, coverage)` with a verdict of
  `scanned`, `not_scanned` or `no_data`, and both diff scripts render
  **NOT SCANNED — this is not a clean result** with a per-rule reason table
  instead of "no suspected retail contamination". When rules *did* run, the
  report names which ones and which were never applicable, so even a genuine
  zero states its own scope.

  This is the same class as the encrypted-count detector (finding #6) passing
  on three builds whose file sets were byte-identical: a pass on data that
  could not have produced a failure is not evidence. The generalisation:
  **a detector's output is only meaningful once you have shown the detector
  could have said otherwise.**
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
  `Achievement` that way collapsed 114 of its 233 rows onto duplicate
  description strings and reported no change against a real 233 → 232
  delta. Always key on the column literally named `ID`, fall back to column 0
  only when absent, and state which key was used in the output. This applies
  to `diff_builds.py` as much as to `diff_hotfixes.py`.

  **A newly generated layout may have no column named `ID` at all.** WoWDBDefs
  names every column of a fresh layout `Field_<build>_NNN` and marks the ID
  with a `$id$` annotation instead. `UiModelSceneActor` at 1.60.1.70009 is
  the case: layout `B777EC3A`, ID column `Field_1_60_1_70009_002`. Keying on
  column 0 there collapsed 1,007 rows onto 128 unique `ScriptTag` strings.
  The diff reported "126 → 128 rows, +2" against a real 1,006 → 1,007, +1.
  `key_index(header, table)` now tries three things in order: a column named
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

  **Critically: a table with a schema change must stay in the report even when
  its magnitude is zero.** Suppressed field diffs leave added/removed/changed
  all at 0, and a "skip anything with magnitude 0" rule then silently drops the
  tables that most need flagging. This was a real bug in `diff_builds.py`,
  caught only because the test fixture included a schema change with no row
  changes. Report the change in the summary, in a dedicated section, and in the
  table's own section, stating which comparisons were suppressed and why.

  **A layout change with byte-identical data is still a schema change.**
  `diff_table()` used to skip identical CSVs before it looked at the
  layouthash. So `SpellDispelType` at 1.60.1.70009 never reached the schema
  section: layout `47AA7AEB` → `3B574D4B` (retail 12.1.5's layout, `Mask`
  u8 → u16), all 11 rows identical. The layouthash is now checked first. A
  table like that is reported as "data byte-identical (storage-only change)",
  with nothing suppressed.

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

---

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
   rewritten under an unchanged FDID (70058 was nothing else). It caches to
   `out/<to>/content_diff_<from>.json`, which `render_patchnotes.py` reads.
   With WTL down and no cache, both reports say **not measured**.
10. Commit `builds.json` and the new report.

### Hotfix-only day (downtime with no new build)

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
- **A re-extract on an unchanged build is cheap.** 13s for all 1161 tables,
  measured 2026-09-21, because WTL serves the exports from cache. There is no
  reason to extract selectively.
- **Do not drive the wave diff off `/dbc/hotfixes/list`.** Scan every CSV.
  Measured 2026-09-21: the list reported new records in **19** tables while
  only **8** tables' exported rows actually moved, and it named **5**
  `BroadcastText` records against **11** rows that appeared. Trusting it would
  have invented 11 empty tables and missed 6 real additions.
- **`firstDetected` is when WTL first saw a record, not when Blizzard pushed
  it.** It is an upper bound on push time. The wave page uses the baseline
  directory's mtime as the cutoff and says so; do not present the window as
  push times.
- Re-run `diff_hotfixes.py`, `inventory.py` and `build_db.py` afterwards — the
  cumulative report, manifest and database all describe the old overlay until
  you do. (A re-extract used to **erase** `manifest.json`'s inventory section:
  `write_manifest()` rewrote the whole file. That is how 69913 and 69977 lost
  theirs. It now carries forward keys it does not own. `inventory.py` also
  recomputes a missing baseline from `files.csv`.) `inventory.py` will print its "file set did not change" banner; on a
  hotfix-only day that is the correct result, not a failure.
- **`build_db.py` cannot replace `wow.db` while `mcp_server.py` is running.**
  Windows holds the file open even for a read-only connection, `os.replace`
  fails with `WinError 5`, and the rebuilt database is left as `wow.db.tmp`.
  Stop the MCP server, then re-run.

---

## Scope

Reading and diffing local client data for analysis. Not client modification,
not CASC writing, not redistribution of extracted assets.
