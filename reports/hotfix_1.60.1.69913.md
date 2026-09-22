# Hotfix diff — 1.60.1.69913

Live hotfix data versus the DB2s as shipped in the client. Both sides come from the same build, so every difference here is something Blizzard changed **without a client patch**.

Generated 2026-09-22T00:02:29+00:00

| | |
|---|---|
| Tables differing | 19 |
| Rows added | 8,374 |
| Rows removed | 74 |
| Rows changed | 37 |
| Hotfix records known to WTL | 27,113 |
| Distinct real push IDs | 16 |
| Records with only a synthetic push ID | 8,204 |
| Tables extracted | 1161 |

---

## Retail contamination

Rows that look like retail-era data in a Classic+ build. `wow_classic_beta` is a recycled product code and Forever shares tooling with retail, so these turn up and get pruned over time — a **new** one appearing is itself a signal.

| Confidence | Rule | Table | Record | Detail |
|---|---|---|---|---|
| HIGH | `dangling_map_ref` | `Achievement` | `9275` | Instance_ID = 1358, a map not present in this build |
| HIGH | `light_absent_map` | `LightParams` | `453` | replaced in Light 269.LightParamsID[2]; referenced only by Light 16161 on map(s) 3064 absent from this build |
| MEDIUM | `orphan_removal` | `Item` | `72 rows` | 72 removed rows have no ItemSparse/ItemSearchName data (ClassID 4/SubclassID 0 × 72; InventoryType 12 × 32, InventoryType 11 × 22, InventoryType 2 × 18). Orphanhood alone is NOT a signal — 8,196 of 31,675 Item rows lack display data in this build, because ItemSparse ships incomplete and arrives by hotfix. The signal is that these were removed together. |

---

## Summary

| Table | Added | Removed | Changed | Plain → Hotfixed | Push IDs |
|---|--:|--:|--:|---|---|
| `ItemSparse` | 4,308 | 0 | 5 | 19,171 → 23,479 | bulk injection (4,191 records, no real push attribution); real: 112078, 112129, 112144, 112145 +2 |
| `ItemSearchName` | 4,013 | 0 | 0 | 6,621 → 10,634 | bulk injection (4,013 records, no real push attribution) |
| `Item` | 0 | 72 | 0 | 31,675 → 31,603 | 112078 |
| `BroadcastText` | 41 | 0 | 0 | 12 → 53 | 112142, 112203 |
| `LightData` | 0 | 0 | 16 | 5,375 → 5,375 | 112132 |
| `LightDataGlobalVolumeFog` | 0 | 0 | 7 | 9,617 → 9,617 | 112132 |
| `ConversationLine` | 5 | 0 | 1 | 553 → 558 | 112203 |
| `GlobalStrings` | 0 | 0 | 3 | 27,262 → 27,262 | 112128 |
| `TimeEventData` *(hotfix-only)* | 3 | 0 | 0 | 0 → 3 | 112079 |
| `LoadingScreenTaxiSplines` | 2 | 0 | 0 | 16 → 18 | 112120 |
| `Achievement` | 0 | 1 | 0 | 233 → 232 | 112039 |
| `Achievement_Category` | 0 | 1 | 0 | 35 → 34 | 112039 |
| `AreaTriggerActionSet` | 1 | 0 | 0 | 241 → 242 | 112184 |
| `Faction` | 0 | 0 | 1 | 253 → 253 | 112201 |
| `GossipNPCOption` | 1 | 0 | 0 | 1,539 → 1,540 | 112142 |
| `Light` | 0 | 0 | 1 | 625 → 625 | 112132 |
| `LightParams` | 0 | 0 | 1 | 827 → 827 | 112132 |
| `SpellMisc` | 0 | 0 | 1 | 31,748 → 31,748 | 112209 |
| `SpellTargetRestrictions` | 0 | 0 | 1 | 4,447 → 4,447 | 112209 |

---

## ItemSparse

4,308 added · 0 removed · 5 changed · 19,171 → 23,479 rows · keyed on `ID`

### Added (4,308)

| ID | Push ID | First fields |
|---|---|---|
| `720` | *bulk* | — |
| `727` | *bulk* | — |
| `754` | *bulk* | — |
| `789` | *bulk* | — |
| `790` | *bulk* | — |
| `791` | *bulk* | — |
| `816` | *bulk* | — |
| `820` | *bulk* | — |
| `821` | *bulk* | — |
| `826` | *bulk* | — |
| `827` | *bulk* | — |
| `832` | *bulk* | — |
| `863` | *bulk* | — |
| `865` | *bulk* | — |
| `866` | *bulk* | — |
| `867` | *bulk* | — |
| `868` | *bulk* | — |
| `870` | *bulk* | — |
| `871` | *bulk* | — |
| `872` | *bulk* | — |
| `873` | *bulk* | — |
| `880` | *bulk* | — |
| `885` | *bulk* | — |
| `886` | *bulk* | — |
| `888` | *bulk* | — |
| … | | *4,283 more* |

### Changed (5)

**ID 247886** — push 112129 (valid)

| Field | Before | After |
|---|---|---|
| `Flags[0]` | `0` | `2048` |

**ID 248002** — push 112146 (valid)

| Field | Before | After |
|---|---|---|
| `Flags[0]` | `0` | `2048` |

**ID 253664** — push 112145 (valid)

| Field | Before | After |
|---|---|---|
| `SellPrice` | `5000` | `200` |

**ID 254871** — push 112155 (valid)

| Field | Before | After |
|---|---|---|
| `Flags[0]` | `64` | `0` |
| `Flags[3]` | `1` | `0` |

**ID 257945** — push 112144 (valid)

| Field | Before | After |
|---|---|---|
| `Flags[0]` | `2048` | `0` |

---

## ItemSearchName

4,013 added · 0 removed · 0 changed · 6,621 → 10,634 rows · keyed on `ID`

### Added (4,013)

| ID | Push ID | First fields |
|---|---|---|
| `720` | *bulk* | Brawler Gloves · 3 · 0 |
| `727` | *bulk* | Notched Shortsword · 2 · 0 |
| `754` | *bulk* | Shortsword of Vengeance · 3 · 0 |
| `789` | *bulk* | Stout Battlehammer · 2 · 0 |
| `790` | *bulk* | Forester's Axe · 2 · 0 |
| `791` | *bulk* | Gnarled Ash Staff · 3 · 0 |
| `816` | *bulk* | Small Hand Blade · 2 · 0 |
| `820` | *bulk* | Slicer Blade · 2 · 0 |
| `821` | *bulk* | Riverpaw Leather Vest · 2 · 0 |
| `826` | *bulk* | Brutish Riverpaw Axe · 2 · 0 |
| `827` | *bulk* | Wicked Blackjack · 2 · 0 |
| `832` | *bulk* | Silver Defias Belt · 2 · 0 |
| `863` | *bulk* | Gloom Reaper · 2 · 0 |
| `865` | *bulk* | Leaden Mace · 2 · 0 |
| `866` | *bulk* | Monk's Staff · 2 · 0 |
| `867` | *bulk* | Gloves of Holy Might · 4 · 0 |
| `868` | *bulk* | Ardent Custodian · 4 · 0 |
| `870` | *bulk* | Fiery War Axe · 4 · 0 |
| `871` | *bulk* | Flurry Axe · 4 · 0 |
| `872` | *bulk* | Rockslicer · 3 · 0 |
| `873` | *bulk* | Staff of Jordan · 4 · 0 |
| `880` | *bulk* | Staff of Horrors · 2 · 0 |
| `885` | *bulk* | Black Metal Axe · 2 · 0 |
| `886` | *bulk* | Black Metal Shortsword · 2 · 0 |
| `888` | *bulk* | Naga Battle Gloves · 3 · 0 |
| … | | *3,988 more* |

---

## Item

0 added · 72 removed · 0 changed · 31,675 → 31,603 rows · keyed on `ID`

### Removed (72)

| ID | Push ID | First fields |
|---|---|---|
| `833` | 112078 (invalidated(3)) | 4 · 0 · 4 |
| `942` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| `1315` | 112078 (invalidated(3)) | 4 · 0 · 7 |
| `1443` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| `1447` | 112078 (invalidated(3)) | 4 · 0 · 1 |
| `1980` | 112078 (invalidated(3)) | 4 · 0 · 1 |
| `2246` | 112078 (invalidated(3)) | 4 · 0 · 1 |
| `5004` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| `5005` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| `5010` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| `7549` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| `7550` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| `7551` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| `13001` | 112078 (invalidated(3)) | 4 · 0 · 5 |
| `13089` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| `13091` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| `14557` | 112078 (invalidated(3)) | 4 · 0 · 4 |
| `14558` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| `17063` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| `17065` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| `17082` | 112078 (invalidated(3)) | 4 · 0 · 2 |
| `17108` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| `17109` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| `17110` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| `17982` | 112078 (invalidated(3)) | 4 · 0 · 3 |
| … | | *47 more* |

---

## BroadcastText

41 added · 0 removed · 0 changed · 12 → 53 rows · keyed on `ID`

### Added (41)

| ID | Push ID | First fields |
|---|---|---|
| `2545` | — | It is not yet your time. I shall aid your journey back to the realm o… · 2545 · 0 |
| `2660` | — | 2660 · 0 |
| `2824` | — | Rest your weary bones for a spell. · 2824 · 1 |
| `5907` | — | 5907 · 1 |
| `7748` | — | 7748 · 0 |
| `8094` | — | 8094 · 0 |
| `8095` | — | 8095 · 0 |
| `8111` | — | 8111 · 0 |
| `8112` | — | 8112 · 0 |
| `8122` | — | 8122 · 0 |
| `8200` | — | 8200 · 0 |
| `8260` | — | Through rigorous retraining I have had to break many students of all … · 8260 · 0 |
| `10031` | — | 10031 · 1 |
| `10032` | — | 10032 · 1 |
| `10753` | — | Where would you like to fly to? · 10753 · 0 |
| `11865` | — | 11865 · 0 |
| `292461` | — | Welcome to Bandarion Keep, $c. · 292461 · 0 |
| `292463` | — | Why do they call me "The Breaker"?\n\nTrust me, you don't want to kno… · 292463 · 0 |
| `292477` | — | 292477 · 0 |
| `296783` | — | 296783 · 0 |
| `299234` | — | Praise to the Banshee Queen, it isn't often I have visitors out here. · 299234 · 0 |
| `299873` | — | Hello, $c. · 299873 · 0 |
| `304010` | — | 304010 · 0 |
| `304793` | 112203 (valid) | Belathaan... I thought we agreed to meet alone? I'd hate to think you… · 304793 · 0 |
| `304794` | 112203 (valid) | 304794 · 0 |
| … | | *16 more* |

---

## LightData

0 added · 0 removed · 16 changed · 5,375 → 5,375 rows · keyed on `ID`

### Changed (16)

**ID 74945** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_055` | `0` | `13533183` |

**ID 74946** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_055` | `0` | `13533183` |

**ID 74947** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_055` | `0` | `13533183` |

**ID 74948** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_055` | `0` | `13533183` |

**ID 74949** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_055` | `0` | `13533183` |

**ID 74950** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_055` | `0` | `13533183` |

**ID 74951** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_055` | `0` | `13533183` |

**ID 74952** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_055` | `0` | `13533183` |

**ID 74953** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_055` | `0` | `13533183` |

**ID 74954** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_055` | `0` | `13533183` |

**ID 74955** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_055` | `0` | `13533183` |

**ID 74956** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_055` | `0` | `13533183` |

**ID 75101** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_051` | `3` | `2.5` |
| `Field_1_60_1_69876_052` | `1.2` | `1.3` |
| `Field_1_60_1_69876_058` | `2` | `1` |

**ID 75102** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_058` | `2` | `1` |

**ID 75103** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_051` | `4` | `2` |
| `Field_1_60_1_69876_058` | `2` | `1` |

**ID 75104** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_058` | `2.5` | `1` |

---

## LightDataGlobalVolumeFog

0 added · 0 removed · 7 changed · 9,617 → 9,617 rows · keyed on `ID`

### Changed (7)

**ID 14498** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_007` | `0.6` | `0.8` |
| `Field_1_60_1_69876_014` | `3` | `5` |

**ID 14501** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_006` | `0.15` | `0.1` |
| `Field_1_60_1_69876_014` | `3` | `5` |

**ID 14504** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_014` | `2` | `4` |

**ID 14528** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_014` | `5` | `3` |

**ID 14529** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_014` | `2` | `3` |

**ID 14530** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_014` | `2` | `3` |

**ID 14531** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_014` | `2` | `3` |

---

## ConversationLine

5 added · 0 removed · 1 changed · 553 → 558 rows · keyed on `ID`

### Added (5)

| ID | Push ID | First fields |
|---|---|---|
| `101020` | 112203 (valid) | 304793 · 0 · 0 |
| `101021` | 112203 (valid) | 304794 · 0 · 0 |
| `101022` | 112203 (valid) | 304795 · 0 · 0 |
| `101023` | 112203 (valid) | 304796 · 0 · 0 |
| `101024` | 112203 (valid) | 304797 · 0 · 0 |

### Changed (1)

**ID 89591** — push 112203 (valid)

| Field | Before | After |
|---|---|---|
| `AdditionalDuration` | `0` | `1000` |
| `NextConversationLineID` | `0` | `35484` |

---

## GlobalStrings

0 added · 0 removed · 3 changed · 27,262 → 27,262 rows · keyed on `ID`

### Changed (3)

**ID 60077** — push 112128 (valid)

| Field | Before | After |
|---|---|---|
| `TagText_lang` | `Transfer Now` | `Refresh Now` |

**ID 60078** — push 112128 (valid)

| Field | Before | After |
|---|---|---|
| `TagText_lang` | `Your character will be transferred to another shard in %s %s.` | `The world around you will refresh in %s %s. Make sure you are out of …` |

**ID 60175** — push 112128 (valid)

| Field | Before | After |
|---|---|---|
| `TagText_lang` | `Transfer to a new shard now.` | `Refresh the world now.` |

---

## TimeEventData

**Hotfix-only table.** The plain export returned 204 (no rows shipped in the build), so every row below exists only as live hotfix data.

3 added · 0 removed · 0 changed · 0 → 3 rows · keyed on `ID`

### Added (3)

| ID | Push ID | First fields |
|---|---|---|
| `25930` | 112079 (valid) | 1791824400 · 3162 · 5 |
| `25943` | 112079 (valid) | 1792429200 · 3163 · 5 |
| `25956` | 112079 (valid) | 1793034000 · 3164 · 5 |

---

## LoadingScreenTaxiSplines

2 added · 0 removed · 0 changed · 16 → 18 rows · keyed on `ID`

### Added (2)

| ID | Push ID | First fields |
|---|---|---|
| `1563` | 112120 (valid) | 11167 · 0 · 0 |
| `1564` | 112120 (valid) | 11167 · 1 · 0 |

---

## Achievement

0 added · 1 removed · 0 changed · 233 → 232 rows · keyed on `ID`

### Removed (1)

| ID | Push ID | First fields |
|---|---|---|
| `9275` | 112039 (invalidated(2)) | Warlord Zaela kills (Upper Blackrock Spire) · 9275 |

---

## Achievement_Category

0 added · 1 removed · 0 changed · 35 → 34 rows · keyed on `ID`

### Removed (1)

| ID | Push ID | First fields |
|---|---|---|
| `15233` | 112039 (invalidated(2)) | 15233 · 14807 · 2 |

---

## AreaTriggerActionSet

1 added · 0 removed · 0 changed · 241 → 242 rows · keyed on `ID`

### Added (1)

| ID | Push ID | First fields |
|---|---|---|
| `45793` | 112184 (valid) | 8 |

---

## Faction

0 added · 0 removed · 1 changed · 253 → 253 rows · keyed on `ID`

### Changed (1)

**ID 2758** — push 112201 (valid)

| Field | Before | After |
|---|---|---|
| `ReputationClassMask[1]` | `0` | `-1` |
| `ReputationFlags[0]` | `0` | `17` |
| `ReputationFlags[1]` | `0` | `16` |
| `ReputationMax[1]` | `0` | `42999` |
| `ReputationRaceMasks1[0]` | `0` | `255` |

---

## GossipNPCOption

1 added · 0 removed · 0 changed · 1,539 → 1,540 rows · keyed on `ID`

### Added (1)

| ID | Push ID | First fields |
|---|---|---|
| `62099` | 112142 (valid) | 3 · 0 · 0 |

---

## Light

0 added · 0 removed · 1 changed · 625 → 625 rows · keyed on `ID`

### Changed (1)

**ID 269** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `LightParamsID[2]` | `453` | `7641` |

---

## LightParams

0 added · 0 removed · 1 changed · 827 → 827 rows · keyed on `ID`

### Changed (1)

**ID 7742** — push 112132 (valid)

| Field | Before | After |
|---|---|---|
| `Field_1_60_1_69876_033` | `0.25` | `1` |

---

## SpellMisc

0 added · 0 removed · 1 changed · 31,748 → 31,748 rows · keyed on `ID`

### Changed (1)

**ID 819975** — push 112209 (valid)

| Field | Before | After |
|---|---|---|
| `Attributes[0]` | `159383808` | `696254720` |
| `Attributes[1]` | `32` | `1056` |
| `Attributes[2]` | `16389` | `272646149` |
| `Attributes[3]` | `1048576` | `269681152` |
| `Attributes[4]` | `128` | `8388736` |

---

## SpellTargetRestrictions

0 added · 0 removed · 1 changed · 4,447 → 4,447 rows · keyed on `ID`

### Changed (1)

**ID 347326** — push 112209 (valid)

| Field | Before | After |
|---|---|---|
| `ConeDegrees` | `60` | `0` |

---

