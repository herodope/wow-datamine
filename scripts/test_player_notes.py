#!/usr/bin/env python3
"""Unit tests for player_notes: provenance, direction and display.

Run:  python scripts/test_player_notes.py
"""
import unittest

import player_notes as p


class Provenance(unittest.TestCase):
    def test_nothing_moved(self):
        self.assertIsNone(p.provenance(1, 1, 1, 1))

    def test_shipped_in_client(self):
        self.assertEqual(p.provenance(20, 20, 30, 30), (p.CLIENT, 20, 30))

    def test_live_hotfix_on_new_build(self):
        self.assertEqual(p.provenance(20, 20, 20, 30), (p.HOTFIX, 20, 30))

    def test_old_hotfix_folded_into_client(self):
        # 70291: the 70170 warrior pass shipped in the client byte for byte.
        self.assertEqual(p.provenance(35, 45, 45, 45), (p.FOLDED, 35, 45))

    def test_hotfix_lapses_with_new_build(self):
        # A live value that reverts is a change in game, judged live to live.
        self.assertEqual(p.provenance(35, 45, 35, 35), (p.CLIENT, 45, 35))

    def test_unmeasured_overlay_reads_as_client(self):
        self.assertEqual(p.provenance(1, 1, 2, 2)[0], p.CLIENT)


class Direction(unittest.TestCase):
    def test_magnitude(self):
        self.assertEqual(p._direction("EffectBasePointsF", 20, 30), 1)
        self.assertEqual(p._direction("EffectBasePointsF", -140, -280), 1)   # Disengage
        self.assertEqual(p._direction("talent_value", 30, 20), -1)           # Redoubt

    def test_smaller_is_better(self):
        self.assertEqual(p._direction("ManaCost", 100, 150), -1)             # Penance
        self.assertEqual(p._direction("BaseLevel", 40, 25), 1)               # Mana Tide Totem

    def test_no_direction(self):
        self.assertEqual(p._direction("EffectAura", 22, 674), 0)
        self.assertEqual(p._direction("Attributes_3", 0, 131072), 0)
        self.assertEqual(p._direction("EffectBasePointsF", None, 3), 0)


class Display(unittest.TestCase):
    def test_ms(self):
        self.assertEqual(p._fmt_ms(15000), "15 sec")
        self.assertEqual(p._fmt_ms(600000), "10 min")
        self.assertEqual(p._fmt_ms(0), "none")

    def test_talent_value_fallback_text(self):
        self.assertEqual(p._display("talent_value", "spell value -3000", None),
                         ("spell value -3000", None))
        self.assertEqual(p._display("talent_value", "2/4/6", None), ("2/4/6", 6.0))

    def test_technical_labels(self):
        self.assertTrue(p._technical("Attributes_4"))
        self.assertTrue(p._technical("EffectSpellClassMask_0"))
        self.assertIsNone(p._technical("ManaCost"))


if __name__ == "__main__":
    unittest.main()
