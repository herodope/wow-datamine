# Repo layout

The full repository and output layout, the `wow.db` query surface, the MCP
server and the `manifest.json` resolution values.

```
.
├── CLAUDE.md                # short agent context; points into docs/
├── .claude/skills/wow-query/SKILL.md   # how to query wow.db, and the rules
├── .claude/notes/state-log.md          # LOCAL ONLY — build log + findings record
├── .claude/skills/patch-day/SKILL.md   # LOCAL ONLY — runs docs/patch-day.md
├── docs/
│   ├── wtl-api.md           # WTL routes and their silent-failure gotchas
│   ├── conventions.md       # working rules in full, with measurements
│   ├── patch-day.md         # patch-day checklist, hotfix-only day
│   ├── key-facts.md         # FDIDs, layouts, hotfixes, encryption, GameTables
│   ├── baseline-metrics.md  # per-build metrics, known benign errors
│   ├── retail-contamination.md
│   ├── sod-tags.md          # SoD tags and player reachability
│   └── repo-layout.md       # this file
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
│   ├── extract_gametables.py# GameTables/*.txt -- NOT DB2s, see key-facts.md
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
│   ├── check_findings.py    # re-test the local findings record against a build
│   └── diff_builds.py       # compare two build dirs, emit markdown
├── out/                     # GITIGNORED — extracted data
│   └── <version>.<build>/
│       ├── db2/*.csv           # plain DB2s, as shipped in the build
│       ├── db2_hotfixed/*.csv  # same tables with the hotfix overlay applied
│       ├── hotfixes.csv        # push IDs + changed rows from Cache/ADB/enUS
│       ├── files.csv           # fdid, path, size, encrypted, content_type
│       ├── gametables/*.txt    # tab-separated, NOT DB2s — see key-facts.md
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

## `wow.db`

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
[sod-tags.md](sod-tags.md)), then `spell_reach` and `spell_reach_coverage`
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
> deliberate.** A common global gitignore excludes `**/.claude/` as
> *per-project Claude Code state* — settings, caches, session files — which is
> the right default and should stay.
>
> This file is not that. It is shared documentation: the schema's prefixes,
> the join paths, and nine rules each derived from a measurement. Anyone
> cloning the repo needs it to query `wow.db` without repeating known
> mistakes — resolving FKs by value and picking up numeric collisions, missing
> array-suffixed FK columns, asserting a meaning for an unverified column,
> decoding a context-gated enum without its gate.
>
> Committed with `git add -f`. If more files ever land under `.claude/` here,
> add them the same way and only when they are documentation rather than
> state — do not relax the global rule to cover them. `.claude/notes/` is
> state and stays untracked.

## MCP server

`scripts/mcp_server.py` exposes the same database over MCP stdio, for clients
that cannot run `query.py` themselves.

Four tools: `list_tables`, `describe_table`, `query`, `get_conventions`.
Install with `pip install -r requirements.txt` — see **Prerequisites** in
[`CLAUDE.md`](../CLAUDE.md#prerequisites).

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

## `manifest.json` resolution values

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
