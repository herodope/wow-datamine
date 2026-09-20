#!/usr/bin/env python3
"""Run SQL against out/<build>/wow.db and print the result readably.

A thin wrapper, deliberately. It opens the database **read-only**, picks the
newest extracted build when none is named, and formats the result; it does not
try to be an ORM or to know anything about WoW. Everything it knows about the
schema is in `.claude/skills/wow-query/SKILL.md`.

Read-only is a URI-mode connection, not a convention: `wow.db` is rebuildable
from the CSVs in ~20s, but a stray UPDATE in an ad-hoc query would produce a
database that disagrees with the CSVs and would not announce it.

Usage:
    python scripts/query.py "SELECT ID, Name_lang FROM SpellName LIMIT 5"
    python scripts/query.py -f query.sql --build 1.60.1.69913
    python scripts/query.py --tables spell
    python scripts/query.py --schema SpellEffect
    echo "SELECT 1" | python scripts/query.py
"""

import argparse
import csv
import json
import sqlite3
import sys

import config

DEFAULT_LIMIT = 200
CELL = 60


def log(msg=""):
    print(msg, file=sys.stderr, flush=True)


def newest_build():
    candidates = []
    if config.OUT_DIR.is_dir():
        for d in config.OUT_DIR.iterdir():
            parts = d.name.rsplit(".", 1)
            if (d.is_dir() and len(parts) == 2 and parts[1].isdigit()
                    and config.is_forever_build(d.name, int(parts[1]))
                    and (d / "wow.db").exists()):
                candidates.append((int(parts[1]), d.name))
    return max(candidates)[1] if candidates else None


def connect(build):
    path = config.build_out_dir(build) / "wow.db"
    if not path.exists():
        log(f"no {path} -- run scripts/build_db.py --build {build}")
        raise SystemExit(2)
    uri = "file:" + str(path).replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def fmt(v):
    if v is None:
        return "NULL"
    s = str(v).replace("\n", "\\n").replace("\r", "")
    return s if len(s) <= CELL else s[: CELL - 1] + "\u2026"


def print_table(cols, rows, out=sys.stdout):
    if not cols:
        return
    cells = [[fmt(v) for v in r] for r in rows]
    widths = [len(c) for c in cols]
    for r in cells:
        for i, v in enumerate(r):
            if i < len(widths):
                widths[i] = max(widths[i], len(v))
    line = "  ".join(c.ljust(widths[i]) for i, c in enumerate(cols))
    print(line, file=out)
    print("  ".join("-" * w for w in widths), file=out)
    for r in cells:
        print("  ".join(v.ljust(widths[i]) for i, v in enumerate(r)), file=out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sql", nargs="?", help="SQL; omit to read stdin")
    ap.add_argument("-f", "--file", help="read SQL from a file")
    ap.add_argument("--build", help="defaults to the newest build with a wow.db")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help=f"max rows printed (default {DEFAULT_LIMIT}; 0 = all)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--tables", nargs="?", const="", metavar="SUBSTR",
                    help="list tables, optionally filtered")
    ap.add_argument("--schema", metavar="TABLE", help="show a table's columns")
    args = ap.parse_args(argv)

    build = args.build or newest_build()
    if not build:
        log("no build with a wow.db -- run scripts/build_db.py")
        return 2
    conn = connect(build)

    if args.tables is not None:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE ? ORDER BY name", (f"%{args.tables}%",)).fetchall()
        for (n,) in rows:
            cnt = conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
            print(f"{n:<48} {cnt:>10,}")
        log(f"\n  {len(rows)} table(s) in {build}")
        return 0

    if args.schema:
        info = conn.execute(f'PRAGMA table_info("{args.schema}")').fetchall()
        if not info:
            log(f"no such table: {args.schema}")
            return 1
        idx = conn.execute(f'PRAGMA index_list("{args.schema}")').fetchall()
        indexed = set()
        for i in idx:
            for r in conn.execute(f'PRAGMA index_info("{i[1]}")'):
                indexed.add(r[2])
        print_table(["column", "type", "indexed"],
                    [(c[1], c[2], "yes" if c[1] in indexed else "") for c in info])
        log(f"\n  {len(info)} column(s), "
            f"{conn.execute(f'SELECT COUNT(*) FROM \"{args.schema}\"').fetchone()[0]:,} rows")
        return 0

    sql = args.sql
    if args.file:
        sql = open(args.file, encoding="utf-8").read()
    if not sql:
        sql = sys.stdin.read()
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        log("no SQL given")
        return 2

    try:
        cur = conn.execute(sql)
    except sqlite3.Error as exc:
        # Read-only is enforced by the connection, so a write attempt lands
        # here rather than silently succeeding.
        log(f"SQL error: {exc}")
        return 1

    cols = [d[0] for d in (cur.description or [])]
    rows = cur.fetchmany(args.limit) if args.limit else cur.fetchall()
    more = bool(args.limit) and len(rows) == args.limit and cur.fetchone() is not None

    if args.json:
        json.dump([dict(zip(cols, r)) for r in rows], sys.stdout,
                  indent=2, ensure_ascii=False, default=str)
        print()
    elif args.csv:
        w = csv.writer(sys.stdout, lineterminator="\n")
        w.writerow(cols)
        w.writerows(rows)
    else:
        print_table(cols, rows)

    log(f"\n  {len(rows):,} row(s){' (truncated; raise --limit)' if more else ''} "
        f"from {build}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
