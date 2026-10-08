#!/usr/bin/env python3
"""Checks for spell_reach.py against the newest extracted build's wow.db.

Stdlib unittest, like the rest of the pipeline:

    python scripts/test_spell_reach.py

The expected IDs were measured on 1.60.1.70170. Skipped, not failed, when no
wow.db exists -- a missing extraction is not a regression.
"""

import unittest

import sod_tags
import spell_reach

BUILD = sod_tags._newest_db()
CONN = sod_tags.open_ro(BUILD) if BUILD else None
TAGS = None
RESULT = None
if CONN is not None:
    _sod = sod_tags.detect(CONN, BUILD)
    TAGS = {s: (r["tier"], bool(r["live_in_forever"])) for s, r in _sod["tags"].items()}
    RESULT = spell_reach.compute(CONN, TAGS, BUILD)


def root(spell_id):
    r = RESULT["reach"].get(spell_id)
    return r["root_kind"] if r else None


@unittest.skipIf(RESULT is None, "no wow.db extracted")
class SpellReach(unittest.TestCase):

    def test_every_root_scanned(self):
        for c in RESULT["coverage"]:
            self.assertEqual(c["status"], "scanned", c)

    def test_trainer_spells_reachable(self):
        for s in (781, 20925, 24597, 8170):     # Disengage, Holy Shield, Furious Howl,
            with self.subTest(spell=s):         # Disease Cleansing Totem
                self.assertEqual(root(s), "skill_line")

    def test_talent_tree_roots(self):
        self.assertEqual(RESULT["reach"][400624]["root_id"], 1112)   # Heating Up, Mage
        self.assertEqual(RESULT["reach"][1322605]["root_id"], 1089)  # Shifting Power, Druid
        for s in (400624, 1322605):
            self.assertEqual(root(s), "talent_tree")

    def test_live_trees(self):
        trees = spell_reach.live_trait_trees(CONN)
        for t in (1082, 1089, 1091, 1100, 1111, 1112, 1114, 1116, 1117, 1187, 1188, 1189):
            with self.subTest(tree=t):
                self.assertIn(t, trees)
        for t in (1058, 1066, 1081, 1083):      # unlinked on the class system, or no system
            with self.subTest(tree=t):
                self.assertNotIn(t, trees)

    def test_enchant_edge(self):
        # Revelation: Enchanting recipe -> enchant -> the proc spell
        r = RESULT["reach"][1248806]
        self.assertEqual((r["root_kind"], r["via_edge"], r["via_spell"]),
                         ("skill_line", "enchant", 1248805))

    def test_class_passive_root(self):
        # Rule of Rage (DND): warrior crit rage on 95 Defense. AcquireMethod 2
        # (a skill_line root) at 70170, 3 from 70291 on.
        self.assertIn(root(1322574), ("skill_line", "class_passive"))
        self.assertEqual(RESULT["reach"][1322574]["root_id"], 95)

    def test_class_passive_stays_narrow(self):
        # AM 3 with a single-class mask on any line is 262 spells at 70291;
        # this root must not open that door. Tiger's Fury was removed at 70170.
        hits = next(c["hits"] for c in RESULT["coverage"] if c["root"] == "class_passive")
        self.assertLessEqual(hits, 5)
        self.assertIsNone(root(5217))

    def test_engraving_is_not_a_root(self):
        self.assertIsNone(root(400102))         # Engrave Pants - Envenom

    def test_unreachable(self):
        # Reported as class changes at 70170, none on any player path.
        for s in (1300361, 1289450, 1242853, 422978, 1323420):   # Starfall, Renew,
            with self.subTest(spell=s):                           # Soul Harvest, Coward!,
                self.assertIsNone(root(s))                        # Totemic Recall

    def test_sod_items_excluded(self):
        graph = spell_reach._Graph(CONN)
        items = spell_reach.sod_items(CONN, graph, TAGS)
        for it in (210979, 208853):             # Rune of Shadowstep, Spell Notes: Brain Freeze
            with self.subTest(item=it):
                self.assertIn(it, items)
        self.assertIn(208754, items)            # Spell Notes: TENGI RONEERA, second hop
        self.assertNotIn(6953, items)           # Verigan's Fist: live, carries Holy Forgefire
        self.assertEqual(root(1322218), "item")

    def test_without_tags_items_leak(self):
        # No SoD-item exclusion -> coverage must say so, not pretend.
        r = spell_reach.compute(CONN, None, BUILD)
        item = next(c for c in r["coverage"] if c["root"] == "item")
        self.assertEqual(item["status"], "not_scanned")


if __name__ == "__main__":
    unittest.main(verbosity=2)
