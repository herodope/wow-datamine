#!/usr/bin/env python3
"""Load a build's extracted CSVs into one SQLite file -> out/<build>/wow.db

The CSVs are the source of truth and stay that way; this is a query surface
over them. Grepping 610 files answers "where does this ID appear" badly, and
the questions this project actually asks -- which rows a hotfix touched, what
a foreign key resolves to, whether a value moved between variants -- are joins.

Three sets of tables land in one database:

    <Table>          from db2_hotfixed/  -- the live view, shipped + hotfixes
    plain_<Table>    from db2/           -- as shipped in the client
    gt_<Name>        from gametables/    -- tab-separated, not DB2s

So the comparison this repo is built around is one query:

    SELECT h.ID, p.Display_lang AS shipped, h.Display_lang AS live
    FROM ItemSparse h LEFT JOIN plain_ItemSparse p USING (ID)
    WHERE p.ID IS NULL;          -- rows that exist only as live hotfix data

The `gt_` prefix is not decoration. `SpellScaling` exists both as a DB2 (which
ships 204-empty in this build) and as `SpellScaling.txt` in the GameTables,
and they are different things; an unprefixed load would collide the moment the
DB2 stopped being empty.

Column names are sanitised: `Corpse[0]` becomes `Corpse_0`, because brackets
are alternative identifier quoting in SQLite and a column you have to escape
carefully is a column nobody will query. The original header order is
preserved, and `_columns` records the mapping.

Types are sniffed from a sample and declared as affinities, so `WHERE ID =
6343` works rather than silently matching nothing against TEXT '6343'. A
mis-sniff degrades safely: SQLite stores a value that will not convert as-is.

Idempotent. Builds into a temporary file and atomically replaces the target,
so an interrupted run never leaves a half-loaded database behind, and a
re-run produces the same result rather than appending to it.

Usage:
    python scripts/build_db.py
    python scripts/build_db.py --build 1.60.1.69913
    python scripts/build_db.py --vacuum          # slower, smaller file
"""

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

import config

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

SAMPLE_ROWS = 4000        # rows sniffed per column to pick an affinity
BATCH = 5000

_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d*\.\d+(?:[eE][-+]?\d+)?$|^-?\d+(?:[eE][-+]?\d+)$")
# Trailing _<digits> comes from an array column (LightParamsID[3] -> _3); strip
# it before asking whether the column is an ID, or every array FK is missed.
_ARRAY_SUFFIX = re.compile(r"_\d+$")


def log(msg=""):
    print(msg, file=sys.stderr, flush=True)


def sanitize(name, taken):
    """'Corpse[0]' -> 'Corpse_0'. Unique within a table."""
    clean = re.sub(r"\[(\d+)\]", r"_\1", name.strip())
    clean = re.sub(r"[^0-9A-Za-z_]", "_", clean)
    if not clean or clean[0].isdigit():
        clean = "c_" + clean
    base, n = clean, 2
    while clean.lower() in taken:
        clean, n = f"{base}_{n}", n + 1
    taken.add(clean.lower())
    return clean


def is_id_column(clean):
    """ID, or anything ending in ID once an array suffix is stripped."""
    base = _ARRAY_SUFFIX.sub("", clean)
    return base.upper() == "ID" or base.upper().endswith("ID")


def affinity(values):
    """INTEGER / REAL / TEXT from a sample. Empty strings do not vote."""
    seen = [v for v in values if v not in ("", None)]
    if not seen:
        return "TEXT"
    if all(_INT_RE.match(v) for v in seen):
        return "INTEGER"
    if all(_INT_RE.match(v) or _FLOAT_RE.match(v) for v in seen):
        return "REAL"
    return "TEXT"


def read_delimited(path, delimiter):
    """(header, row iterator). Returns (None, []) for an unreadable file."""
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace", newline="")
    except OSError:
        return None, iter(())
    reader = csv.reader(fh, delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        fh.close()
        return None, iter(())

    def rows():
        try:
            for r in reader:
                if r:
                    yield r
        finally:
            fh.close()

    return [h.strip() for h in header], rows()


def load_one(conn, table, path, delimiter=","):
    """Create and fill one table. Returns (rows, columns) or (0, 0) if empty."""
    header, _probe = read_delimited(path, delimiter)
    if not header:
        return 0, 0

    taken = set()
    cols = [sanitize(h, taken) for h in header]

    # Sniff affinities from the head of the file, then re-open to load. Two
    # passes over a small prefix beats holding 1.9M rows in memory.
    _h, rows = read_delimited(path, delimiter)
    sample = []
    for i, r in enumerate(rows):
        sample.append(r)
        if i + 1 >= SAMPLE_ROWS:
            break
    if not sample:
        return 0, 0
    affs = [affinity([r[i] for r in sample if i < len(r)]) for i in range(len(cols))]

    ddl = ", ".join(f'"{c}" {a}' for c, a in zip(cols, affs))
    conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    conn.execute(f'CREATE TABLE "{table}" ({ddl})')

    placeholders = ",".join("?" * len(cols))
    insert = f'INSERT INTO "{table}" VALUES ({placeholders})'
    width = len(cols)

    _h, rows = read_delimited(path, delimiter)
    total, batch = 0, []
    for r in rows:
        # Ragged rows are padded or clipped rather than dropped: a short row is
        # still a row, and losing it silently would be the worse failure.
        if len(r) < width:
            r = r + [""] * (width - len(r))
        elif len(r) > width:
            r = r[:width]
        batch.append(r)
        if len(batch) >= BATCH:
            conn.executemany(insert, batch)
            total += len(batch)
            batch = []
    if batch:
        conn.executemany(insert, batch)
        total += len(batch)

    return total, len(cols)


def index_table(conn, table, prefix_note=""):
    """Index ID and every *ID column. Returns the number created."""
    cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
    made = 0
    for c in cols:
        if not is_id_column(c):
            continue
        name = f"ix_{table}_{c}"[:120]
        try:
            conn.execute(f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}" ("{c}")')
            made += 1
        except sqlite3.OperationalError:
            pass
    return made


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", help="version string; defaults to the newest extracted")
    ap.add_argument("--vacuum", action="store_true",
                    help="VACUUM at the end -- slower, smaller file")
    ap.add_argument("--no-gametables", action="store_true")
    args = ap.parse_args(argv)

    started = time.monotonic()

    build = args.build
    if not build:
        candidates = []
        if config.OUT_DIR.is_dir():
            for d in config.OUT_DIR.iterdir():
                parts = d.name.rsplit(".", 1)
                if d.is_dir() and len(parts) == 2 and parts[1].isdigit() \
                        and config.is_forever_build(d.name, int(parts[1])):
                    candidates.append((int(parts[1]), d.name))
        if not candidates:
            log(f"no extracted Forever build under {config.OUT_DIR}")
            return 2
        build = max(candidates)[1]

    out_dir = config.build_out_dir(build)
    if not (out_dir / "db2_hotfixed").is_dir():
        log(f"no {out_dir / 'db2_hotfixed'} -- run extract_db2.py first")
        return 2

    target = out_dir / "wow.db"
    tmp = out_dir / "wow.db.tmp"
    if tmp.exists():
        tmp.unlink()

    log(f"build {build}")
    conn = sqlite3.connect(tmp)
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA temp_store = MEMORY")

    loaded, skipped, indexes = {}, [], 0

    sources = [("db2_hotfixed", "", ","), ("db2", "plain_", ",")]
    if not args.no_gametables and (out_dir / "gametables").is_dir():
        sources.append(("gametables", "gt_", "\t"))

    for sub, prefix, delim in sources:
        d = out_dir / sub
        if not d.is_dir():
            log(f"  {sub}/ absent, skipped")
            continue
        pattern = "*.txt" if sub == "gametables" else "*.csv"
        files = sorted(d.glob(pattern))
        log(f"  {sub}/  {len(files)} file(s) -> {prefix or '(no prefix)'}")
        n_tables = 0
        with conn:
            for f in files:
                table = prefix + f.stem
                rows, ncols = load_one(conn, table, f, delim)
                if rows == 0:
                    # A CSV with a header and no data rows. 204-empty tables
                    # produce no file at all, so this is the rarer case of a
                    # table that exists but shipped nothing.
                    conn.execute(f'DROP TABLE IF EXISTS "{table}"')
                    skipped.append(table)
                    continue
                loaded[table] = {"rows": rows, "columns": ncols, "source": sub}
                n_tables += 1
        for table in list(loaded):
            if loaded[table]["source"] == sub:
                indexes += index_table(conn, table)
        log(f"    {n_tables} table(s) loaded, "
            f"{sum(v['rows'] for v in loaded.values() if v['source'] == sub):,} rows")

    # A database that cannot say what it is gets mistaken for another build.
    with conn:
        conn.execute("DROP TABLE IF EXISTS _build_info")
        conn.execute("CREATE TABLE _build_info (key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany(
            "INSERT INTO _build_info VALUES (?,?)",
            [("build", build),
             ("generatedAt", datetime.now(timezone.utc).replace(microsecond=0).isoformat()),
             ("tables", str(len(loaded))),
             ("skipped_empty", str(len(skipped))),
             ("note", "unprefixed = db2_hotfixed (live); plain_ = db2 (as shipped); "
                      "gt_ = GameTables (tab-separated, not DB2s)")])

    if args.vacuum:
        log("  vacuuming")
        conn.execute("VACUUM")

    conn.commit()
    conn.close()
    os.replace(tmp, target)

    total_rows = sum(v["rows"] for v in loaded.values())
    section = {
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "durationSeconds": round(time.monotonic() - started, 1),
        "path": target.name,
        "bytes": target.stat().st_size,
        "tables": len(loaded),
        "rows": total_rows,
        "indexes": indexes,
        "skipped_empty": sorted(skipped),
        "by_source": {
            s: {"tables": sum(1 for v in loaded.values() if v["source"] == s),
                "rows": sum(v["rows"] for v in loaded.values() if v["source"] == s)}
            for s in {v["source"] for v in loaded.values()}
        },
        "row_counts": {k: v["rows"] for k, v in sorted(loaded.items())},
    }
    mpath = out_dir / "manifest.json"
    if mpath.exists():
        try:
            m = json.loads(mpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            m = {}
        m["database"] = section
        mpath.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")

    log("")
    log(f"  {len(loaded):,} table(s), {total_rows:,} row(s), {indexes:,} index(es)")
    if skipped:
        log(f"  {len(skipped)} skipped as empty: {', '.join(skipped[:8])}"
            + (" …" if len(skipped) > 8 else ""))
    log(f"  {section['bytes']:,} bytes in {section['durationSeconds']}s")
    log(f"  -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
