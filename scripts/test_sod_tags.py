#!/usr/bin/env python3
"""Checks for sod_tags.py against the newest extracted build's wow.db.

Stdlib unittest, like the rest of the pipeline:

    python scripts/test_sod_tags.py

The expected IDs were measured on 1.60.1.70009. Skipped, not failed, when no
wow.db exists -- a missing extraction is not a regression.
"""

import unittest

import sod_tags

BUILD = sod_tags._newest_db()
RESULT = sod_tags.detect(sod_tags.open_ro(BUILD), BUILD) if BUILD else None


def tier(spell_id):
    r = RESULT["tags"].get(spell_id)
    return r["tier"] if r else None


@unittest.skipIf(RESULT is None, "no wow.db extracted")
class SodTags(unittest.TestCase):

    def test_every_step_scanned(self):
        for c in RESULT["coverage"]:
            self.assertEqual(c["status"], "scanned", c)

    def test_chain_count_in_expected_range(self):
        self.assertTrue(262 <= RESULT["stats"]["chains"] <= 271, RESULT["stats"]["chains"])

    def test_sod_rune(self):
        for s in (410002, 409914, 410014, 409924, 407778, 410013, 409922, 407632):
            with self.subTest(spell=s):
                self.assertEqual(tier(s), sod_tags.RUNE)

    def test_target_missing(self):
        for s in (407676, 407669):
            with self.subTest(spell=s):
                r = RESULT["tags"].get(s)
                self.assertIsNotNone(r)
                self.assertTrue(r["target_missing"])
        # the wrapper that points at the missing target is still tagged
        self.assertEqual(tier(409914), sod_tags.RUNE)

    def test_sod_book_candidate(self):
        for s in (407798, 407788, 435984):
            with self.subTest(spell=s):
                self.assertEqual(tier(s), sod_tags.BOOK)
        self.assertTrue(RESULT["tags"][435984]["live_in_forever"])
        self.assertFalse(RESULT["tags"][407798]["live_in_forever"])

    def test_propagated(self):
        for s in (407799,):
            with self.subTest(spell=s):
                r = RESULT["tags"].get(s)
                self.assertIsNotNone(r)
                self.assertEqual(r["source_rule"], "propagation")
                self.assertNotEqual(r["tier"], sod_tags.FLAG)

    def test_seal_dummy_bp(self):
        # Seal of Martyrdom 407798 (book) names Judgement of Martyrdom 407803
        # in an aura-4 effect's base points.
        r = RESULT["tags"].get(407803)
        self.assertIsNotNone(r)
        self.assertEqual(r["tier"], sod_tags.BOOK)
        self.assertEqual(r["source_rule"], "seal_dummy_bp")

    def test_seal_dummy_bp_validation(self):
        land = sod_tags.seal_dummy_landings(RESULT["data"])
        name = RESULT["data"].name

        def judgement(s, t):
            return (name.get(s, "").startswith("Seal of")
                    and name[t] == "Judgement of " + name[s][len("Seal of "):])

        seals = [x for x in land if judgement(x[0], x[2])]
        other = [x for x in land if not judgement(x[0], x[2])]
        self.assertEqual(sum(1 for x in seals if x[3]), len(seals))   # 35/35
        self.assertEqual(sum(1 for x in other if x[3]), 0)            # 0/19

    def test_sod_variant(self):
        for s in (415068, 429145):
            with self.subTest(spell=s):
                self.assertEqual(tier(s), sod_tags.VARIANT)

    def test_manual(self):
        r = RESULT["tags"].get(429151)
        self.assertIsNotNone(r)
        self.assertEqual(r["tier"], sod_tags.FLAG)
        self.assertEqual(r["source_rule"], "manual")

    def test_sod_ported(self):
        pairs = dict(RESULT["stats"]["ported_pairs"])
        self.assertEqual(pairs.get(399956), 1241582)          # Mutilate
        self.assertEqual(tier(399956), sod_tags.PORTED)
        self.assertEqual(RESULT["tags"][399956]["sibling"], 1241582)
        for sod, sib in pairs.items():
            with self.subTest(sod=sod, sibling=sib):
                self.assertIsNone(tier(sib))
                self.assertNotIn(sib, RESULT["data"].reference_ids)
        # classic-sibling pairs stay variants
        for s in (415335, 468766, 428708):
            with self.subTest(spell=s):
                self.assertEqual(tier(s), sod_tags.VARIANT)

    def test_class_from_origin(self):
        self.assertEqual(RESULT["tags"][415076]["class"], "Paladin")   # Exorcist
        self.assertEqual(RESULT["tags"][415068]["class"], "Paladin")   # via Exorcist

    def test_must_be_untagged(self):
        base = {
            635, 25292,                          # Holy Light
            879, 10314,                          # Exorcism
            24239,                               # Hammer of Wrath
            2812,                                # Holy Wrath
            7328,                                # Redemption
            20271,                               # Judgement
            20154,                               # Seal of Righteousness
            20375,                               # Seal of Command
            19750,                               # Flash of Light
            # Holy Shock: the AcquireMethod-0 "Rank 1" trainer row at 70009 is
            # 1311606. 20473, the classic rank 1, is labelled "Rank 2" here.
            1311606, 20473,
            26573, 20116, 20922, 20923, 20924,   # Consecration ranks
            25771, 18350,                        # Forbearance, Dummy Trigger (guard)
            10, 100,                             # Blizzard, Charge (aura-4 collisions)
        }
        for s in sorted(base):
            with self.subTest(spell=s):
                self.assertIsNone(tier(s), RESULT["tags"].get(s))

    def test_old_crusader_strikes_not_rune(self):
        for s in (2537, 14517, 17281, 1319259):
            with self.subTest(spell=s):
                self.assertNotEqual(tier(s), sod_tags.RUNE)

    def test_no_variant_base_is_tagged(self):
        for sod, base, _how in RESULT["stats"]["variant_pairs"]:
            with self.subTest(sod=sod, base=base):
                self.assertIsNone(tier(base))

    def test_trait_tree_marks_live(self):
        # Measured at 70170: SoD runes that a linked Forever talent tree grants.
        # The tier stays -- SoD-derived, not cut.
        for s in (400624, 400647, 408498, 431622, 427712):   # Heating Up, Fingers of
            with self.subTest(spell=s):                       # Frost, Maelstrom Weapon,
                r = RESULT["tags"].get(s)                     # Divine Aegis, Pandemic
                self.assertIsNotNone(r)
                self.assertEqual(r["tier"], sod_tags.RUNE)
                self.assertTrue(r["live_in_forever"])
                self.assertEqual(r["live_source"], "trait_tree")

    def test_untreed_rune_stays_cut(self):
        for s in (410002, 409914):
            with self.subTest(spell=s):
                self.assertFalse(RESULT["tags"][s]["live_in_forever"])
                self.assertIsNone(RESULT["tags"][s]["live_source"])

    def test_forever_trainer_marks_live(self):
        # Trainer rows Forever added or changed relative to 1.15.9.
        for s in (408341, 408345, 402927, 407632):   # Fire Nova r1/r5, Victory Rush,
            with self.subTest(spell=s):               # Hammer of the Righteous
                r = RESULT["tags"][s]
                self.assertTrue(r["live_in_forever"])
                self.assertEqual(r["live_source"], "forever_trainer")

    def test_sod_identical_trainer_row_is_not_evidence(self):
        # Same AcquireMethod-0 row in the SoD client: leftover, still cut.
        for s in (415423, 401977, 438040):            # Aspect of the Viper,
            with self.subTest(spell=s):               # Shadowfiend, Redirect
                self.assertFalse(RESULT["tags"][s]["live_in_forever"])

    def test_allowlist_keeps_its_source(self):
        self.assertEqual(RESULT["tags"][435984]["live_source"], "allowlist")

    def test_enrich_view(self):
        v = sod_tags.tag_for(RESULT, 435984)
        self.assertEqual(v["tier"], sod_tags.BOOK)
        self.assertTrue(v["live_in_forever"])
        self.assertTrue(v["reasons"])
        self.assertEqual(sod_tags.tag_for(RESULT, 635)["tier"], None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
