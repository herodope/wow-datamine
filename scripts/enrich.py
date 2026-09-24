#!/usr/bin/env python3
"""Turn IDs into human-readable context. Imported by the report generators.

Given a table and a row ID, `Enricher.resolve()` returns a structure carrying
the row's display label, its foreign keys resolved to names, its enum and flag
columns decoded, and an explicit list of columns that must not be asserted in
committed output. Everything it cannot resolve stays numeric and is marked
`unresolved` rather than dropped or guessed at.

Two data sources, with a hard rule about which answers what:

  out/<build>/db2/          the shipped client build
  out/<build>/db2_hotfixed/ the same tables with the live hotfix overlay
  WTL over HTTP             enum/flag mappings, DBD column metadata, tooltips

CSV joins are the load-bearing half and work with WTL stopped. WTL is
*enrichment only* -- if it is not reachable, enum decoding, unverified-column
marking and item tooltips degrade to "unavailable" and say so in the result.
Nothing here fails hard on a missing WTL, because the report generators must
still produce a report.

Item lookups route two ways, and this is the whole reason the module exists.
`/dbc/tooltip/item/{id}` is the better answer -- it computes stats, resolves
the icon through ItemModifiedAppearance, and renders flavor text -- but the
tooltip controller loads every table through the two-argument
`GetOrLoad(name, CASC.BuildName)`, i.e. `useHotfixes: false`, with no
parameter to change it. Measured on 1.60.1.69913: of the 4,218 hotfix-only
ItemSparse rows, `/dbc/tooltip/item/720` returns HTTP 200 with
`name: "Unknown Item"`, `hasSparse: false` -- a plausible-looking answer for
an item that exists. So:

    ID in the SHIPPED ItemSparse or ItemSearchName -> /dbc/tooltip/item/{id}
    otherwise (hotfix-only)                        -> direct CSV join

See docs/wtl-api.md, "Row lookup and rendered tooltips".

Usage as a library:

    import enrich
    e = enrich.get("1.60.1.69913")
    e.label("Achievement", 9275)      -> "Warlord Zaela kills (Upper ...)"
    e.item(720)                       -> {... "source": "join" ...}
    e.resolve("Light", 16161)         -> full structure

Usage as a smoke test:

    python scripts/enrich.py Achievement 9275
    python scripts/enrich.py Item 720
    python scripts/enrich.py --self-test
"""

import argparse
import atexit
import csv
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import config
from diff_hotfixes import key_index, key_rows, load_csv

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

USER_AGENT = "wow-datamine/1.0 (+local datamining pipeline)"

# WTL is optional here, so the probe must be short. A report generator should
# not stall for 30s per call because the server is down.
PROBE_TIMEOUT = 5
CALL_TIMEOUT = 30
META_TIMEOUT = 180          # /dbc/meta/getMappings is ~10 MB

# Bump when the on-disk cache layout changes so stale caches are ignored.
CACHE_VERSION = 1

PLAIN, HOTFIXED = "plain", "hotfixed"
VARIANT_DIRS = {PLAIN: "db2", HOTFIXED: "db2_hotfixed"}


# --- Display labels ---------------------------------------------------------

# table (lowercased) -> (table to read the label from, column).
#
# The label table differs from the keyed table where the name lives elsewhere:
# Spell.db2 carries only Description_lang/NameSubtext_lang, and the actual
# spell name is in SpellName.db2 under the same ID.
#
# Only columns verified present in 1.60.1.69913 are listed. QuestV2 is
# deliberately absent -- it has no name column in this build (quest text lives
# in the client's WDB cache, not a DB2), so quests fall through to unresolved
# rather than being given an invented label.
LABELS = {
    "achievement":          ("Achievement", "Title_lang"),
    "achievement_category": ("Achievement_Category", "Name_lang"),
    "areatable":            ("AreaTable", "AreaName_lang"),
    "chrclasses":           ("ChrClasses", "Name_lang"),
    "chrraces":             ("ChrRaces", "Name_lang"),
    "creature":             ("Creature", "Name_lang"),
    "currencytypes":        ("CurrencyTypes", "Name_lang"),
    "faction":              ("Faction", "Name_lang"),
    "globalstrings":        ("GlobalStrings", "BaseTag"),
    "itemsearchname":       ("ItemSearchName", "Display_lang"),
    "itemsparse":           ("ItemSparse", "Display_lang"),
    "map":                  ("Map", "MapName_lang"),
    "skillline":            ("SkillLine", "DisplayName_lang"),
    "spell":                ("SpellName", "Name_lang"),
    "spellname":            ("SpellName", "Name_lang"),
}

# Columns that reference another table, used only when WTL is unreachable and
# the real FK map from /dbc/header/{name} is unavailable. Keys are lowercased
# column names with any array suffix stripped.
#
# UiMapID is deliberately excluded: it is a different ID namespace from Map.ID
# and resolving it against Map would produce confident nonsense. Same rule as
# contamination.MAP_REF_COLUMNS.
FALLBACK_FKS = {
    "instance_id":    "Map::ID",
    "instanceid":     "Map::ID",
    "continentid":    "Map::ID",
    "mapid":          "Map::ID",
    "map_id":         "Map::ID",
    "parentmapid":    "Map::ID",
    "cosmeticparentmapid": "Map::ID",
    "corpsemapid":    "Map::ID",
    "areatableid":    "AreaTable::ID",
    "parentareaid":   "AreaTable::ID",
    "zoneid":         "AreaTable::ID",
    "lightparamsid":  "LightParams::ID",
    "spellid":        "SpellName::ID",
    "itemid":         "Item::ID",
    "category":       "Achievement_Category::ID",
    "supercedes":     "Achievement::ID",
    "factionid":      "Faction::ID",
    "rewarditemid":   "Item::ID",
}


def _base_column(name):
    """'LightParamsID[3]' -> ('lightparamsid', 3); 'ID' -> ('id', None)."""
    n = (name or "").strip()
    idx = None
    if n.endswith("]") and "[" in n:
        head, _, tail = n.rpartition("[")
        try:
            idx = int(tail[:-1])
            n = head
        except ValueError:
            pass
    return n.lower(), idx


# --- HTTP -------------------------------------------------------------------


class _Wtl:
    """Thin WTL client that reports unavailability instead of raising.

    Probes once, lazily. Every caller must handle `None` as "WTL could not
    answer" -- that is the normal path when the server is not running.
    """

    def __init__(self, url=None, enabled=True):
        self.url = url or config.WTL_URL
        self.enabled = enabled
        self._probed = False
        self.available = False
        self.build_name = None
        self.reason = "not probed"

    def probe(self):
        if self._probed:
            return self.available
        self._probed = True
        if not self.enabled:
            self.reason = "disabled by caller"
            return False
        try:
            status, body = self._raw("/casc/buildname", timeout=PROBE_TIMEOUT)
        except Exception as exc:                      # noqa: BLE001 - any transport failure
            self.reason = f"unreachable at {self.url}: {exc}"
            return False
        if status != 200 or not body:
            self.reason = f"/casc/buildname returned HTTP {status}"
            return False
        self.available = True
        self.build_name = body.decode("utf-8").strip()
        self.reason = "ok"
        return True

    def _raw(self, path, params=None, timeout=CALL_TIMEOUT):
        url = self.url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            # 404 and 500 are answers, not transport failures. A 500 from WTL
            # usually means bad input (no exception handler is registered for
            # non-Development environments), not that the server is down --
            # so it must not disable the client.
            return exc.code, exc.read()

    def json(self, path, params=None, timeout=CALL_TIMEOUT):
        """Parsed JSON, or None if WTL is unavailable or did not answer 200."""
        if not self.probe():
            return None
        try:
            status, body = self._raw(path, params, timeout)
        except Exception:                             # noqa: BLE001
            return None
        if status != 200 or not body:
            return None
        try:
            return json.loads(body)
        except ValueError:
            return None


# --- Enricher ---------------------------------------------------------------


class Enricher:
    """Resolves IDs to human-readable context for one build.

    Caches aggressively and on two levels: parsed CSV tables and WTL responses
    live in memory for the process, and the two expensive WTL responses (the
    ~10 MB enum mapping set and per-table column metadata) are also written to
    out/<build>/.enrich-cache/ so a second script run does not refetch them.
    Pass `use_cache=False` to bypass the disk half.
    """

    def __init__(self, build=None, use_wtl=True, wtl_url=None, use_cache=True):
        self.build = build or self._latest_build()
        self.out_dir = config.build_out_dir(self.build)
        self.wtl = _Wtl(wtl_url, enabled=use_wtl)
        self.use_cache = use_cache
        self.cache_dir = self.out_dir / ".enrich-cache"

        self._tables = {}        # (variant, lowername) -> (header, {id: row})
        self._headers = {}       # lowername -> header info dict
        self._mappings = None    # (table, column, arrIndex) -> unconditional mapping
        self._conditional = {}   # same key -> [mappings gated on another column]
        self._tooltips = {}      # ("item"|"spell", id) -> dict|None
        self._light_by_param = None
        self._files = None
        self._dirty = set()
        atexit.register(self.flush)

    # --- build / paths ------------------------------------------------------

    @staticmethod
    def _latest_build():
        """Highest-numbered extracted build under out/. Forever-filtered."""
        candidates = []
        if config.OUT_DIR.is_dir():
            for d in config.OUT_DIR.iterdir():
                if not d.is_dir():
                    continue
                parts = d.name.rsplit(".", 1)
                if len(parts) != 2 or not parts[1].isdigit():
                    continue
                if config.is_forever_build(d.name, int(parts[1])):
                    candidates.append((int(parts[1]), d.name))
        if not candidates:
            raise SystemExit(
                f"no extracted Forever build found under {config.OUT_DIR}\n"
                "  run scripts/extract_db2.py first, or pass build= explicitly"
            )
        return max(candidates)[1]

    def _cache_path(self, name):
        return self.cache_dir / f"{name}.json"

    def _cache_read(self, name):
        if not self.use_cache:
            return None
        p = self._cache_path(name)
        if not p.exists():
            return None
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None
        if blob.get("version") != CACHE_VERSION or blob.get("build") != self.build:
            return None
        return blob.get("data")

    def _cache_write(self, name, data):
        if not self.use_cache:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._cache_path(name).with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"version": CACHE_VERSION, "build": self.build, "data": data}),
                encoding="utf-8",
            )
            tmp.replace(self._cache_path(name))
        except OSError:
            pass  # a cache that cannot be written is not an error

    def flush(self):
        """Persist the caches that accumulate during a run."""
        if "tooltips" in self._dirty:
            self._cache_write(
                "tooltips",
                {f"{kind}:{ident}": val for (kind, ident), val in self._tooltips.items()},
            )
            self._dirty.discard("tooltips")

    # --- CSV access ---------------------------------------------------------

    def table(self, name, variant=HOTFIXED):
        """(header, {id_string: row}) for one table, keyed on the ID column.

        Returns (None, {}) when the table has no CSV -- which is the normal
        result for a 204-empty table, not a failure. Keying is on the column
        literally named `ID`, never column 0: DBCD emits columns in DBD
        definition order and Achievement puts ID at index 3.
        """
        key = (variant, name.lower())
        if key in self._tables:
            return self._tables[key]

        path = self.out_dir / VARIANT_DIRS[variant] / f"{name}.csv"
        header, rows = load_csv(path)
        if header is None:
            self._tables[key] = (None, {})
            return self._tables[key]
        idx, _col = key_index(header, name)
        self._tables[key] = (header, key_rows(rows, idx, f"{name}.csv ({variant})"))
        return self._tables[key]

    def row(self, name, row_id, variant=HOTFIXED):
        """One row as a list, or None. Falls back across variants when asked."""
        _h, rows = self.table(name, variant)
        return rows.get(str(row_id))

    def row_any(self, name, row_id):
        """(row, variant) preferring the hotfix overlay, or (None, None)."""
        for variant in (HOTFIXED, PLAIN):
            r = self.row(name, row_id, variant)
            if r is not None:
                return r, variant
        return None, None

    def cell(self, name, row_id, column, variant=HOTFIXED):
        header, rows = self.table(name, variant)
        row = rows.get(str(row_id))
        if row is None or not header:
            return None
        for i, n in enumerate(header):
            if n.strip().lower() == column.lower():
                return row[i] if i < len(row) else None
        return None

    # --- WTL-backed metadata ------------------------------------------------

    def header_info(self, table):
        """DBD column metadata for a table: fks, unverifieds, comments.

        Falls back to FALLBACK_FKS and an empty unverified set when WTL is
        unreachable, flagging `source` so callers can tell a genuinely empty
        unverified list from an unknown one.
        """
        key = table.lower()
        if key in self._headers:
            return self._headers[key]

        disk = self._cache_read(f"header-{key}")
        if disk is not None:
            self._headers[key] = disk
            return disk

        info = {
            "headers": [],
            "fks": {},
            "comments": {},
            "unverifieds": [],
            "source": "fallback",
        }
        payload = self.wtl.json(f"/dbc/header/{table}", {"build": self.build})
        if isinstance(payload, dict) and not payload.get("error"):
            info = {
                "headers": payload.get("headers") or [],
                "fks": payload.get("fks") or {},
                "comments": payload.get("comments") or {},
                "unverifieds": payload.get("unverifieds") or [],
                "source": "wtl",
            }
            self._cache_write(f"header-{key}", info)

        self._headers[key] = info
        return info

    def unverified(self, table):
        """Set of columns DBD marks unverified -- i.e. not safe to assert.

        Empty when WTL is unavailable; check `header_info(table)["source"]`
        to distinguish "none" from "unknown".
        """
        return {c.lower() for c in self.header_info(table)["unverifieds"]}

    def mappings(self):
        """(table, column, arrIndex) -> mapping, from /dbc/meta/getMappings.

        GOTCHA (CLAUDE.md, docs/wtl-api.md): `build` MUST be passed. Without
        it, columns whose enum was re-versioned return both variants with the
        retail one first -- measured, 2 of 606 mappings collide that way, and
        `Weather::Type` comes back with values 0-5 twice, retail names ahead
        of Classic. A decoder taking the first entry matching a value then
        labels every Forever weather row with retail names. With `build` set,
        0 mappings collide and none come back empty.
        """
        if self._mappings is not None:
            return self._mappings

        raw = self._cache_read("meta-mappings")
        if raw is None:
            payload = self.wtl.json(
                "/dbc/meta/getMappings", {"build": self.build}, timeout=META_TIMEOUT
            )
            raw = payload if isinstance(payload, list) else []
            if raw:
                self._cache_write("meta-mappings", raw)

        # Two indexes, because a column's enum can depend on another column.
        # Item::SubclassID has 21 conditional mappings keyed on Item::ClassID
        # -- the subclass names are entirely different per item class. A flat
        # index keyed on (table, column, arrIndex) keeps whichever mapping was
        # read last (ClassID 20) and would then label SubclassID 0 on a
        # ClassID 4 row with a name from a different class. That is the exact
        # shape of silent wrongness this pipeline exists to avoid, so
        # conditional mappings are kept apart and only used when the caller
        # supplies enough row context to pick one.
        uncond, cond = {}, {}
        for m in raw:
            t = (m.get("tableName") or "").lower()
            c = (m.get("columnName") or "").lower()
            if not t or not c:
                continue
            key = (t, c, m.get("arrIndex"))
            if m.get("conditionalTable"):
                cond.setdefault(key, []).append(m)
            else:
                uncond.setdefault(key, m)   # first wins; the 2 dupes are identical
        self._mappings = uncond
        self._conditional = cond
        return uncond

    def conditional_mappings(self):
        if self._mappings is None:
            self.mappings()
        return self._conditional

    def decode(self, table, column, value, context=None):
        """Decode one cell against its enum/flag/colour mapping.

        `context` is {column_name: raw_value} for the row the cell came from,
        needed only for columns whose enum is gated on another column. Without
        it such a column is returned `unresolved` with `needs_context` naming
        the gate, never decoded against an arbitrary variant.

        Returns None when the column has no mapping or the value is not
        numeric. Never invents a name: unmatched flag bits are reported as
        `unknown_bits` and an unmatched enum value as `unresolved`.
        """
        base, arr = _base_column(column)
        index = self.mappings()
        if not index and not self.conditional_mappings():
            return None

        tkey = table.lower()
        m = index.get((tkey, base, arr)) or index.get((tkey, base, None))

        candidates = (self.conditional_mappings().get((tkey, base, arr))
                      or self.conditional_mappings().get((tkey, base, None)) or [])
        if candidates:
            picked, gate = self._pick_conditional(candidates, context)
            if picked is not None:
                m = picked
            elif m is None:
                try:
                    n = int(str(value).strip())
                except (TypeError, ValueError):
                    return None
                return {
                    "kind": "enum",
                    "raw": n,
                    "name": None,
                    "unresolved": True,
                    "needs_context": gate,
                }

        if m is None:
            return None

        try:
            n = int(str(value).strip())
        except (TypeError, ValueError):
            return None

        meta = m.get("meta")
        entries = m.get("entries") or []

        if meta == 2:  # COLOR
            # Byte order is NOT asserted. WoW packs these inconsistently and
            # the project convention is never to state an unverified meaning
            # in committed output. Both readings are offered; the caller picks
            # and labels it.
            b = [(n >> 0) & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF, (n >> 24) & 0xFF]
            return {
                "kind": "color",
                "raw": n,
                "bytes": b,
                "hex_rgb": "#%02X%02X%02X" % (b[2], b[1], b[0]),
                "hex_bgr": "#%02X%02X%02X" % (b[0], b[1], b[2]),
                "byte_order": "unasserted",
            }

        if meta == 0:  # FLAGS
            names, matched = [], 0
            for e in entries:
                v = e.get("value")
                if isinstance(v, int) and v and (n & v) == v:
                    names.append(e.get("name") or f"bit_{v}")
                    matched |= v
            leftover = n & ~matched
            return {
                "kind": "flags",
                "raw": n,
                "names": names,
                "unknown_bits": leftover or None,
                "unresolved": bool(leftover) or (n != 0 and not names),
            }

        if meta == 1:  # ENUM
            for e in entries:
                if e.get("value") == n:
                    return {
                        "kind": "enum",
                        "raw": n,
                        "name": e.get("name"),
                        "comment": e.get("comment"),
                        "unresolved": False,
                    }
            return {"kind": "enum", "raw": n, "name": None, "unresolved": True}

        return None

    @staticmethod
    def _pick_conditional(candidates, context):
        """(mapping, gate_description). mapping is None when context cannot decide."""
        gate = candidates[0]
        gate_desc = f"{gate.get('conditionalTable')}::{gate.get('conditionalColumn')}"
        if not context:
            return None, gate_desc
        lowered = {str(k).lower(): str(v).strip() for k, v in context.items()}
        for m in candidates:
            col = (m.get("conditionalColumn") or "").lower()
            want = str(m.get("conditionalValue") or "").strip()
            if col and lowered.get(col) == want:
                return m, gate_desc
        return None, gate_desc

    # --- labels -------------------------------------------------------------

    def label(self, table, row_id):
        """Short human-readable name for a row, or None if there is none.

        None means "this table has no label column, or the row is absent" --
        callers render the bare ID and mark it unresolved rather than
        substituting something plausible.
        """
        key = table.lower()

        if key == "item":
            return self.item(row_id).get("name")
        if key == "light":
            return self._light_label(row_id)
        if key == "lightparams":
            return self._lightparams_label(row_id)

        target = LABELS.get(key)
        if target is None:
            return None
        src_table, column = target
        for variant in (HOTFIXED, PLAIN):
            val = self.cell(src_table, row_id, column, variant)
            if val:
                return val
        return None

    def _map_name(self, map_id):
        for variant in (HOTFIXED, PLAIN):
            v = self.cell("Map", map_id, "MapName_lang", variant)
            if v:
                return v
        return None

    def _light_label(self, light_id):
        """Light rows have no name; the useful context is which map they sit on."""
        row, variant = self.row_any("Light", light_id)
        if row is None:
            return None
        header, _ = self.table("Light", variant)
        cont = None
        for i, n in enumerate(header or []):
            if n.strip().lower() == "continentid" and i < len(row):
                cont = row[i]
        if cont is None:
            return f"Light {light_id}"
        name = self._map_name(cont)
        return f"Light {light_id} on {name}" if name else f"Light {light_id} on map {cont} (absent)"

    def light_by_param(self):
        """LightParams ID -> [(light_id, continent_id)], from the hotfixed Light table.

        Same join contamination.py uses for its light_absent_map rule: a
        LightParams row has no map of its own, so its continent comes from the
        Light rows that reference it across LightParamsID[0..7].
        """
        if self._light_by_param is not None:
            return self._light_by_param
        header, rows = self.table("Light", HOTFIXED)
        if not header:
            header, rows = self.table("Light", PLAIN)
        by_param = {}
        if header:
            param_cols = [i for i, n in enumerate(header) if n.lower().startswith("lightparamsid")]
            cont_i = next((i for i, n in enumerate(header) if n.strip().lower() == "continentid"), None)
            for light_id, row in rows.items():
                cont = row[cont_i] if cont_i is not None and cont_i < len(row) else None
                for i in param_cols:
                    if i < len(row) and row[i] not in ("0", ""):
                        by_param.setdefault(row[i], []).append((light_id, cont))
        self._light_by_param = by_param
        return by_param

    def _lightparams_label(self, param_id):
        uses = self.light_by_param().get(str(param_id), [])
        if not uses:
            return f"LightParams {param_id} (unreferenced)"
        maps = []
        for _light_id, cont in uses:
            name = self._map_name(cont) if cont else None
            entry = name or (f"map {cont} (absent)" if cont else "no map")
            if entry not in maps:
                maps.append(entry)
        light_ids = ", ".join(str(l) for l, _ in uses[:4])
        more = "" if len(uses) <= 4 else f" +{len(uses) - 4}"
        return f"LightParams {param_id} — Light {light_ids}{more} on {', '.join(maps)}"

    # --- items --------------------------------------------------------------

    def item_is_shipped(self, item_id):
        """True if the tooltip route can see this item.

        The tooltip controller reads plain ItemSparse then falls back to plain
        ItemSearchName, both non-hotfixed. If the ID is in neither of the
        SHIPPED tables the route returns 200 with "Unknown Item", so this
        predicate is exactly the routing decision.
        """
        sid = str(item_id)
        for t in ("ItemSparse", "ItemSearchName"):
            _h, rows = self.table(t, PLAIN)
            if sid in rows:
                return True
        return False

    def _tooltip(self, kind, ident):
        key = (kind, str(ident))
        if key in self._tooltips:
            return self._tooltips[key]
        if not self._tooltips and self.use_cache:
            for k, v in (self._cache_read("tooltips") or {}).items():
                kk, _, vv = k.partition(":")
                self._tooltips[(kk, vv)] = v
            if key in self._tooltips:
                return self._tooltips[key]
        payload = self.wtl.json(f"/dbc/tooltip/{kind}/{ident}")
        # Keys are camelCase on the wire, not the PascalCase the C# struct declares.
        self._tooltips[key] = payload if isinstance(payload, dict) else None
        self._dirty.add("tooltips")
        return self._tooltips[key]

    def item(self, item_id):
        """Item context, routed around the tooltip controller's hotfix blindness.

        `source` records which path answered:
            "tooltip"     -- /dbc/tooltip/item, shipped row, stats computed
            "join"        -- direct ItemSparse/ItemSearchName join, hotfix-only
            "unresolved"  -- neither had a name
        """
        sid = str(item_id)
        result = {
            "id": item_id,
            "source": "unresolved",
            "name": None,
            "unresolved": True,
            "icon_fdid": None,
            "quality": None,
            "item_level": None,
            "required_level": None,
            "expansion": None,
            "flavor": None,
            "shipped": self.item_is_shipped(item_id),
            "hotfix_only": False,
            "notes": [],
        }

        _h, hot_sparse = self.table("ItemSparse", HOTFIXED)
        _h2, hot_search = self.table("ItemSearchName", HOTFIXED)
        result["hotfix_only"] = (not result["shipped"]) and (sid in hot_sparse or sid in hot_search)

        if result["shipped"]:
            tt = self._tooltip("item", item_id)
            if tt is None:
                result["notes"].append(
                    f"tooltip route unavailable ({self.wtl.reason}); fell back to CSV join"
                )
            elif tt.get("name") and tt.get("name") != "Unknown Item":
                result.update(
                    source="tooltip",
                    name=tt.get("name"),
                    unresolved=False,
                    icon_fdid=tt.get("iconFileDataID") or None,
                    quality=tt.get("overallQualityID"),
                    item_level=tt.get("itemLevel"),
                    required_level=tt.get("requiredLevel"),
                    expansion=tt.get("expansionID"),
                    flavor=tt.get("flavorText") or None,
                )
                return result
            else:
                result["notes"].append(
                    'tooltip returned "Unknown Item" for a shipped row; using CSV join'
                )

        if result["hotfix_only"]:
            result["notes"].append(
                "hotfix-only row: the tooltip controller is non-hotfixed and would "
                'return "Unknown Item", so this was resolved by direct join'
            )

        self._item_from_csv(item_id, result)
        return result

    def _item_from_csv(self, item_id, result):
        """Fill an item result from ItemSparse, then ItemSearchName, then Item."""
        got = False
        for variant in (HOTFIXED, PLAIN):
            if self.row("ItemSparse", item_id, variant) is None:
                continue
            name = self.cell("ItemSparse", item_id, "Display_lang", variant)
            if name:
                result.update(source="join", name=name, unresolved=False)
                got = True
            result["flavor"] = self.cell("ItemSparse", item_id, "Description_lang", variant) or None
            result["quality"] = _int(self.cell("ItemSparse", item_id, "OverallQualityID", variant))
            result["item_level"] = _int(self.cell("ItemSparse", item_id, "ItemLevel", variant))
            result["required_level"] = _int(self.cell("ItemSparse", item_id, "RequiredLevel", variant))
            result["expansion"] = _int(self.cell("ItemSparse", item_id, "ExpansionID", variant))
            break

        if not got:
            for variant in (HOTFIXED, PLAIN):
                name = self.cell("ItemSearchName", item_id, "Display_lang", variant)
                if name:
                    result.update(source="join", name=name, unresolved=False)
                    result["quality"] = _int(self.cell("ItemSearchName", item_id, "OverallQualityID", variant))
                    result["item_level"] = _int(self.cell("ItemSearchName", item_id, "ItemLevel", variant))
                    result["required_level"] = _int(self.cell("ItemSearchName", item_id, "RequiredLevel", variant))
                    result["expansion"] = _int(self.cell("ItemSearchName", item_id, "ExpansionID", variant))
                    break

        if result["icon_fdid"] is None:
            result["icon_fdid"] = self._item_icon(item_id)

        if result["name"] is None:
            _row, variant = self.row_any("Item", item_id)
            if variant is not None:
                result["notes"].append(
                    "present in Item.db2 but no ItemSparse/ItemSearchName row in either variant"
                )

    def _item_icon(self, item_id):
        """Item.IconFileDataID, or the ItemModifiedAppearance -> ItemAppearance
        fallback the tooltip controller uses when it is 0."""
        for variant in (HOTFIXED, PLAIN):
            raw = _int(self.cell("Item", item_id, "IconFileDataID", variant))
            if raw:
                return raw

        header, rows = self.table("ItemModifiedAppearance", HOTFIXED)
        if not header:
            header, rows = self.table("ItemModifiedAppearance", PLAIN)
        if not header:
            return None
        item_i = next((i for i, n in enumerate(header) if n.strip().lower() == "itemid"), None)
        app_i = next((i for i, n in enumerate(header) if n.strip().lower() == "itemappearanceid"), None)
        if item_i is None or app_i is None:
            return None
        for row in rows.values():
            if item_i < len(row) and row[item_i] == str(item_id) and app_i < len(row):
                icon = _int(self.cell("ItemAppearance", row[app_i], "DefaultIconFileDataID", HOTFIXED))
                return icon or None
        return None

    def file_ref(self, fdid):
        """Context for an FDID column: listfile name plus a render URL.

        `image_url` is only offered when files.csv types the file as a BLP.
        /casc/blp2png 500s on non-BLP input and 404s on an encrypted file, so
        a caller embedding the URL blindly would get broken images; `encrypted`
        is carried through so the report can say why one is missing rather
        than showing a gap.
        """
        info = self.files().get(str(fdid))
        out = {"fdid": _int(fdid), "resolved": None, "unresolved": True}
        if info is None:
            return out
        name, ctype, enc = info
        out["resolved"] = name or None
        out["unresolved"] = not name
        out["content_type"] = ctype or None
        out["encrypted"] = enc or None
        if ctype == "blp" and not enc:
            out["image_url"] = f"{self.wtl.url}/casc/blp2png?fileDataID={fdid}"
        return out

    def files(self):
        """fdid -> (path, content_type, encrypted), from files.csv.

        Lazy: inventory.py writes ~1.4M rows, so this is only paid for by
        callers that actually resolve a file reference.
        """
        if self._files is not None:
            return self._files
        self._files = {}
        path = self.out_dir / "files.csv"
        header, rows = load_csv(path)
        if header:
            def col(n):
                return next((i for i, h in enumerate(header) if h.strip().lower() == n), None)
            fi, pi, ti, ei = col("fdid"), col("path"), col("content_type"), col("encrypted")
            if fi is not None:
                for r in rows:
                    enc = r[ei] if ei is not None and ei < len(r) else ""
                    self._files[r[fi]] = (
                        r[pi] if pi is not None and pi < len(r) else "",
                        r[ti] if ti is not None and ti < len(r) else "",
                        "" if enc in ("", "false", "False") else enc,
                    )
        return self._files

    # --- the general resolver ----------------------------------------------

    def resolve(self, table, row_id, columns=None):
        """Full human-readable context for one row.

        Returns, for every column (or the subset named in `columns`):
          raw          the CSV value, always present and always a string
          resolved     an FK target's label, when the column is a foreign key
          ref          the FK target as "Table::Column"
          decoded      enum/flag/colour decoding, when the column is mapped
          unresolved   True when something was expected but could not be found
          unassertable True when DBD marks the column unverified

        Unresolvable values are never dropped or guessed at -- `raw` stays and
        `unresolved` is set, so a report can print the bare number and say so.
        """
        row, variant = self.row_any(table, row_id)
        info = self.header_info(table)
        unverified = self.unverified(table)

        out = {
            "table": table,
            "id": row_id,
            "found": row is not None,
            "variant": variant,
            "label": self.label(table, row_id),
            "fields": {},
            "unassertable": sorted(unverified),
            "metadata_source": info["source"],
            "notes": [],
        }

        if info["source"] == "fallback":
            out["notes"].append(
                f"column metadata unavailable ({self.wtl.reason}); "
                "unverified-column marking is incomplete and FKs use the static fallback map"
            )
        if not self.mappings():
            out["notes"].append(
                "enum/flag mappings unavailable; numeric values are not decoded"
            )

        if row is None:
            out["notes"].append(f"no row {row_id} in {table} in either variant")
            return out

        header, _ = self.table(table, variant)
        wanted = {c.lower() for c in columns} if columns else None

        # Full row context, even when `columns` narrows the output: a gated
        # enum such as Item::SubclassID needs Item::ClassID to decode, and the
        # caller may not have asked for the gate column.
        context = {n: (row[i] if i < len(row) else "") for i, n in enumerate(header or [])}

        for i, name in enumerate(header or []):
            if wanted is not None and name.lower() not in wanted:
                continue
            raw = row[i] if i < len(row) else ""
            base, _arr = _base_column(name)

            field = {
                "raw": raw,
                "unassertable": name.lower() in unverified or base in unverified,
            }

            ref = info["fks"].get(name) or FALLBACK_FKS.get(base)
            if ref and raw not in ("", "0", "-1"):
                field["ref"] = ref
                target = ref.split("::", 1)[0]
                if target.lower() in ("filedata", "filedataid"):
                    # Not a real DB2 -- these are FDIDs into CASC. Hand the
                    # report what it needs to render the texture instead.
                    field.update(self.file_ref(raw))
                else:
                    lbl = self.label(target, raw)
                    field["resolved"] = lbl
                    field["unresolved"] = lbl is None

            decoded = self.decode(table, name, raw, context)
            if decoded is not None:
                field["decoded"] = decoded
                if decoded.get("unresolved"):
                    field["unresolved"] = True

            out["fields"][name] = field

        return out


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


# --- module-level convenience ----------------------------------------------

_instances = {}


def get(build=None, **kwargs):
    """Memoised Enricher per build. The normal entry point for other scripts."""
    key = (build, tuple(sorted(kwargs.items())))
    if key not in _instances:
        _instances[key] = Enricher(build, **kwargs)
    return _instances[key]


# --- CLI --------------------------------------------------------------------


def _self_test(e):
    """Exercise every resolver path and print what each returned.

    Not assertions -- the point is to show a human what the joins produce so a
    wrong label is visible rather than silently shipped into a report.
    """
    e.wtl.probe()
    print(f"build            {e.build}")
    print(f"WTL              {e.wtl.reason}"
          + (f" ({e.wtl.build_name})" if e.wtl.build_name else ""))
    print(f"enum mappings    {len(e.mappings())} unconditional, "
          f"{sum(len(v) for v in e.conditional_mappings().values())} conditional "
          f"across {len(e.conditional_mappings())} columns")
    print()

    print("-- labels " + "-" * 58)
    for table, ident in [
        ("Achievement", 9275), ("Map", 0), ("AreaTable", 1),
        ("SpellName", 133), ("Spell", 133),
        ("Light", 16161), ("LightParams", 453), ("LightParams", 7641),
        ("QuestV2", 1),
    ]:
        lbl = e.label(table, ident)
        print(f"  {table + ' ' + str(ident):<24} {lbl if lbl is not None else '<unresolved>'}")

    print()
    print("-- items " + "-" * 59)
    for ident in (25, 19019, 720, 286554, 99999999):
        r = e.item(ident)
        print(f"  {ident:<10} source={r['source']:<11} shipped={str(r['shipped']):<5} "
              f"hotfix_only={str(r['hotfix_only']):<5} icon={r['icon_fdid']} "
              f"{r['name'] if r['name'] else '<unresolved>'}")
        for n in r["notes"]:
            print(f"             note: {n}")

    print()
    print("-- decode " + "-" * 58)
    for table, col, val, ctx in [
        ("Achievement", "Flags", 1, None),
        ("Weather", "Type", 0, None),
        ("Map", "InstanceType", 1, None),
        ("LightData", "AmbientColor", 13533183, None),
        ("Item", "SubclassID", 0, None),                  # gated, no context
        ("Item", "SubclassID", 0, {"ClassID": "4"}),      # the 75 stubs
        ("Item", "SubclassID", 0, {"ClassID": "2"}),      # same value, weapons
    ]:
        label = f"{table}::{col} = {val}" + (f" ctx={ctx}" if ctx else "")
        print(f"  {label}")
        print(f"      -> {e.decode(table, col, val, ctx)}")

    print()
    print("-- resolve Achievement 9275 " + "-" * 40)
    r = e.resolve("Achievement", 9275)
    print(f"  found={r['found']} variant={r['variant']} label={r['label']!r}")
    print(f"  metadata_source={r['metadata_source']} unassertable={r['unassertable']}")
    for n in r["notes"]:
        print(f"  note: {n}")
    for name, f in r["fields"].items():
        if f.get("ref") or f.get("decoded") or f.get("unassertable"):
            print(f"    {name:<28} {f}")


def main(argv=None):
    # Labels carry em dashes and non-ASCII item names; a cp1252 console would
    # mangle or crash on them.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("table", nargs="?", help="table name, e.g. Achievement")
    ap.add_argument("id", nargs="?", help="row ID")
    ap.add_argument("--build", help="build to read; defaults to the newest under out/")
    ap.add_argument("--no-wtl", action="store_true", help="CSV joins only, no HTTP")
    ap.add_argument("--no-cache", action="store_true", help="ignore the on-disk cache")
    ap.add_argument("--self-test", action="store_true", help="exercise every resolver")
    args = ap.parse_args(argv)

    e = Enricher(args.build, use_wtl=not args.no_wtl, use_cache=not args.no_cache)

    if args.self_test or not args.table:
        _self_test(e)
        e.flush()
        return 0

    if args.id is None:
        ap.error("an ID is required with a table name")

    if args.table.lower() == "item":
        result = e.item(args.id)
    else:
        result = e.resolve(args.table, args.id)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    e.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
