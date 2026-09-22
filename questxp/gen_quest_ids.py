#!/usr/bin/env python3
"""Regenerate QuestXPSweep/QuestIDs.lua from QuestV2 in out/<build>/wow.db.

The addon needs the full quest-ID list at file-load time, and the only
authoritative list is the client's own QuestV2 table. This writes it as a Lua
array rather than having the addon discover IDs at runtime: the sweep has to
know up front how many requests it will make, and a hand-maintained list would
drift from the build silently.

The build is an argument, never a literal -- see Conventions in CLAUDE.md.
Defaults to the newest extracted build, the same rule scripts/query.py uses.

Usage:
    python questxp/gen_quest_ids.py
    python questxp/gen_quest_ids.py --build 1.60.1.69913
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import config  # noqa: E402

# QuestV2 (unprefixed) is the hotfixed view -- the live set of quests the
# server will answer for. plain_QuestV2 is as-shipped and would miss any quest
# added by a hotfix wave.
SOURCE_TABLE = "QuestV2"

IDS_PER_LINE = 12
OUT_PATH = Path(__file__).resolve().parent / "QuestXPSweep" / "QuestIDs.lua"


def log(msg=""):
    print(msg, file=sys.stderr, flush=True)


def newest_build():
    """Newest extracted Forever build that has a wow.db. Mirrors query.py."""
    candidates = []
    if config.OUT_DIR.is_dir():
        for d in config.OUT_DIR.iterdir():
            parts = d.name.rsplit(".", 1)
            if (d.is_dir() and len(parts) == 2 and parts[1].isdigit()
                    and config.is_forever_build(d.name, int(parts[1]))
                    and (d / "wow.db").exists()):
                candidates.append((int(parts[1]), d.name))
    return max(candidates)[1] if candidates else None


def read_ids(build):
    path = config.build_out_dir(build) / "wow.db"
    if not path.exists():
        log("no %s -- run scripts/build_db.py --build %s" % (path, build))
        raise SystemExit(2)
    uri = "file:" + str(path).replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            'SELECT ID FROM "%s" ORDER BY ID' % SOURCE_TABLE).fetchall()
    except sqlite3.Error as exc:
        log("cannot read %s: %s" % (SOURCE_TABLE, exc))
        raise SystemExit(1)
    finally:
        conn.close()
    return [int(r[0]) for r in rows]


def render(build, ids):
    head = [
        "-- QuestIDs.lua -- GENERATED, do not hand-edit.",
        "--",
        "-- Source: SELECT ID FROM %s ORDER BY ID" % SOURCE_TABLE,
        "--   against wow-datamine out/%s/wow.db (hotfixed view)." % build,
        "-- Rows: %d. Range: %d..%d." % (len(ids), ids[0], ids[-1]),
        "--",
        "-- Regenerate with:  python questxp/gen_quest_ids.py --build %s" % build,
        "",
        "local _, ns = ...",
        "",
        'ns.questIdSourceBuild = "%s"' % build,
        "",
        "ns.QuestIDs = {",
    ]
    body = []
    for i in range(0, len(ids), IDS_PER_LINE):
        body.append("\t" + ", ".join(str(v) for v in ids[i:i + IDS_PER_LINE]) + ",")
    return "\n".join(head + body + ["}", ""])


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", help="defaults to the newest build with a wow.db")
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args(argv)

    build = args.build or newest_build()
    if not build:
        log("no build with a wow.db -- run scripts/build_db.py")
        return 2

    ids = read_ids(build)
    if not ids:
        log("%s is empty in %s -- nothing to write" % (SOURCE_TABLE, build))
        return 1
    if len(ids) != len(set(ids)):
        log("%s has duplicate IDs -- refusing to write" % SOURCE_TABLE)
        return 1

    text = render(build, ids)
    # The addon is ASCII-only by house rule; fail here rather than in game.
    text.encode("ascii")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="ascii", newline="\n")

    log("wrote %s" % args.out)
    log("  %d ids from %s, range %d..%d" % (len(ids), build, ids[0], ids[-1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
