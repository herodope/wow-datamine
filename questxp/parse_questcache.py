#!/usr/bin/env python3
"""Merge questcache.wdb files into a quest XP table for route planning.

The client rewrites Cache/WDB/<locale>/questcache.wdb from scratch each
session, so one file only ever holds what that session asked for. This reads
any number of them, merges by quest ID, and joins the result against QuestXP
and QuestV2 from out/<build>/wow.db plus the titles QuestXPSweep saved.

    baseXP = QuestXP[questLevel][Difficulty_<tier>] * xpMult

The product is written raw. It is NOT rounded, because the live rounding rule
is unverified -- see the CONFIDENCE table below and the note on PENALTY_BANDS.

CONFIDENCE. Per the repo rule that a rule must say whether it was measured or
reasoned, every payload offset below is tagged:

    confirmed    holds across a real 2,097 record capture
    populated    the field carries varying real data; the label is still a guess
    always-zero  zero on every record read so far
    refuted      the reported label does not survive the data
    inferred     the bytes are real, the meaning is not

Measured 2026-09-22 against one full sweep, build 69913, enUS. Before that
date every offset here was second-hand and the only evidence was a synthetic
file built from this same spec, which could only ever agree with itself.

--selftest still exercises the decoder against that synthetic file. It proves
the decoder matches the spec; the capture is what proves the spec matches the
client, and it now does for the fields tagged confirmed.

Usage:
    python questxp/parse_questcache.py --cache questcache_s1.wdb questcache_s2.wdb
    python questxp/parse_questcache.py --cache cachedir/ --saved QuestXPSweep.lua
    python questxp/parse_questcache.py --selftest
"""

import argparse
import csv
import math
import re
import sqlite3
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import config  # noqa: E402

# --- File format ------------------------------------------------------------

MAGIC = b"TSQW"                  # confirmed: 'WQST' little-endian
HEADER_SIZE = 24                 # reported: magic, build, locale, 3x u32
HEADER_STRUCT = "<4sI4sIII"

# Offsets into a record payload, little-endian.
#
# MEASURED 2026-09-22 against a real capture: 2,097 records, build 69913,
# enUS, one full sweep of all 6,600 QuestV2 ids on a level 20 character.
# Record alignment is independently corroborated -- the title string embedded
# later in each payload matches the quest the header names. The notes below
# say what those 2,097 records actually showed.
#
#   name, offset, struct code, confidence
PAYLOAD_FIELDS = (
    # questID at 0 matched the record header on 2097 of 2097 records.
    ("questID",     0,  "<I", "confirmed"),
    # 3 values: 2 (1953x), 0 (136x), 1 (8x). Real enum, meaning still unknown.
    ("questType",   4,  "<I", "inferred"),
    # Range 0-60, and 60 is this client's level cap.
    ("level",       8,  "<I", "confirmed"),
    # Zero on all 2097 records. Carries nothing in this build.
    ("unk12",       12, "<I", "always-zero"),
    # Range 0-22. minLevel > level on 11 records, which is odd but real.
    ("minLevel",    16, "<I", "confirmed"),
    ("unk20",       20, "<I", "always-zero"),
    # SIGNED. Range -676..16941, with 704 negative values.
    ("sort",        24, "<i", "confirmed"),
    ("info",        28, "<I", "confirmed"),   # QuestInfoID, 81 = dungeon
    # REFUTED as "suggested group size": zero on all 2097 records, including
    # all 70 info==81 dungeon quests, which are exactly the ones that would
    # carry a party size. Either it is something else or it is never filled in.
    ("groupSize",   32, "<I", "refuted"),
    # Nonzero on 677 records, so it is populated. The label is still a guess.
    ("nextQuest",   36, "<I", "populated"),
    # Range 0-8 observed. QuestXP Difficulty_<tier>.
    ("tier",        40, "<I", "confirmed"),
    # Range 1.0-4.35 observed, matching the reported range exactly.
    ("xpMult",      44, "<f", "inferred"),    # label inferred, see below
    # Nonzero on 498 records.
    ("money",       48, "<I", "populated"),
    # Independent of tier: the two agree on only 36% of records, so offset 40
    # and offset 52 are genuinely separate fields rather than one value read
    # twice. 1487 happens to have 6 in both, which is a coincidence.
    ("moneyTier",   52, "<I", "populated"),
    # 1.0 on all but 2 records.
    ("moneyMult",   56, "<f", "inferred"),
)

# Payload lengths ran 506-1969 bytes in the capture, so there is a great deal
# past offset 60 -- the title, objectives and description strings live there.
# Titles still come from the addon's SavedVariables, per the spec.

# Shortest payload that carries every field above.
MIN_PAYLOAD = max(off + struct.calcsize(code) for _, off, code, _ in PAYLOAD_FIELDS)

# 'xpMult' is the working label, not a confirmed one. It reads 1.0 on normal
# quests and 2.9 to 4.35 on dungeon quests, which is consistent with a
# multiplier and also with several other things. Treat a row flagged
# xp_mult as needing a live check before it is trusted for routing.
XP_MULT_NAME = "xpMult"

# A float32 holds about 7.2 significant decimal digits, so 2.9 comes back off
# the wire as 2.9000000953674316 and 2050 * that is 5945.000196. That trailing
# noise is an artifact of the storage format, not data. Snapping the float to
# its shortest float32-safe decimal recovers the value that was authored.
#
# This is NOT the XP rounding the spec says to leave alone: it removes noise
# below a millionth of a point and never moves a value onto the 5 XP grid. A
# genuinely fractional product such as 455 * 4.35 stays 1979.25.
FLOAT32_SIG_DIGITS = 7
XP_OUTPUT_DECIMALS = 6


def snap_float32(value):
    """The shortest decimal a float32 round-trips to."""
    return float("%.*g" % (FLOAT32_SIG_DIGITS, value))


def format_xp(value):
    """Trim float noise for output without touching the value's magnitude."""
    if value is None:
        return ""
    text = "%.*f" % (XP_OUTPUT_DECIMALS, value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"

# --- XP model ---------------------------------------------------------------

QUESTXP_TABLE = "QuestXP"
QUESTV2_TABLE = "QuestV2"
QUESTXP_COLUMNS = 10                      # Difficulty_0 .. Difficulty_9

# Level penalty, from the Warcraft Wiki. NOT VERIFIED against this client --
# that is what verify.csv exists to settle. Keyed by (playerLevel - questLevel).
PENALTY_BANDS = ((5, 100), (6, 80), (7, 60), (8, 40), (9, 20))
PENALTY_FLOOR = 10                        # +10 or more

# Quests below this level are reported to lose full XP one level earlier, so
# the whole band table shifts down by one for them. Also unverified.
EARLY_PENALTY_BELOW_LEVEL = 10

XP_ROUNDING_STEP = 5                      # ROUND(xp * pct / 5) * 5

# --- Dev/test title heuristic ------------------------------------------------

# A heuristic, and labelled as one. It flags rows for a human to look at; it
# does not decide anything. Quest 1 is 'The "Chow" Quest (123)aa'.
DEV_TITLE_PATTERNS = (
    r"\btest\b",
    r"\bdebug\b",
    r"\bdnd\b",
    r"\bdnt\b",                           # Blizzard's "do not translate" marker
    r"\bnyi\b",
    r"<txt>",                             # seen on unfinished text, e.g. 4541
    r"\bunused\b",
    r"\bdeprecated\b",
    r"\bplaceholder\b",
    r"\[ph\]",
    r"\bph\b",
    r"\(\d+\)\s*[a-z]{1,3}$",             # the '(123)aa' shape
    r"\bzzold",
    r"\bzz\b",
)
DEV_TITLE_RE = tuple(re.compile(p, re.IGNORECASE) for p in DEV_TITLE_PATTERNS)

FLAG_ZERO_XP_TIER = "zero_xp_tier"
FLAG_XP_MULT = "xp_mult"
FLAG_NOT_IN_CACHE = "not_in_cache"
FLAG_NOT_IN_QUESTV2 = "not_in_questv2"
FLAG_DEV_TITLE = "dev_title"
FLAG_NO_XP_ROW = "no_questxp_row"
FLAG_CONFLICT = "cache_conflict"

QUESTS_COLUMNS = ("questID", "title", "level", "minLevel", "sort", "info",
                  "tier", "xpMult", "baseXP", "status", "flags")

VERIFY_COLUMNS = ("questID", "title", "cacheLevel", "loggedLevel", "playerLevel",
                  "levelDiff", "pct", "baseXP", "expectedXP", "observedXP",
                  "delta", "match", "note")


def log(msg=""):
    print(msg, file=sys.stderr, flush=True)


# --- Cache file -------------------------------------------------------------

class CacheError(Exception):
    pass


def parse_header(blob, path):
    if len(blob) < HEADER_SIZE:
        raise CacheError("shorter than a %d byte header" % HEADER_SIZE)
    magic, build, locale, u1, u2, u3 = struct.unpack_from(
        HEADER_STRUCT, blob, 0)
    if magic != MAGIC:
        raise CacheError("magic is %r, expected %r" % (magic, MAGIC))
    return {
        "path": str(path),
        "build": build,
        # 'SUne' on disk is 'enUS' written backwards.
        "locale": locale[::-1].decode("ascii", "replace"),
        "unknown": (u1, u2, u3),
    }


def parse_records(blob, path):
    """Yield (questID, payload). Stops at the 8 zero bytes that end the file.

    An unreadable tail is logged and the rest of the file is kept, per the
    repo rule: an unreadable file is expected, not exceptional.
    """
    pos = HEADER_SIZE
    end = len(blob)
    while pos + 8 <= end:
        quest_id, length = struct.unpack_from("<II", blob, pos)
        pos += 8
        if quest_id == 0 and length == 0:
            return                                 # documented terminator
        if length == 0:
            log("  %s: zero-length payload for quest %d at %d, stopping"
                % (path.name, quest_id, pos - 8))
            return
        if pos + length > end:
            log("  %s: quest %d claims %d payload bytes, only %d left, stopping"
                % (path.name, quest_id, length, end - pos))
            return
        yield quest_id, blob[pos:pos + length]
        pos += length
    if pos < end:
        log("  %s: %d trailing byte(s) with no terminator"
            % (path.name, end - pos))


def decode_payload(payload):
    rec = {}
    for name, off, code, _conf in PAYLOAD_FIELDS:
        value = struct.unpack_from(code, payload, off)[0]
        if code.endswith("f"):
            value = snap_float32(value)
        rec[name] = value
    return rec


def read_cache_file(path):
    blob = path.read_bytes()
    header = parse_header(blob, path)
    records = {}
    short = 0
    for quest_id, payload in parse_records(blob, path):
        if len(payload) < MIN_PAYLOAD:
            short += 1
            continue
        rec = decode_payload(payload)
        rec["_payload"] = payload[:MIN_PAYLOAD]
        if rec["questID"] != quest_id:
            log("  %s: record header says quest %d, payload says %d; "
                "using the payload" % (path.name, quest_id, rec["questID"]))
        records[rec["questID"]] = rec
    if short:
        log("  %s: %d record(s) shorter than %d bytes, skipped"
            % (path.name, short, MIN_PAYLOAD))
    return header, records


def collect_cache_paths(inputs):
    """Files and directories, in the order given, each file only once.

    A directory contributes *.wdb plus anything named questcache*, and those
    two globs overlap on the usual questcache.wdb -- reading a file twice
    would double every count in the run summary.
    """
    paths = []
    seen = set()

    def add(path):
        key = path.resolve()
        if key in seen:
            return
        seen.add(key)
        paths.append(path)

    for item in inputs:
        p = Path(item)
        if p.is_dir():
            found = sorted(set(list(p.glob("*.wdb")) + list(p.glob("questcache*"))))
            if not found:
                log("no cache files in %s" % p)
            for f in found:
                if f.is_file():
                    add(f)
        elif p.is_file():
            add(p)
        else:
            log("no such cache path: %s" % p)
    return paths


def merge_caches(paths):
    """First file to carry a quest wins. Disagreements are reported, not hidden."""
    merged = {}
    origin = {}
    conflicts = {}
    headers = []
    for path in paths:
        try:
            header, records = read_cache_file(path)
        except CacheError as exc:
            log("skipping %s: %s" % (path, exc))
            continue
        except OSError as exc:
            log("skipping %s: %s" % (path, exc))
            continue
        headers.append(header)
        new = 0
        for quest_id, rec in records.items():
            if quest_id not in merged:
                merged[quest_id] = rec
                origin[quest_id] = path.name
                new += 1
            elif merged[quest_id]["_payload"] != rec["_payload"]:
                conflicts.setdefault(quest_id, set()).add(origin[quest_id])
                conflicts[quest_id].add(path.name)
        log("  %s: %d record(s), %d new, build %s, locale %s"
            % (path.name, len(records), new, header["build"], header["locale"]))
    return merged, headers, conflicts


# --- SavedVariables ---------------------------------------------------------

class LuaParseError(Exception):
    pass


_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<comment>--\[\[.*?\]\]|--[^\n]*)
  | (?P<string>"(?:\\.|[^"\\])*")
  | (?P<number>-?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)
  | (?P<name>[A-Za-z_]\w*)
  | (?P<punct>[{}\[\]=,;])
""", re.VERBOSE | re.DOTALL)

_STRING_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
                   "t": "\t", "v": "\v", "\\": "\\", '"': '"', "'": "'",
                   "\n": "\n"}


def _unescape(raw):
    out = []
    i = 0
    body = raw[1:-1]
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= len(body):
            break
        nxt = body[i]
        if nxt.isdigit():                       # \ddd decimal escape
            digits = ""
            while i < len(body) and body[i].isdigit() and len(digits) < 3:
                digits += body[i]
                i += 1
            out.append(chr(int(digits)))
            continue
        out.append(_STRING_ESCAPES.get(nxt, nxt))
        i += 1
    return "".join(out)


def _tokenize(text):
    pos = 0
    n = len(text)
    while pos < n:
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise LuaParseError("unexpected character %r at %d"
                                % (text[pos], pos))
        pos = m.end()
        kind = m.lastgroup
        if kind in ("ws", "comment"):
            continue
        yield kind, m.group()
    yield "eof", ""


class _LuaReader:
    """Just enough Lua to read a SavedVariables file. Not an interpreter."""

    def __init__(self, text):
        self.tokens = list(_tokenize(text))
        self.i = 0

    def peek(self):
        return self.tokens[self.i]

    def next(self):
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def expect(self, value):
        kind, text = self.next()
        if text != value:
            raise LuaParseError("expected %r, got %r" % (value, text))

    def value(self):
        kind, text = self.next()
        if text == "{":
            return self.table()
        if kind == "string":
            return _unescape(text)
        if kind == "number":
            return float(text) if ("." in text or "e" in text.lower()) else int(text)
        if text == "true":
            return True
        if text == "false":
            return False
        if text == "nil":
            return None
        if kind == "name":
            return text                      # bare word, e.g. inf
        raise LuaParseError("unexpected value token %r" % text)

    def table(self):
        out = {}
        array_index = 1
        while True:
            kind, text = self.peek()
            if text == "}":
                self.next()
                return out
            if text == ",":
                self.next()
                continue
            if text == "[":
                self.next()
                key = self.value()
                self.expect("]")
                self.expect("=")
                out[key] = self.value()
            elif kind == "name" and self.tokens[self.i + 1][1] == "=":
                self.next()
                self.expect("=")
                out[text] = self.value()
            else:
                out[array_index] = self.value()
                array_index += 1

    def top_level(self):
        out = {}
        while True:
            kind, text = self.peek()
            if kind == "eof":
                return out
            if text in (",", ";"):
                self.next()
                continue
            if kind != "name":
                raise LuaParseError("expected an assignment, got %r" % text)
            self.next()
            self.expect("=")
            out[text] = self.value()


def read_saved_variables(path, var="QuestXPSweepDB"):
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    globals_ = _LuaReader(text).top_level()
    if var not in globals_:
        raise LuaParseError("%s not found in %s" % (var, path))
    db = globals_[var]
    if not isinstance(db, dict):
        raise LuaParseError("%s is not a table" % var)
    return db


# --- wow.db -----------------------------------------------------------------

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


def open_db(build):
    path = config.build_out_dir(build) / "wow.db"
    if not path.exists():
        log("no %s -- run scripts/build_db.py --build %s" % (path, build))
        raise SystemExit(2)
    uri = "file:" + str(path).replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def read_quest_xp(conn):
    """{level: [Difficulty_0 .. Difficulty_9]}"""
    cols = ", ".join('"Difficulty_%d"' % i for i in range(QUESTXP_COLUMNS))
    out = {}
    for row in conn.execute('SELECT ID, %s FROM "%s"' % (cols, QUESTXP_TABLE)):
        out[int(row[0])] = [int(v or 0) for v in row[1:]]
    return out


def read_questv2_ids(conn):
    return set(int(r[0]) for r in
               conn.execute('SELECT ID FROM "%s"' % QUESTV2_TABLE))


# --- XP calculation ---------------------------------------------------------

def base_xp(quest_xp, level, tier, mult):
    """Raw product, unrounded. None when the table cannot answer."""
    row = quest_xp.get(level)
    if row is None or not (0 <= tier < QUESTXP_COLUMNS):
        return None, None
    return row[tier], row[tier] * mult


def penalty_pct(player_level, quest_level):
    diff = player_level - quest_level
    if quest_level < EARLY_PENALTY_BELOW_LEVEL:
        diff += 1                      # the band table shifts down one level
    for threshold, pct in PENALTY_BANDS:
        if diff <= threshold:
            return pct
    return PENALTY_FLOOR


def apply_penalty(xp, pct):
    """ROUND(xp * pct / 5) * 5, rounding halves up rather than to even."""
    scaled = xp * pct / 100.0 / XP_ROUNDING_STEP
    return int(math.floor(scaled + 0.5)) * XP_ROUNDING_STEP


def looks_like_dev_title(title):
    if title is None or title == "":
        return True
    return any(rx.search(title) for rx in DEV_TITLE_RE)


# --- Output -----------------------------------------------------------------

def build_quest_rows(merged, conflicts, quest_xp, questv2, saved_quests):
    ids = sorted(set(merged) | questv2 | set(saved_quests))
    rows = []
    for quest_id in ids:
        rec = merged.get(quest_id)
        saved = saved_quests.get(quest_id) or {}
        title = saved.get("t")
        status = saved.get("s", "")
        flags = []

        if rec is None:
            if quest_id in questv2:
                flags.append(FLAG_NOT_IN_CACHE)
            if looks_like_dev_title(title) and title is not None:
                flags.append(FLAG_DEV_TITLE)
            rows.append({
                "questID": quest_id, "title": title or "", "level": "",
                "minLevel": "", "sort": "", "info": "", "tier": "",
                "xpMult": "", "baseXP": "", "status": status,
                "flags": ";".join(flags),
            })
            continue

        if quest_id not in questv2:
            flags.append(FLAG_NOT_IN_QUESTV2)
        if quest_id in conflicts:
            flags.append(FLAG_CONFLICT)

        level = rec["level"]
        tier = rec["tier"]
        mult = rec[XP_MULT_NAME]
        tier_value, xp = base_xp(quest_xp, level, tier, mult)

        if tier_value is None:
            flags.append(FLAG_NO_XP_ROW)
        elif tier_value == 0:
            flags.append(FLAG_ZERO_XP_TIER)
        if mult != 1.0:
            flags.append(FLAG_XP_MULT)
        if looks_like_dev_title(title if title is not None else ""):
            if title:
                flags.append(FLAG_DEV_TITLE)

        rows.append({
            "questID": quest_id,
            "title": title or "",
            "level": level,
            "minLevel": rec["minLevel"],
            "sort": rec["sort"],
            "info": rec["info"],
            "tier": tier,
            "xpMult": format_xp(mult),
            "baseXP": format_xp(xp),
            "status": status,
            "flags": ";".join(flags),
        })
    return rows


def build_verify_rows(turn_ins, merged, quest_xp, saved_quests):
    rows = []
    for entry in turn_ins:
        quest_id = entry.get("q")
        if quest_id is None:
            continue
        observed = entry.get("xp")
        player_level = entry.get("pl")
        logged_level = entry.get("ql")
        saved = saved_quests.get(quest_id) or {}
        rec = merged.get(quest_id)

        cache_level = rec["level"] if rec else None
        quest_level = cache_level if cache_level else logged_level
        note = []
        if rec is None:
            note.append("no cache record")
        if cache_level and logged_level and cache_level != logged_level:
            note.append("cache level %s != logged %s" % (cache_level, logged_level))

        xp = None
        if rec is not None:
            _, xp = base_xp(quest_xp, rec["level"], rec["tier"], rec[XP_MULT_NAME])

        expected = pct = diff = None
        if xp is not None and player_level is not None and quest_level:
            diff = player_level - quest_level
            pct = penalty_pct(player_level, quest_level)
            expected = apply_penalty(xp, pct)

        if expected is None or observed is None:
            match = "unknown"
            delta = ""
            if expected is None:
                note.append("not computable")
        else:
            delta = observed - expected
            match = "yes" if delta == 0 else "no"

        rows.append({
            "questID": quest_id,
            "title": saved.get("t") or "",
            "cacheLevel": "" if cache_level is None else cache_level,
            "loggedLevel": "" if logged_level is None else logged_level,
            "playerLevel": "" if player_level is None else player_level,
            "levelDiff": "" if diff is None else diff,
            "pct": "" if pct is None else pct,
            "baseXP": format_xp(xp),
            "expectedXP": "" if expected is None else expected,
            "observedXP": "" if observed is None else observed,
            "delta": delta,
            "match": match,
            "note": "; ".join(note),
        })
    return rows


def write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    log("wrote %s (%d row(s))" % (path, len(rows)))


# --- Self test --------------------------------------------------------------

# The two records the format spec was worked out against.
KNOWN_RECORDS = {
    92742: {"level": 12, "minLevel": 9, "sort": 40, "tier": 3, "xpMult": 1.0,
            "info": 0},
    1487:  {"level": 21, "minLevel": 15, "sort": 718, "tier": 6, "xpMult": 2.9,
            "info": 81},
}

SELFTEST_BUILD = 69913
SELFTEST_LOCALE = b"SUne"


def synth_payload(quest_id, level, min_level, sort, info, tier, mult):
    buf = bytearray(MIN_PAYLOAD)
    values = {"questID": quest_id, "questType": 0, "level": level, "unk12": 0,
              "minLevel": min_level, "unk20": 0, "sort": sort, "info": info,
              "groupSize": 0, "nextQuest": 0, "tier": tier, "xpMult": mult,
              "money": 0, "moneyTier": 0, "moneyMult": 1.0}
    for name, off, code, _conf in PAYLOAD_FIELDS:
        struct.pack_into(code, buf, off, values[name])
    return bytes(buf)


def synth_cache(records, path):
    out = bytearray(struct.pack(HEADER_STRUCT, MAGIC, SELFTEST_BUILD,
                                SELFTEST_LOCALE, 12296, 12, 0))
    for quest_id, payload in records:
        out += struct.pack("<II", quest_id, len(payload))
        out += payload
    out += b"\x00" * 8
    Path(path).write_bytes(bytes(out))


def selftest(build):
    """Decode a synthetic cache built to the documented spec.

    This proves the decoder agrees with the spec in this file. It cannot prove
    the spec agrees with the client -- only a real questcache.wdb does that.
    """
    import tempfile

    failures = []

    def check(name, got, want):
        ok = got == want
        print(("  PASS  " if ok else "  FAIL  ") + "%-34s got %r, want %r"
              % (name, got, want))
        if not ok:
            failures.append(name)

    records = [
        (92742, synth_payload(92742, 12, 9, 40, 0, 3, 1.0)),
        (1487, synth_payload(1487, 21, 15, 718, 81, 6, 2.9)),
        (7, synth_payload(7, 6, 1, -1, 0, 2, 1.0)),          # negative sort
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "questcache_selftest.wdb"
        synth_cache(records, path)
        header, decoded = read_cache_file(path)

    print("\n[header]")
    check("magic accepted", header["build"], SELFTEST_BUILD)
    check("locale decoded", header["locale"], "enUS")

    print("\n[known records]")
    for quest_id, want in sorted(KNOWN_RECORDS.items()):
        rec = decoded.get(quest_id)
        if rec is None:
            check("quest %d present" % quest_id, False, True)
            continue
        for field, value in sorted(want.items()):
            got = rec[field]
            if field == XP_MULT_NAME:
                got = round(got, 6)
            check("%d %s" % (quest_id, field), got, value)

    print("\n[signed sort]")
    check("negative sort", decoded[7]["sort"], -1)

    print("\n[terminator]")
    check("record count", len(decoded), 3)

    conn = open_db(build)
    quest_xp = read_quest_xp(conn)
    print("\n[baseXP against %s.%s]" % (QUESTXP_TABLE, build))
    for quest_id, want in sorted(KNOWN_RECORDS.items()):
        rec = decoded[quest_id]
        tier_value, xp = base_xp(quest_xp, rec["level"], rec["tier"],
                                 rec[XP_MULT_NAME])
        check("%d QuestXP[%d][Difficulty_%d]" % (quest_id, rec["level"], rec["tier"]),
              tier_value, quest_xp[want["level"]][want["tier"]])
        check("%d baseXP" % quest_id, format_xp(xp),
              format_xp(tier_value * want[XP_MULT_NAME]))

    print("\n[float32 noise and fractional products]")
    check("2.9 snaps back", snap_float32(
        struct.unpack("<f", struct.pack("<f", 2.9))[0]), 2.9)
    check("4.35 snaps back", snap_float32(
        struct.unpack("<f", struct.pack("<f", 4.35))[0]), 4.35)
    check("2050 * 2.9", format_xp(2050 * snap_float32(
        struct.unpack("<f", struct.pack("<f", 2.9))[0])), "5945")
    # A fractional product must survive: this is the case XP rounding would eat.
    check("455 * 4.35 stays fractional", format_xp(455 * 4.35), "1979.25")
    check("0 stays 0", format_xp(0.0), "0")
    check("None stays blank", format_xp(None), "")

    print("\n[penalty table]")
    # 1000 XP at quest level 20, so the early-level shift does not apply.
    for diff, want_pct in ((0, 100), (5, 100), (6, 80), (7, 60), (8, 40),
                           (9, 20), (10, 10), (25, 10)):
        check("level +%d" % diff, penalty_pct(20 + diff, 20), want_pct)
    print("  -- quests under level %d lose it one level earlier"
          % EARLY_PENALTY_BELOW_LEVEL)
    for diff, want_pct in ((4, 100), (5, 80), (6, 60), (9, 10)):
        check("level +%d at quest level 9" % diff, penalty_pct(9 + diff, 9), want_pct)

    print("\n[rounding]")
    check("ROUND(1000*80%/5)*5", apply_penalty(1000, 80), 800)
    check("ROUND(455*60%/5)*5", apply_penalty(455, 60), 275)
    check("ROUND(5945*20%/5)*5", apply_penalty(5945, 20), 1190)
    check("halves round up", apply_penalty(25, 10), 5)

    print("\n[lua reader]")
    sample = ('QuestXPSweepDB = {\n'
              '\t["quests"] = {\n'
              '\t\t[1487] = {\n'
              '\t\t\t["s"] = "ok",\n'
              '\t\t\t["t"] = "The \\"Chow\\" Quest (123)aa",\n'
              '\t\t\t["l"] = 21,\n'
              '\t\t},\n'
              '\t},\n'
              '\t["verify"] = false,\n'
              '\t["turnIns"] = {\n'
              '\t\t{ ["q"] = 1487, ["xp"] = 5945, ["pl"] = 21, ["ql"] = 21 },\n'
              '\t},\n'
              '}\n')

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "sv.lua"
        p.write_text(sample, encoding="utf-8")
        db = read_saved_variables(p)
    check("quest parsed", db["quests"][1487]["l"], 21)
    check("escaped quotes", db["quests"][1487]["t"], 'The "Chow" Quest (123)aa')
    check("boolean parsed", db["verify"], False)
    check("array entry parsed", db["turnIns"][1]["xp"], 5945)
    check("dev title flagged", looks_like_dev_title(db["quests"][1487]["t"]), True)
    check("real title not flagged",
          looks_like_dev_title("The Curse of the Tides"), False)

    print("\n%d check(s) failed" % len(failures))
    for name in failures:
        print("  " + name)
    return 1 if failures else 0


# --- Main -------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", nargs="+", default=[], metavar="PATH",
                    help="questcache.wdb files, or directories holding them")
    ap.add_argument("--saved", metavar="LUA",
                    help="QuestXPSweep.lua from WTF/Account/<ACCOUNT>/SavedVariables")
    ap.add_argument("--build", help="defaults to the newest build with a wow.db")
    ap.add_argument("--out-dir", type=Path, default=Path("questxp/out"))
    ap.add_argument("--selftest", action="store_true",
                    help="decode a synthetic cache and check the known records")
    args = ap.parse_args(argv)

    build = args.build or newest_build()
    if not build:
        log("no build with a wow.db -- run scripts/build_db.py")
        return 2

    if args.selftest:
        return selftest(build)

    if not args.cache:
        log("nothing to do: pass --cache with at least one questcache.wdb")
        return 2

    paths = collect_cache_paths(args.cache)
    if not paths:
        log("no readable cache files")
        return 2

    log("reading %d cache file(s)" % len(paths))
    merged, headers, conflicts = merge_caches(paths)
    if not merged:
        log("no records decoded from any cache file")
        return 1

    conn = open_db(build)
    quest_xp = read_quest_xp(conn)
    questv2 = read_questv2_ids(conn)

    build_id = int(build.rsplit(".", 1)[1])
    for header in headers:
        if header["build"] != build_id:
            log("WARNING: %s was written by build %d, joining against %s"
                % (Path(header["path"]).name, header["build"], build))

    saved_quests = {}
    turn_ins = []
    if args.saved:
        try:
            db = read_saved_variables(args.saved)
        except (LuaParseError, OSError) as exc:
            log("cannot read %s: %s" % (args.saved, exc))
            db = {}
        quests = db.get("quests") or {}
        saved_quests = {int(k): v for k, v in quests.items()
                        if isinstance(v, dict)}
        raw_turn_ins = db.get("turnIns") or {}
        turn_ins = [v for _, v in sorted(raw_turn_ins.items())
                    if isinstance(v, dict)]
        log("saved variables: %d quest record(s), %d turn-in(s)"
            % (len(saved_quests), len(turn_ins)))
    else:
        log("no --saved given: titles and status will be blank, "
            "and verify.csv will not be written")

    rows = build_quest_rows(merged, conflicts, quest_xp, questv2, saved_quests)
    write_csv(args.out_dir / "quests.csv", QUESTS_COLUMNS, rows)

    # Coverage, reported next to the counts so a zero can be told from a
    # detector that never ran.
    flag_counts = {}
    for row in rows:
        for flag in row["flags"].split(";"):
            if flag:
                flag_counts[flag] = flag_counts.get(flag, 0) + 1
    log("")
    width = max([20, len(QUESTV2_TABLE) + 5] + [len(f) for f in flag_counts])
    log("  %-*s : %d" % (width, "cache records merged", len(merged)))
    log("  %-*s : %d" % (width, QUESTV2_TABLE + " rows", len(questv2)))
    log("  %-*s : %d" % (width, "rows written", len(rows)))
    log("  %-*s : %d" % (width, "with a baseXP",
                         sum(1 for r in rows if r["baseXP"] != "")))
    for flag in sorted(flag_counts):
        log("  %-*s : %d" % (width, flag, flag_counts[flag]))
    if conflicts:
        log("  NOTE: %d quest(s) decoded differently in different cache files"
            % len(conflicts))

    if turn_ins:
        vrows = build_verify_rows(turn_ins, merged, quest_xp, saved_quests)
        write_csv(args.out_dir / "verify.csv", VERIFY_COLUMNS, vrows)
        yes = sum(1 for r in vrows if r["match"] == "yes")
        no = sum(1 for r in vrows if r["match"] == "no")
        unknown = sum(1 for r in vrows if r["match"] == "unknown")
        log("")
        log("  verify: %d match, %d mismatch, %d not computable"
            % (yes, no, unknown))
        if no:
            log("  the penalty table and the rounding rule are both unverified;")
            log("  a mismatch is as likely to be the model as the data.")
            for row in vrows:
                if row["match"] == "no":
                    log("    quest %-7s level %-4s player %-4s expected %-8s got %-8s"
                        % (row["questID"], row["cacheLevel"], row["playerLevel"],
                           row["expectedXP"], row["observedXP"]))
    elif args.saved:
        log("")
        log("  no turn-ins logged: verify.csv not written. "
            "Enable it in game with /qxs verify on.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
