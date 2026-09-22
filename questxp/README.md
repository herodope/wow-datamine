# QuestXPSweep — a quest XP table for route planning

Build 1.60.1.69913 (WoW Forever, Classic+).

The client keeps a local copy of every quest record the server has sent it, in
`Cache/WDB/<locale>/questcache.wdb`. It only ever holds quests that were
actually asked for, and it is **rewritten from scratch every session**. So:

1. An addon asks the server for all 6,600 quest IDs in `QuestV2`.
2. The client writes them to `questcache.wdb` when you log out.
3. You copy that file somewhere safe before logging back in.
4. A parser merges however many captures it takes and computes base XP.

```
questxp/
  QuestXPSweep/              the addon
    QuestXPSweep.toc
    QuestXPSweep.lua
    QuestIDs.lua             GENERATED - do not hand-edit
  gen_quest_ids.py           regenerates QuestIDs.lua from wow.db
  parse_questcache.py        the parser
  README.md
```

---

## What is verified, and what is not

This repo's rule is that a claim says whether it was measured or reasoned.

**Measured 2026-09-22** against a real capture: one full sweep of all 6,600
`QuestV2` ids on a level 20 character, 5.5 minutes, producing 2,097 cache
records on build 69913 / enUS. Record alignment is independently corroborated:
the title string embedded later in each payload matches the quest its header
names.

| Claim | Status |
|---|---|
| Offsets 0, 8, 16, 24, 28, 40 | **confirmed** — hold across all 2,097 records |
| Offset 44 (the f32) | **confirmed as a field**, range 1.0-4.35 exactly as reported |
| Offsets 36, 48, 52 | **populated** — carry varying real data (677 / 498 / 2,097 nonzero). Labels still guesses |
| Offset 32 "suggested group size" | **refuted** — zero on every record, *including* all 70 dungeon quests, which are the ones that would carry a party size |
| Offsets 12 and 20 ("unknown") | **always zero** on all 2,097 records |
| Offset 4 `questType` | **inferred** — a real 3-value enum (2 ×1953, 0 ×136, 1 ×8), meaning unknown |
| `xpMult` as the *name* for offset 44 | **inferred** — the range fits a multiplier and also fits other things |
| `baseXP = QuestXP[level][Difficulty_<tier>] * xpMult` | **unverified** — the hypothesis the whole exercise tests |
| The level-penalty table and `ROUND(xp * pct / 5) * 5` | **unverified** — from the Warcraft Wiki, not measured on this client |

`moneyTier` at offset 52 is genuinely a separate field from `tier` at offset
40, not the same value read twice: the two agree on only 36% of records. Quest
1487 has 6 in both, which is a coincidence and would have been misleading as a
single sample.

`verify.csv` settles the last two rows. Until it has data in it, treat
`baseXP` as a well-supported guess, not a number.

`parse_questcache.py --selftest` runs against a synthetic file built from this
same spec, so it proves the decoder matches the spec — it can only ever agree
with itself. The capture is what proves the spec matches the client.

### The sweep that produced this

| | |
|---|---|
| Coverage | 6,600 of 6,600 ids, cursor reached 6,601 |
| `ok` | 2,097 — exactly the number of cache records written, nothing lost |
| `fail` | 4,503 — the server answered, it answered no |
| `noresp` | 0 — `/qxs skip` was never needed |

The 4,503 are **explicit negatives**, not dropped requests. Those IDs exist in
the client's DB2s but are not loaded server-side, so ~32% coverage looks like
the real ceiling rather than a throttling artifact.

### What came out

| | Count |
|---|---|
| Quests with a usable nonzero `baseXP` | **1,766** |
| `zero_xp_tier` | 306 (305 at tier 0, plus 92422 at tier 8) |
| `no_questxp_row` | 25 — all level 0, all `<UNUSED>` / `<nyi>` / test quests |
| `xp_mult` | 62 — values 2.5, 2.6, 2.9, 3.0, 3.05, 3.2, 3.4, 3.75, 4.35 |
| `dev_title` | 52 |

Tier 0 most likely means genuinely no XP rather than a broken model: the
titles are recognisable breadcrumbs (`Melor Sends Word`, `Journey to
Astranaar`), class spell quests (`The Tome of Divinity`, `Desperate Prayer`,
`Shadowguard`) and one literally called `<TXT> No Reward` — all known zero-XP
quest types. That is title recognition, not measurement. Turning one in with
`/qxs verify on` would settle it.

`Difficulty_0`, `_8` and `_9` are zero for **all 100** `QuestXP` rows, and
`QuestXP` is byte-identical to `plain_QuestXP`, so that is as shipped and not
a hotfix artifact. Tier is effectively 1-7.

**Open: quest 92422**, `The Wrath of Rath'mael` — level 22, tier 8, info 81
(dungeon), multiplier 3.2, money 0, and it computes to 0 XP. It is the only
tier-8 quest in the whole capture. A dungeon quest with a 3.2x multiplier
awarding nothing does not add up, and one outlier is not enough to resolve it.

---

## Install the addon

Copy the `QuestXPSweep/` folder into your Forever client's AddOns directory:

```
<install root>/_classic_beta_/Interface/AddOns/QuestXPSweep/
```

The `.toc` is built for `## Interface: 16001`. If the client ever reports the
addon as out of date, get the current number with `/dump select(4,
GetBuildInfo())` and update the first line.

Nothing in the addon runs until you type a command: no frames, no event
registrations, no saved settings turned on. That is deliberate.

---

## Run a sweep

```
/qxs start          begin, or resume from where the cursor left off
/qxs stop           pause; the cursor is kept
/qxs status         done / ok / fail / remaining counts
/qxs skip           give up on unanswered requests and carry on
/qxs inflight <n>   requests in flight at once (default 10, max 50)
/qxs verify on|off  log quest turn-in rewards (default off)
/qxs reset confirm  clear everything and start over
```

`/qxs start` fires `C_QuestLog.RequestLoadQuestByID` for each ID, keeping ten
in flight, and records each `QUEST_DATA_LOAD_RESULT` as `ok` or `fail`.

### When it stops moving, type `/qxs skip`

Some IDs are in `QuestV2` but the server answers nothing for them — 92743 is
the known example. It is not known whether the event fires with
`success=false` or never fires at all, so the addon handles both: a failure is
recorded as `fail`, and a request that never comes back holds its slot until
you clear it.

There is no timeout, on purpose. House rules forbid wall-clock gates as logic,
so `/qxs skip` is the escape hatch: it marks everything currently unanswered as
`noresp` and refills the window. If `/qxs status` stops changing, skip and it
carries on. If a skipped request is answered later while the sweep is still
running, the record is upgraded from `noresp` to the real result.

### Between sessions

The cache does not accumulate. After each sweep session:

1. **Log out all the way to the desktop.** Not to character select — the file
   is written when the game process exits. `/reload` does not write it either.
2. Copy `Cache/WDB/enUS/questcache.wdb` somewhere, named per session:
   `questcache_s1.wdb`, `questcache_s2.wdb`, and so on. `questxp/cache/` is
   gitignored and is a good place for them.
3. Log back in and `/qxs start` again. The sweep resumes from the saved cursor.

Repeat until `/qxs status` shows `remaining 0`.

A cursor at the end of the list does not by itself mean the list is covered:
logging out mid-sweep leaves the in-flight IDs with no result and the cursor
already past them. `/qxs start` notices that, wraps to the beginning and picks
up only the unrecorded IDs.

### Verification logging (optional, off by default)

```
/qxs verify on
```

From then on, every quest you turn in records the quest ID, the XP actually
awarded, your level and the quest's level. That is the ground truth the
computed numbers get checked against, and it is the only way to settle the
penalty table. Leave it on while you play normally.

### What gets saved

`WTF/Account/<ACCOUNT>/SavedVariables/QuestXPSweep.lua`:

| Key | Holds |
|---|---|
| `quests[id]` | `s` = ok / fail / noresp, `t` = title, `l` = quest level |
| `cursor` | index into the ID list, so a sweep resumes across sessions |
| `sessions` | one entry per login: start/stop time, ID range, per-status counts |
| `turnIns` | the verification log, when it is switched on |
| `verify`, `inFlight` | settings |

SavedVariables are written at logout, same as the cache. Copy this file too, or
just leave it in place and point the parser at it.

---

## Run the parser

Requires an extracted build in `out/` (`scripts/build_db.py`) for `QuestXP` and
`QuestV2`. Stdlib only, no new dependencies.

```bash
# check the decoder against the two known records first
python questxp/parse_questcache.py --selftest

# then the real thing
python questxp/parse_questcache.py \
    --cache questxp/cache/ \
    --saved "C:/.../WTF/Account/<ACCOUNT>/SavedVariables/QuestXPSweep.lua"
```

`--cache` takes any mix of files and directories and merges them by quest ID;
the first file to carry a quest wins, and any quest that decodes differently in
two files is flagged `cache_conflict` rather than silently resolved. `--build`
defaults to the newest extracted build. `--out-dir` defaults to `questxp/out/`,
which is gitignored.

Titles come from the addon's SavedVariables, not from the cache. Without
`--saved` the title and status columns are blank and `verify.csv` is skipped.

### quests.csv

One row per quest ID in `QuestV2`, in the cache, or in the addon's records.

`questID, title, level, minLevel, sort, info, tier, xpMult, baseXP, status, flags`

`baseXP` is the **raw product**, deliberately not rounded to the 5 XP grid,
because the live rounding rule is unverified. Float32 storage noise is removed
(2.9 arrives as 2.9000000953674316, which would make 2050 * 2.9 read
5945.000196) but nothing is moved onto the grid: a genuinely fractional product
like 455 * 4.35 stays 1979.25.

Flags, semicolon-separated:

| Flag | Means |
|---|---|
| `zero_xp_tier` | `QuestXP` has 0 for this tier — tiers 0, 8 and 9 are all-zero in this build. Quest 92422 is tier 8. Where the XP actually comes from is an open question |
| `xp_mult` | the f32 at offset 44 is not 1.0. Mostly dungeon quests. Check one live before trusting it |
| `not_in_cache` | in `QuestV2` but no cache file has it. Either not swept yet, or the server has nothing (92743) |
| `not_in_questv2` | in a cache file but not in `QuestV2` — worth a look |
| `no_questxp_row` | quest level has no row in `QuestXP` (it only covers 1-100) |
| `dev_title` | the title matches a dev/test pattern. A heuristic that flags rows for a human, nothing more. Quest 1 is `The "Chow" Quest (123)aa` |
| `cache_conflict` | two cache files decoded this quest differently |

### verify.csv

Written only when `turnIns` has entries. Each logged turn-in against what the
model predicts:

`questID, title, cacheLevel, loggedLevel, playerLevel, levelDiff, pct, baseXP, expectedXP, observedXP, delta, match, note`

The penalty applied:

| Player level vs quest level | XP |
|---|---|
| up to +5 | 100% |
| +6 | 80% |
| +7 | 60% |
| +8 | 40% |
| +9 | 20% |
| +10 or more | 10% |

then `ROUND(xp * pct / 5) * 5`, halves rounding up. Quests below level 10 are
reported to lose full XP one level earlier, so their whole table shifts down by
one (+4 is still 100%, +5 drops to 80%).

All of that is from the Warcraft Wiki and **none of it is verified on this
client**. A `match=no` row is as likely to mean the model is wrong as the data
is. The constants are `PENALTY_BANDS`, `EARLY_PENALTY_BELOW_LEVEL` and
`XP_ROUNDING_STEP` near the top of the parser; change them there, not inline.

---

## Regenerating the ID list

`QuestIDs.lua` is generated. Do not hand-edit it.

```bash
python questxp/gen_quest_ids.py --build 1.60.1.69913
```

It reads `SELECT ID FROM QuestV2 ORDER BY ID` from `out/<build>/wow.db` —
the hotfixed view, not `plain_QuestV2`, so quests added by a hotfix wave are
included. The build defaults to the newest extracted one. The addon records
which build the list came from and `/qxs status` says so if it does not match
the client you are running.

After a new build: re-extract, regenerate, then `/qxs reset confirm` and sweep
again. Quest records can change between builds and the merge keeps the first
file that carries a quest, so mixing captures across builds would quietly
preserve stale rows. The parser warns when a cache file's header build does not
match the build it is joining against.
