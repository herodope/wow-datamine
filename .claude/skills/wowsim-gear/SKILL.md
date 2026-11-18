---
name: wowsim-gear
description: Run WoW Forever (wowsims/forever) sims locally - gear BiS optimization, stat weights, item comparisons, AoE weights. Use for any "what's BiS / which is better / stat weights" question about WoW Forever gear.
---

# wowsim-gear

Local wowsims/forever sims through the `wowsim` MCP server (tools
`mcp__wowsim__*`, or `mcp__remote-devices__wowsim__*` from Claude Desktop) or
the same functions from the shell: `python scripts/wowsim.py <command>`.
Reference: `docs/wowsim.md`.

## Workflow

1. **Findings first.** `findings_list`, then `findings_get` on a match. If the
   finding is not stale (same sim sha, sim item db and rules profile hash), answer
   from it and say it is **saved data from <date>**, not a fresh sim. If it is
   stale, re-sim before quoting a number.
2. **Fast tools next.** One item vs another: `compare_items`. What a stat is
   worth: `stat_weights`. A set's numbers: `sim_gear`. Each takes seconds to a
   minute.
3. **`optimize` only for a full set.** It returns a job id; poll `job_status`
   (cheap) until done, then `job_result`. `quick` takes a few minutes;
   `standard` and `thorough` take longer. Offer `findings_save` for a result
   worth keeping, and `export_gear_siml` when the user wants to import it.
4. **Always state** the budget or iterations, and the noise (`±`) of every
   number you report. A difference inside its `±` is a tie: say so instead of
   ranking.

Gear arguments take `.gear.siml` text, a findings set id
(`2026-11-18_prot_bis/tps_t05`), an optimize job id, or `preset`.

## Metrics

- **TPS / DPS**: threat and damage per second, higher is better.
- **DTPS** counts **melee damage taken only**. Lower is better; ties are
  broken by TMI.
- **Stamina's DTPS value is 0 by definition**: health does not reduce damage
  taken. Use TMI to value Stamina.
- **TMI** is health-normalised spike damage. Lower is better; ties are broken
  by DTPS.
- `dtps_min_hp:<N>` minimises DTPS with a penalty of 0.1 DTPS per HP below N:
  the middle ground between a DTPS set and a TMI set.
- **The preset boss deals no magic damage.** Use `tschool` (e.g. `shadow`) to
  model magic melee; nothing else in the preset does.

## Rules profiles

The rules decide which items the optimizer may propose (`config/rules/*.json`).
Ask which profile applies, or default to the last one used (tools default to
it) and name it in the answer. When presenting an optimize result, list the
items the rules excluded that would have ranked higher (`job_result` shows
them), so the user can see what the rules cost.

## Presenting

- A compact slot table (slot, item, enchant, source) and one metrics row. No
  raw stat arrays.
- Name the race, the encounter, the rules profile and the sim sha the tools
  report in their header line.
- Hand out `.gear.siml` exports when asked (`export_gear_siml`). Line 2 is a
  `//` comment with provenance; the site and CLI accept it.

## Gotchas

1. **GearStats includes base stats and imbues.** The pool subtracts the
   empty-gear baseline. Every main hand carries the preset's weapon oil
   (+36 SP), the same for each, so it does not change a main-hand ranking.
2. **Enchant ids are effectIds, and one effectId can serve several slots**
   (929 is the bracer, boot and shield Stamina enchant). Name enchants by slot;
   the tools do.
3. **`factionRestriction` is often unset** in the sim's db. Enemy-faction PvP
   and rep items are filtered by name, vendor faction and quest faction.
4. **Class quest rewards are not class-restricted** in the db (Voodoo Feathers:
   Fury Visor, Razorsteel Shoulders). Profiles carry a quest-id blocklist.
5. **The sim does not enforce `limitCategory`**: it equips two Warden's
   Signets, or Adaptive Combat Assistant twice (not unique, but limit category
   707). The optimizer and `compare_items` enforce it and unique items across
   ring and trinket pairs.
6. **Some vendors list a Horde and an Alliance NPC** (Soulforge: Mokvar /
   Deliana). Sources show the profile faction's vendor.
7. **Comparisons use common random numbers** (one seed per comparison). At
   4000 iterations the paired noise is about ±0.5 DPS/TPS/DTPS.
8. **Objectives and toggles are structured arguments**, never matched by
   substring (`'t05' in 'Tmi_not05'` is True).
9. **Soulforge items have several ilvl variants** (`soulforge_gauntlets_ilvl55`
   vs `_ilvl65`). Forced-set seeds take the highest ilvl of a name.
10. **Weights are local** to the gear they were measured on: hit reads 0 when
    capped. Always say which set the weights came from.
11. **AoE weights use the preset APL**, which is mostly single-target. Holy
    Shield and Consecration scaling come out right; an AoE-specific APL could
    shift the weights.
12. **The siml printer's slugs** are what the site and CLI accept.
13. **The sim seeds iteration i with `RandomSeed + i`.** Seeds 77 and 78 share
    all but one iteration, so they are not independent samples. A second seed
    must sit more than the iteration count away; the tools use a stride of
    10,000,019.
