# Key facts

Facts about the Forever client data that shape every extraction and report:
FDIDs, DB2 layouts, naming and typing coverage, hotfixes, encryption and
GameTables.

- **FDIDs share the retail namespace.** `wowdev/wow-listfile` applies directly.
  No Forever-specific listfile exists.
- **DB2 layouts are Classic-flavor, not retail.** WoWDBDefs resolves via
  layouthash, but Forever is new — expect unknown columns in new tables. Name
  them `unk_<offset>`; never guess a meaning in committed output.
- **Typing is the bigger gap, but naming is not zero.** 1.60.1.69913 contains
  **1,441,771** files. **19,583 of them (1.4%) have an empty name** despite
  being members of `Listfile.NameMap` — of those, 11,842 remain `unk` and
  7,721 are classified as `blp` by the magic-byte pass. (2,274,258 is the
  *listfile's* total size across the whole retail FDID namespace, not a count
  for this build.) On typing: **99,348 files have no `content_type`**, and the
  magic-byte pass recovers **87,506** of them — 79,784 as `wmo_or_adt` —
  leaving 11,842 unknown. **Magic-byte classification is valuable and worth
  maintaining.** Filename recovery is the lower-value half.
- **Hotfixes are a separate diffable source.** WTL loads live hotfix data
  from the client's `Cache/ADB/enUS` directory and tracks push IDs. This is
  distinct from the static DB2s shipped in the build: hotfixes are Blizzard
  tuning live data between client patches. Diffing them answers a different
  question than diffing DB2s, and the two must not be conflated.
- **Hotfixes are per build. A new build starts with an almost empty
  overlay.** Earlier hotfixes are either promoted into the new client or
  lapse. A hotfix vanishing on a new build is **not** a revert decision until
  a later wave on that build confirms it; it may simply not have been
  re-pushed yet. And a client change on a new build may already have been
  live for days. Compare the new client against the **previous overlay** as
  well as the previous client.
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
  the columns, and diffing them as text is the point. 284 KB, ~2s per build.

  **`SpellScaling.txt` is where class spell scaling would live.** The DB2
  named `SpellScaling` ships **204-empty**, so nothing links a spell to a
  scaling class, and in the GameTable **every class column is zero across all
  123 levels** — Rogue through Evoker, including Warrior — while `Item`,
  `Consumable`, `Gem1–3`, `Health`, `DamageReplaceStat`, `DamageSecondary` and
  `Mana Consumable` all carry data. The infrastructure is present and
  unpopulated. A scaling question cannot be answered without checking both.
