"""Sim-side constants: proto enum indices, slots, weight deltas, budgets."""

# proto.ItemSlot order; job "slot" fields and Gear.items use these indices.
SLOTS = ["head", "neck", "shoulder", "back", "chest", "wrist", "hands", "waist",
         "legs", "feet", "finger1", "finger2", "trinket1", "trinket2",
         "main_hand", "off_hand", "ranged"]
SLOT_LABELS = ["Head", "Neck", "Shoulder", "Back", "Chest", "Wrist", "Hands",
               "Waist", "Legs", "Feet", "Ring 1", "Ring 2", "Trinket 1",
               "Trinket 2", "Main hand", "Off hand", "Ranged"]
SLOT_INDEX = {n: i for i, n in enumerate(SLOTS)}
SLOT_ALIASES = {"ring1": 10, "ring2": 11, "finger 1": 10, "finger 2": 11,
                "trinket 1": 12, "trinket 2": 13, "mainhand": 14, "mh": 14,
                "offhand": 15, "oh": 15, "shield": 15, "libram": 16,
                "relic": 16, "ring 1": 10, "ring 2": 11, "main hand": 14,
                "off hand": 15}
PAIRED = {10: 11, 11: 10, 12: 13, 13: 12}
# Slots whose item type carries an armorType (cloaks are excluded: all cloth).
ARMOR_SLOTS = {0, 2, 4, 5, 6, 7, 8, 9}

# db.json item "type" (proto.ItemType) -> slots it can fill.
ITEM_TYPE_SLOTS = {1: [0], 2: [1], 3: [2], 4: [3], 5: [4], 6: [5], 7: [6],
                   8: [7], 9: [8], 10: [9], 11: [10, 11], 12: [12, 13]}
# Slot -> enchant "type" in db.json, for naming an effectId by the slot it is
# on (one effectId can serve several slots under different names).
SLOT_ENCHANT_TYPE = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 9,
                     9: 10, 10: 11, 11: 11, 14: 13, 15: 13, 16: 14}
ENCHANT_TYPE_SHIELD = 2   # enchants[].enchantType for shield enchants

# proto.Stat
STAT_NAMES = {
    0: "Str", 1: "Agi", 2: "Sta", 3: "Int", 4: "HealPow", 5: "SP",
    6: "ArcaneSP", 7: "FireSP", 8: "FrostSP", 9: "HolySP", 10: "NatureSP",
    11: "ShadowSP", 12: "SpellPen", 13: "Spirit", 14: "AP", 15: "RAP",
    16: "FeralAP", 17: "Hit", 18: "Crit", 19: "Haste", 20: "ArPen",
    21: "Exp", 22: "Def", 23: "BlockR", 24: "BlockV", 25: "Dodge",
    26: "Parry", 27: "Armor", 28: "BonusArmor", 29: "HP", 30: "Mana",
    31: "MP5", 32: "ArcaneRes", 33: "FireRes", 34: "FrostRes",
    35: "NatureRes", 36: "ShadowRes", 37: "PhysDmg", 38: "ArmorNoMods",
}
STAT_INDEX = {v.lower(): k for k, v in STAT_NAMES.items()}
STAT_HP, STAT_ARMOR, STAT_DEF = 29, 27, 22

# Stats measured by stat_weights, with the bonus added per measurement. The
# deltas are large enough to clear paired-sim noise and small enough to stay
# local (a weight is the slope at the gear it was measured on).
WEIGHT_DELTAS = {0: 30, 1: 30, 2: 30, 3: 30, 5: 30, 9: 30, 13: 30, 14: 60,
                 17: 10, 18: 14, 21: 10, 22: 10, 23: 10, 24: 30, 25: 12,
                 26: 15, 27: 300, 31: 10}

METRICS = ("Dps", "Tps", "Dtps", "Tmi")
LOWER_BETTER = {"Dtps", "Tmi"}

# One flag sets every optimizer knob. Times are measured on the machine the
# acceptance run used; see docs/wowsim.md.
BUDGETS = {
    "quick":    {"climb_iters": 1000, "passes": 2, "armor_cands": 4, "screen_top": 5,
                 "screen_iters": 1000, "ench_iters": 2000, "final_iters": 10000,
                 "weight_iters": 2000, "post_ench_passes": 0},
    "standard": {"climb_iters": 1500, "passes": 3, "armor_cands": 6, "screen_top": 7,
                 "screen_iters": 1500, "ench_iters": 4000, "final_iters": 20000,
                 "weight_iters": 4000, "post_ench_passes": 1},
    "thorough": {"climb_iters": 2500, "passes": 4, "armor_cands": 8, "screen_top": 10,
                 "screen_iters": 2500, "ench_iters": 4000, "final_iters": 20000,
                 "weight_iters": 8000, "post_ench_passes": 1},
}

# Encounter presets: overrides applied to the base request's first target.
# "boss" is the preset's own raid boss (lvl 63, physical melee).
ENCOUNTERS = {
    "boss": {},
    "trash62": {"tlevel": 62, "tmindmg": 900, "thealth": 300000, "duration": 60},
}

SPELL_SCHOOLS = {"physical": "SpellSchoolPhysical", "arcane": "SpellSchoolArcane",
                 "fire": "SpellSchoolFire", "frost": "SpellSchoolFrost",
                 "holy": "SpellSchoolHoly", "nature": "SpellSchoolNature",
                 "shadow": "SpellSchoolShadow"}

# Default seeds. Every comparison uses one seed for all of its jobs (common
# random numbers), so differences are paired.
#
# The sim seeds iteration i with RandomSeed + i (Simulation.reseedRands), so
# seeds 77 and 78 share all but one iteration stream and are not independent
# samples. A second, independent seed must sit more than the run's iteration
# count away: use seed + k * SEED_STRIDE.
SEED_STRIDE = 10_000_019
SEED_CLIMB, SEED_SCREEN, SEED_ENCH, SEED_WEIGHTS = 101, 5, 202, 11
SEED_FINAL = 90_000_001      # clear of every climb/screen/enchant seed's iterations
SEED_COMPARE = 77
