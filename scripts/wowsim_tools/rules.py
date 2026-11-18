"""Exclusion-rule profiles: config/rules/<name>.json.

A profile says which items a player will accept. The optimizer only proposes
allowed items, and every result names its profile and the profile's hash, so
a finding made under one set of rules is never quoted under another.

Keys (all optional except faction):
  faction                    horde | alliance. Enemy-faction quest, vendor and
                             rep sources do not count as a way to get an item.
  race                       default proto.Race for sims under this profile.
  armor_slots_plate_only     armor slots need armorType 4.
  exclude_raid_zones         drop sources in these zone ids do not count.
  exclude_drop_difficulties  drop sources of these difficulties do not count.
  exclude_rep_factions       rep sources of these factions do not count.
  exclude_limit_categories   items in these limit categories are excluded.
  exclude_name_patterns      items whose name contains any are excluded.
  exclude_alliance_patterns  same, for enemy-faction PvP and rep names
                             (factionRestriction is often unset in db.json).
  exclude_premier_epic_pvp   {name_prefix, min_ilvl}.
  exclude_epic_bg_rep        epics from BG vendors / sources / name prefixes.
  exclude_darkspear_epics    {rep_faction, min_quality}.
  exclude_world_boe_epics    epics that are world drops, or have no source and
                             an id below max_unsourced_id (classic BoE ids).
  exclude_quest_ids          items rewarded by these quests are excluded
                             (class quest rewards are not class-restricted).
  exclude_item_ids           explicit blocklist.
  exclude_epic_crafts        crafted sources of epics do not count.
  exclude_random_suffix      random-suffix variants are excluded.
  allow_unknown_source       an item with no sources at all is allowed.

An item is allowed when at least one of its sources counts (or it has none and
allow_unknown_source is set) and no item-level exclusion matches.
"""

import hashlib
import json
import re

import config
from .consts import ARMOR_SLOTS
from .simdb import FACTION_CODE, db


class RulesError(ValueError):
    pass


def list_profiles():
    return sorted(p.stem for p in config.RULES_DIR.glob("*.json"))


class Rules:
    def __init__(self, name):
        path = config.RULES_DIR / f"{name}.json"
        if not path.exists():
            raise RulesError(f"no rules profile {name!r}; known: {', '.join(list_profiles())}")
        self.name = name
        self.data = json.loads(path.read_text(encoding="utf-8"))
        canon = json.dumps(self.data, sort_keys=True, separators=(",", ":"))
        self.sha256 = hashlib.sha256(canon.encode()).hexdigest()
        r = self.data
        faction = r.get("faction", "horde")
        if faction not in FACTION_CODE:
            raise RulesError(f"{name}: faction must be horde or alliance")
        self.faction = faction
        self.enemy = 3 - FACTION_CODE[faction]
        self.race = r.get("race", "RaceUndead")
        pats = r.get("exclude_alliance_patterns") or []
        self._enemy_rx = re.compile("|".join(map(re.escape, pats))) if pats else None
        self._quest_items = None

    def describe(self):
        return f"{self.name} ({self.sha256[:8]})"

    # --- source level ----------------------------------------------------

    def _source_counts(self, it, src):
        k, v = next(iter(src.items()))
        r = self.data
        if v.get("faction") == self.enemy or v.get("factionId") == self.enemy:
            return False
        if k == "drop":
            if v.get("zoneId") in (r.get("exclude_raid_zones") or []):
                return False
            if v.get("difficulty") in (r.get("exclude_drop_difficulties") or []):
                return False
        if k == "crafted" and r.get("exclude_epic_crafts") and it["quality"] >= 4:
            return False
        if k == "rep" and v.get("repFactionId") in (r.get("exclude_rep_factions") or []):
            return False
        if k == "pvp" and self._enemy_name(it["name"]):
            return False
        return True

    def _enemy_name(self, name):
        return bool(self._enemy_rx and self._enemy_rx.search(name))

    def ok_source(self, it):
        ss = it.get("sources") or []
        if not ss:
            return bool(self.data.get("allow_unknown_source", True))
        return any(self._source_counts(it, s) for s in ss)

    # --- item level ------------------------------------------------------

    def _quest_blocked(self):
        if self._quest_items is None:
            qids = set(self.data.get("exclude_quest_ids") or [])
            self._quest_items = {
                i["id"] for i in db().items.values()
                for s in i.get("sources") or []
                if "quest" in s and s["quest"].get("id") in qids}
        return self._quest_items

    def excluded_reason(self, cand, t05=True, t05_set=None):
        """Why a pool candidate is not allowed, or None if it is.

        cand is a pool entry: id, name, suffix, slots, q, ilvl, setId, src,
        armorType.
        """
        r = self.data
        it = db().item(cand["id"]) or {}
        name = cand["name"]
        if r.get("exclude_random_suffix") and cand.get("suffix"):
            return "random suffix"
        if cand["id"] in (r.get("exclude_item_ids") or []):
            return "item blocklist"
        if it.get("factionRestriction") == self.enemy:
            return "enemy faction item"
        if self._enemy_name(name):
            return "enemy faction name"
        for p in r.get("exclude_name_patterns") or []:
            if p in name:
                return f"name pattern {p!r}"
        if (it.get("limitCategory") or None) in (r.get("exclude_limit_categories") or []):
            return f"limit category {it.get('limitCategory')}"
        if cand["id"] in self._quest_blocked():
            return "quest blocklist"
        pp = r.get("exclude_premier_epic_pvp")
        if pp and name.startswith(pp.get("name_prefix", "Premier")) and (cand.get("ilvl") or 0) >= pp.get("min_ilvl", 65):
            return "premier epic PvP"
        dk = r.get("exclude_darkspear_epics")
        if dk and cand["q"] >= dk.get("min_quality", 4) and any(
                "rep" in s and s["rep"].get("repFactionId") == dk.get("rep_faction", 2798)
                for s in it.get("sources") or []):
            return "Darkspear epic"
        if cand["q"] >= 4:
            src = cand.get("src") or []
            wb = r.get("exclude_world_boe_epics")
            if wb and ((not src and cand["id"] < wb.get("max_unsourced_id", 200000))
                       or any(s.startswith("drop:World") for s in src)):
                return "world BoE epic"
            bg = r.get("exclude_epic_bg_rep")
            if bg:
                if any(s.startswith("pvp")
                       or any(x in s for x in bg.get("source_substrings") or [])
                       or any(v in s for v in bg.get("vendors") or []) for s in src):
                    return "epic BG/PvP reward"
                if name.startswith(tuple(bg.get("name_prefixes") or ())):
                    return "epic BG rep reward"
        if not t05 and t05_set and cand.get("setId") == t05_set:
            return "T0.5 off"
        if r.get("armor_slots_plate_only") and cand["slots"][0] in ARMOR_SLOTS and cand.get("armorType") != 4:
            return "not plate"
        if not self.ok_source(it):
            return "no allowed source"
        return None

    def allowed(self, cand, t05=True, t05_set=None):
        return self.excluded_reason(cand, t05, t05_set) is None
