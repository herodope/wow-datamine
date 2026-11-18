"""The sim's item database (vendor/forever/assets/database/db.json).

Names come from the sim's own database, not wow.db: the sim models exactly the
items in this file, and an id it does not hold cannot be simmed whatever the
client ships.

Known quirks of the data, which the rules and lookups below account for:
  - factionRestriction is often unset. Enemy-faction PvP and rep items need
    name, vendor and quest-faction filters (rules.py).
  - Class quest rewards are not class-restricted (Voodoo Feathers rewards Fury
    Visor / Razorsteel Shoulders); rule profiles keep a quest-id blocklist.
  - An enchant effectId can appear under several slots with different names
    (929 is the bracer, boot and shield Greater Stamina). Name it by slot.
  - Some vendors list a Horde and an Alliance NPC (Soulforge: Mokvar /
    Deliana). sources() shows the player's faction's vendor.
"""

import hashlib
import json
from functools import lru_cache

import config
from .consts import ENCHANT_TYPE_SHIELD, SLOT_ENCHANT_TYPE

FACTION_CODE = {"alliance": 1, "horde": 2}


class SimDB:
    def __init__(self, path=config.SIM_DB_JSON):
        raw = path.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        d = json.loads(raw)
        self.items = {i["id"]: i for i in d["items"]}
        self.zones = {z["id"]: z["name"] for z in d["zones"]}
        self.npcs = {n["id"]: n.get("name") for n in d["npcs"]}
        self.factions = {f["id"]: f["name"] for f in d.get("factions", [])}
        self.enchants = {}
        for e in d["enchants"]:
            self.enchants.setdefault(e["effectId"], []).append(e)
        self.sets = {}
        for i in d["items"]:
            if i.get("setId"):
                self.sets.setdefault(i["setId"], []).append(i)
        self._by_name = {}
        for i in d["items"]:
            self._by_name.setdefault(i["name"].lower(), []).append(i)

    # --- items ---------------------------------------------------------------

    def item(self, iid):
        return self.items.get(int(iid))

    def name(self, iid):
        it = self.item(iid)
        return it["name"] if it else f"item {iid}"

    def ilvl(self, iid):
        it = self.item(iid) or {}
        return it.get("scalingOptions", {}).get("0", {}).get("ilvl")

    def limcat(self, iid):
        return (self.item(iid) or {}).get("limitCategory")

    def unique(self, iid):
        return bool((self.item(iid) or {}).get("unique"))

    def find(self, query, limit=10):
        """Items matching a name or id. Exact name first, then substring."""
        q = str(query).strip()
        if q.isdigit():
            it = self.item(int(q))
            return [it] if it else []
        exact = self._by_name.get(q.lower(), [])
        if exact:
            return sorted(exact, key=lambda i: -(self.ilvl(i["id"]) or 0))
        ql = q.lower()
        subs = [i for n, lst in self._by_name.items() if ql in n for i in lst]
        subs.sort(key=lambda i: (len(i["name"]), -(self.ilvl(i["id"]) or 0)))
        return subs[:limit]

    def resolve(self, query):
        """One item id for a name or id, or raise ValueError naming the options."""
        hits = self.find(query)
        if not hits:
            raise ValueError(f"no item matches {query!r} in the sim's database")
        names = {h["name"].lower() for h in hits}
        if len(names) > 1 and str(query).lower() not in names:
            opts = "; ".join(f"{h['name']} ({h['id']})" for h in hits[:6])
            raise ValueError(f"{query!r} is ambiguous: {opts}")
        return hits[0]["id"]

    # --- enchants ------------------------------------------------------------

    def enchant_name(self, effect_id, slot):
        """The enchant's name as it reads on this slot."""
        if not effect_id:
            return ""
        cands = self.enchants.get(int(effect_id), [])
        want = SLOT_ENCHANT_TYPE.get(slot)
        for e in cands:
            types = [e.get("type")] + list(e.get("extraTypes") or [])
            if want not in types:
                continue
            shield = e.get("enchantType") == ENCHANT_TYPE_SHIELD
            if want == 13 and shield != (slot == 15):
                continue
            return e["name"]
        return cands[0]["name"] if cands else f"enchant {effect_id}"

    # --- sources -------------------------------------------------------------

    def source_tags(self, it):
        """Machine-matchable source strings, one per source (rules.py reads these)."""
        out = []
        for s in it.get("sources") or []:
            k, v = next(iter(s.items()))
            if k == "drop":
                z = self.zones.get(v.get("zoneId"), "World")
                n = self.npcs.get(v.get("npcId")) or v.get("otherName", "")
                out.append(f"drop:{z}:{n}")
            elif k == "quest":
                out.append(f"quest:{v.get('name')}")
            elif k == "soldBy":
                out.append(f"vendor:{v.get('npcName')}:{self.zones.get(v.get('zoneId'), '')}")
            elif k == "crafted":
                out.append(f"crafted:prof{v.get('profession')}")
            elif k == "rep":
                out.append(f"rep:{v.get('repFactionId')}:{v.get('repLevel')}")
            elif k == "pvp":
                out.append("pvp")
        return out

    def sources(self, iid, faction="horde", limit=2):
        """Human-readable sources, the player's faction's vendor and quests only."""
        it = self.item(iid)
        if not it:
            return "not in the sim's database"
        enemy = 3 - FACTION_CODE.get(faction, 2)
        out = []
        for s in it.get("sources") or []:
            k, v = next(iter(s.items()))
            if v.get("faction") == enemy or v.get("factionId") == enemy:
                continue
            if k == "drop":
                z = self.zones.get(v.get("zoneId"))
                n = (self.npcs.get(v.get("npcId")) or v.get("otherName") or "").strip()
                out.append(f"{z} – {n}" if z and n else (z or "World drop"))
            elif k == "quest":
                out.append(f"Quest: {v.get('name')}")
            elif k == "soldBy":
                z = self.zones.get(v.get("zoneId"), "")
                out.append(f"Vendor: {v.get('npcName')}" + (f" ({z})" if z else ""))
            elif k == "crafted":
                out.append({2: "Blacksmithing", 1: "Leatherworking", 3: "Engineering",
                            8: "Tailoring", 7: "Enchanting"}.get(v.get("profession"), "Crafted"))
            elif k == "rep":
                fac = self.factions.get(v.get("repFactionId"), f"faction {v.get('repFactionId')}")
                lvl = {5: "Friendly", 6: "Honored", 7: "Revered", 8: "Exalted"}.get(v.get("repLevel"), "")
                out.append(f"{fac} {lvl}".strip())
            elif k == "pvp":
                out.append("PvP")
        out = list(dict.fromkeys(out))
        return "; ".join(out[:limit]) or "source unknown"


@lru_cache(maxsize=1)
def db():
    return SimDB()
