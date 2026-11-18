# wowsim: local gear sims

Gear sims, stat weights, item comparisons and gear optimization for WoW
Forever, run locally on [wowsims/forever](https://github.com/wowsims/forever).
Exposed as a second MCP server, `wowsim`, beside `wow-datamine`, and as a CLI
(`scripts/wowsim.py`) that gives the same answers.

The sim is separate from the datamine. It reads its own item database
(`vendor/forever/assets/database/db.json`), not `wow.db`, and is versioned by
the wowsims commit, not by the WoW build. The two MCP servers share no process:
a long or stuck sim cannot slow or crash `wow-datamine`, which stays read-only.

The skill [`.claude/skills/wowsim-gear/SKILL.md`](../.claude/skills/wowsim-gear/SKILL.md)
carries the workflow, the metric definitions and the gotchas for an agent.

## Layout

```
bisrun/                     Go batch harness, its own module (committed)
config/specs.json           per-spec pool filters, enchant options, seed sets
config/presets/<spec>.json  base RaidSimRequest per spec (protojson)
config/rules/<name>.json    exclusion-rule profiles
findings/<date>_<topic>/    saved results: meta, result, summary.md, gear/*.gear.siml
scripts/build_sim.py        toolchains, checkout, protos, build
scripts/wowsim_tools/       the Python package (harness, pool, rules, optimizer, ...)
scripts/wowsim_mcp_server.py   the MCP server
scripts/wowsim.py           the CLI
scripts/wowsim_job.py       background job runner (spawned detached)
scripts/test_wowsim_tools.py   unit tests
scripts/wowsim_accept.py    acceptance tests
bin/                        GITIGNORED: bisrun, wowsimcli, build_info.json
sim_out/                    GITIGNORED: pool cache, jobs/<id>/
vendor/forever/             GITIGNORED: the wowsims checkout, at config.SIM_PIN
vendor/toolchains/          GITIGNORED: Go, protoc, protoc-gen-go, GOPATH, GOCACHE
```

## Build

```powershell
python scripts/build_sim.py --setup-toolchains   # first time
python scripts/build_sim.py                      # after moving the pin
python scripts/build_sim.py --check              # state only
```

`--setup-toolchains` downloads Go, protoc and protoc-gen-go into
`vendor/toolchains/` and refuses any zip whose sha256 differs from the hash
pinned in `config.py` (Go 1.27.3, protoc 36.2 as the sim's CI pins it,
protoc-gen-go matching the sim's `google.golang.org/protobuf`). Every Go
command runs with `GOPATH`, `GOCACHE` and `GOENV` under that folder,
`GOTOOLCHAIN=local` and `CGO_ENABLED=0`, so nothing outside `vendor/` and
`bin/` is written. The zips are Windows builds; on another OS install the same
versions onto `PATH` and skip the flag.

The build:

1. Clones `vendor/forever` if missing and checks out `config.SIM_PIN`. It
   refuses a checkout with modified tracked files: upstream is never patched.
2. Checks Go against the `go` line of the sim's `go.mod`.
3. Generates `sim/core/proto/*.pb.go` with protoc (upstream does not track
   them).
4. Builds `bin/wowsimcli` and `bin/bisrun` with `-tags=with_db`.
5. Writes `bin/build_info.json` (sim sha, Go version, sha256 of `db.json` and
   `db.bin`). When the sim sha or the db changes, the pool cache is deleted.

To follow upstream, change `SIM_PIN` and rebuild. Findings simmed on the old
sha then read as stale.

## The harness (`bisrun`)

```
bisrun -base config/presets/prot_paladin.json [-workers N] < jobs.jsonl
bisrun preset vendor/forever/ui/specs/paladin/protection/defaults.siml
```

Batch mode reads one JSON job per line and writes one result per line, in
completion order (match by `Name`). Workers default to the CPU count. Each job
clones the base request, applies its overrides, calls `core.ComputeStats` and,
unless `statsOnly`, `core.RunRaidSim` (`core.RunRaidSimConcurrent` with
`concurrent`).

| Job field | Meaning |
|---|---|
| `name` | result key |
| `base` | RaidSimRequest JSON path; default the `-base` file |
| `gear` | `.gear.siml` text, parsed by the sim's own `siml` package |
| `items` | `[{slot, id, enchant, suffix}]` per-slot overrides, applied after `gear` |
| `full` | start from empty gear |
| `bonus` | `{"<stat index>": amount}` added to bonus stats |
| `race`, `talents` | proto Race name; talent string |
| `consumables` | ConsumesSpec protojson; replaces the base's |
| `targets`, `tlevel`, `tmindmg`, `thealth`, `duration` | encounter overrides on the first target, which `targets` copies |
| `tschool` | proto SpellSchool name for every target's melee (magic melee) |
| `iterations`, `seed` | sim options |
| `statsOnly` | ComputeStats only |
| `concurrent` | split iterations over every core |
| `printGear` | return the gear as `.gear.siml` text (`Siml`) |

Result: `Name, Dps, Tps, Dtps, Tmi, Cod, Hps, DpsSd, TpsSd, DtpsSd, TmiSd, Err,
Final[], Gear[], Sets[], Items[], Siml`. `Final` and `Gear` are indexed by
proto `Stat`.

`preset` turns a full `.siml` setup into the RaidSimRequest JSON the site's
individual sim sends, without the potions and conjured items the site adds for
its APL. For the prot preset its output equals `config/presets/prot_paladin.json`
except for the random seed. A new spec needs that JSON plus an entry in
`config/specs.json`.

## Tools

| MCP tool | CLI | Read-only |
|---|---|---|
| `sim_status` | `wowsim.py status` | yes |
| `sim_gear` | `wowsim.py sim --gear …` | yes |
| `stat_weights` | `wowsim.py weights --gear … [--targets 1,3,5,7]` | yes |
| `compare_items` | `wowsim.py compare --gear … --slot … (--items … \| --top-n N)` | yes |
| — | `wowsim.py close-calls --gear …` | — |
| `optimize` | `wowsim.py optimize --objective … --t05 on\|off [--wait]` | **no** |
| `job_status`, `job_result` | `wowsim.py job [id]`, `wowsim.py result <id>` | yes |
| `export_gear_siml` | `wowsim.py export <job id \| finding/set>` | yes |
| `findings_list`, `findings_get` | `wowsim.py findings [id[/set]]` | yes |
| `findings_save` | `wowsim.py save <job id> --topic …` | **no** |

Gear arguments take `.gear.siml` text, a findings set id
(`2026-11-18_prot_bis/tps_t05`), an optimize job id, a `.siml` path (CLI), or
`preset`. Item arguments take names or ids from the sim's database. Outputs
are short markdown tables; every one names the sim sha, rules profile, race,
iterations and noise.

- `sim_gear` reports each metric with the standard error of its mean.
- `stat_weights` and `compare_items` run two paired seeds and report the
  mean, with ± as half the spread of the two seeds.
- `optimize` spawns `scripts/wowsim_job.py` detached and returns a job id. The
  job writes `sim_out/jobs/<id>/{args.json, state.json, log.txt, result.json}`
  and keeps running if the server stops. On Windows an MCP client can run the
  server inside a job object that kills its whole tree on exit and forbids
  breakaway (the Python `mcp` client does). The runner is then started through
  WMI (`Win32_Process.Create`), which puts it outside that job;
  `state.json` records which way it was started (`spawned_by`). A runner that
  dies without writing its state reads as `died` in `job_status`.

### Registering the server

Claude Code, at user scope (never in a committed `.mcp.json`):

```
claude mcp add --scope user wowsim -- python <repo>/scripts/wowsim_mcp_server.py
```

Claude Desktop, in `claude_desktop_config.json` beside `wow-datamine`:

```json
"wowsim": {
  "command": "python",
  "args": ["C:\\path\\to\\wow-datamine\\scripts\\wowsim_mcp_server.py"]
}
```

Desktop reads that file at startup only: quit it fully and restart after an
edit. From a linked computer the tools appear as
`mcp__remote-devices__wowsim__*`.

## Optimizer

1. Stat weights at the starting gear, for EP.
2. Seeds: EP-greedy, or the given start set with disallowed slots refilled;
   plus one seed per configured forced set (the PvP 6pc sets the rules allow;
   Soulforge 8pc with T0.5 on, tank pieces for `dtps`/`tmi` and str/SP pieces
   for `tps`/`dps`; Lightforge with T0.5 off). Over a start set, forced pieces
   replace its slots, so the seed can reach a set bonus a one-slot climb
   cannot.
3. With T0.5 on and no `seed_from`, the newest non-stale finding with the
   same spec, rules and objective and T0.5 off is the start set.
4. Screen: every allowed trinket, main hand, shield and libram simmed in the
   first seed. Procs, on-use effects and weapon damage do not show up in EP.
5. Climb: per seed, in parallel, slot by slot in a shuffled order. Every
   candidate swap is simmed with one seed, and the best one that beats the
   current score by 0.05% is kept. Armor slots try the top EP candidates;
   screened slots try the top screen results; configured set pieces are always
   tried.
6. Enchant pass on the best seed: all slots' options simmed in one batch per
   round, two rounds. With `standard` and `thorough`, one more climb pass
   follows with the chosen enchants.
7. Final sim at the budget's final iterations.

| Budget | Climb iters | Passes | Armor cands | Screen top | Screen iters | Enchant iters | Final iters |
|---|---|---|---|---|---|---|---|
| quick | 1000 | 2 | 4 | 5 | 1000 | 2000 | 10000 |
| standard | 1500 | 3 | 6 | 7 | 1500 | 4000 | 20000 |
| thorough | 2500 | 4 | 8 | 10 | 2500 | 4000 | 20000 |

Measured on a 12-thread desktop: `quick` for TPS with T0.5 on under prot_v3
takes about 235 s and reaches 1627 TPS (Soulforge 6pc). The screen, about 325
sims at screen iterations, is the largest fixed cost. `standard` and
`thorough` have not been timed.

Objectives: `tps`, `dps`, `dtps` (ties broken by TMI), `tmi` (ties broken by
DTPS), `dtps_min_hp:<N>` (DTPS plus 0.1 per HP below N).

The optimizer enforces what the sim does not: `limitCategory` (707 Pix
Xizzix trinkets, 710 Watchers signets) and unique items across the ring and
trinket pairs. `job_result` lists items the rules excluded that rank above the
chosen item by EP, with the rule that excluded each.

## Rules profiles

`config/rules/<name>.json`, documented key by key in
`scripts/wowsim_tools/rules.py`. A profile is hashed as canonical JSON; every
result and finding records the hash. `prot_v3` is the Horde Undead prot
paladin profile: no raid drops or raid rep, no Alliance items, no premier
PvP epics, no epic BG rep, no Darkspear epics, no world BoE epics, no epic
crafts, no random suffixes, no other classes' Sunken Temple quest rewards, and
neither Band of the Better Half nor Elemental Focus Band.
Profiles are JSON, not YAML, so the pipeline stays stdlib-only.

## Findings

Saved results are previous data, never ground truth. A finding is **stale**
when the sim sha, the sim's `db.json` hash or its rules profile hash differs
from the current one. Re-sim a stale finding before quoting it.

A finding keeps a snapshot of its rules profile (`rules_snapshot` in
`meta.json`). After a profile edit that only adds exclusions,
`python scripts/wowsim.py rebase-rules <finding>` re-stamps it with the new
hash, and refuses unless both hold: every pool item the new profile allows,
the old one allowed too; and no saved set holds a newly excluded item. A set
that was best over the old items and is still allowed is then still best over
the new ones. Any other rules change means a re-sim. For a finding with no
snapshot, pass the old profile with `--old-profile` (e.g. from `git show`); its
hash must match the finding's stamp.

`2026-11-18_prot_bis` holds the prototype run on sim `b83fe9df` under
`prot_v3`: the five sets `dtps`, `tps_t05`, `tps_not05`, `dps_t05`,
`dps_not05`, plus its weights, screens, close calls and AoE weights under
`extra/`. The `dtps` set was optimized under an earlier rule set that allows
more items; every item in it is allowed under `prot_v3`.

## Sim behaviour the tools account for

- **Seeds.** The sim seeds iteration *i* with `RandomSeed + i`. Seeds 77 and
  78 share all but one iteration and give near-identical results, so a second
  independent seed sits `SEED_STRIDE` (10,000,019) away. The optimizer's final
  sim uses a seed clear of every climb seed's iterations.
- **GearStats** includes base stats and imbues; item stat vectors subtract the
  empty-gear baseline. Every main hand carries the preset's weapon oil.
- **Enchant effectIds** can serve several slots under different names; names
  are chosen by slot.
- **`limitCategory` is not enforced by the sim**, including an item paired
  with itself (Adaptive Combat Assistant is not unique but is limit category
  707).
- The preset boss deals no magic damage; `tschool` models magic melee.

## Tests

```
python scripts/test_wowsim_tools.py              # unit tests, seconds
python scripts/wowsim_accept.py --skip-optimize  # build, re-sims, compare
python scripts/wowsim_accept.py --clean-build    # all of it, plus a clean-copy build
```

The acceptance script checks: a clean `build_sim --check` and a preset sim;
each `findings_nov18_2026` set re-simmed at 10k iterations within 1% of its
recorded value; Heart of the Scale vs Heart of Wyrmthalak within ±1 DPS; a
`quick` TPS T0.5 optimize, started through the MCP server, under 5 minutes and
within 1% of 1619 TPS; that the job survives the server exiting; and the
`wow-datamine` query latency idle and during the job. It reads the prototype
files from `wowsim-kit/findings_nov18_2026/`, a local folder the repo ignores
(its own git repository, with no remote); on a machine without it, the
re-sim checks fail.

Claude Desktop through the remote-devices bridge is not covered by the script:
restart Desktop and call `sim_gear` from a chat.

## Limits

- One spec is configured (`prot_paladin`). Others need a preset JSON
  (`bisrun preset`) and a `config/specs.json` entry.
- The climb changes one slot at a time; forced-set seeds are how it reaches
  multi-piece bonuses.
- AoE weights use the preset APL, which is mostly single-target.
- Item names come from the sim's database, not from `wow.db`.
