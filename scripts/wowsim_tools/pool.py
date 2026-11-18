"""Candidate item pool for one spec, with each item's exact stat vector.

The pool holds every item the spec can equip at all (phase, level, class,
slot, weapon type, minimum ilvl and quality). It is rule-independent: rules.py
filters it per profile at use time.

Each entry's "st" is its ComputeStats GearStats with only that item equipped,
minus the empty-gear GearStats. GearStats includes base stats and imbues, so
the subtraction matters; a main hand still carries the preset's weapon oil
(+36 SP on the prot preset), which is the same for every main hand.

Cached at sim_out/pool/<spec>_<sim sha>_<db hash>.json; build_sim.py deletes
the cache when the sim sha or db changes.
"""

import json

import config
from . import harness
from .consts import ITEM_TYPE_SLOTS
from .simdb import db


def _slots_for(it, spec_pool):
    t = it.get("type")
    if t in ITEM_TYPE_SLOTS:
        if t in (1, 3, 5, 6, 7, 8, 9, 10) and it.get("armorType", 0) not in spec_pool["armor_types"]:
            return None
        return ITEM_TYPE_SLOTS[t]
    if t == 13:
        oh = spec_pool.get("off_hand") or {}
        mh = spec_pool.get("main_hand") or {}
        if it.get("weaponType") in oh.get("weapon_types", []):
            return [15]
        if it.get("handType") in mh.get("hand_types", []) and it.get("weaponType") in mh.get("weapon_types", []):
            return [14]
    if t == 14 and it.get("rangedWeaponType") in (spec_pool.get("ranged") or {}).get("ranged_weapon_types", []):
        return [16]
    return None


def candidates(spec):
    s = harness.load_specs()[spec]
    sp = s["pool"]
    d = db()
    out = []
    for it in d.items.values():
        if it["phase"] != sp["phase"] or it.get("reqLvl", 0) > sp["max_req_level"]:
            continue
        ca = it.get("classAllowlist")
        if ca and s["class"] not in ca:
            continue
        if it["quality"] < sp["min_quality"]:
            continue
        slots = _slots_for(it, sp)
        if not slots:
            continue
        ilvl = it["scalingOptions"].get("0", {}).get("ilvl", 0)
        if ilvl < sp["min_ilvl"]:
            continue
        for sfx in it.get("randomSuffixOptions") or [0]:
            out.append({"id": it["id"], "name": it["name"], "suffix": sfx, "slots": slots,
                        "q": it["quality"], "ilvl": ilvl, "set": it.get("setName"),
                        "setId": it.get("setId"), "unique": bool(it.get("unique")),
                        "limcat": it.get("limitCategory"),
                        "src": d.source_tags(it), "armorType": it.get("armorType")})
    return out


def cache_path(spec):
    info = harness.build_info()
    sha = (info.get("sim_sha") or "unbuilt")[:10]
    return config.SIM_POOL_DIR / f"{spec}_{sha}_{db().sha256[:10]}.json"


def load(spec, log=print):
    path = cache_path(spec)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    cands = candidates(spec)
    log(f"pool: computing stats for {len(cands)} candidates ({spec})")
    base = harness.preset_path(spec)
    jobs = [{"name": "empty", "full": True, "statsOnly": True}]
    jobs += [{"name": str(i), "full": True, "statsOnly": True,
              "items": [{"slot": c["slots"][0], "id": c["id"], "suffix": c["suffix"]}]}
             for i, c in enumerate(cands)]
    res = harness.run(jobs, base)
    emp = res["empty"]["Gear"]
    keep = []
    errs = 0
    for i, c in enumerate(cands):
        o = res[str(i)]
        if o["Err"]:
            errs += 1
            continue
        c["st"] = [round(a - b, 4) for a, b in zip(o["Gear"], emp)]
        keep.append(c)
    log(f"pool: {len(keep)} items, {errs} the sim could not equip")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keep), encoding="utf-8")
    return keep
