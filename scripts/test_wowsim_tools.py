#!/usr/bin/env python3
"""Pins the wowsim rules, constraints and known sim quirks (stdlib unittest).

    python scripts/test_wowsim_tools.py

Tests that read the sim's item database skip when vendor/forever is missing;
tests that run the sim skip when bin/bisrun is not built.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from wowsim_tools import harness  # noqa: E402
from wowsim_tools.consts import BUDGETS, SEED_STRIDE  # noqa: E402
from wowsim_tools.objective import parse, parse_bool  # noqa: E402

HAVE_DB = config.SIM_DB_JSON.exists()
HAVE_SIM = harness.BISRUN.exists() and HAVE_DB

ACA = 272437            # Adaptive Combat Assistant: not unique, limit category 707
WEAKNESS = 272438       # Weakness Analyzer: limit category 707
HOTS = 13164            # Heart of the Scale: unique
SIGNET = 275968         # Initiate's Signet of Ferocity: limit category 710
FURY_VISOR = 20521      # warrior Sunken Temple quest reward
BAND = 284716           # Band of the Better Half: item blocklist
ELEMENTAL_FOCUS = 20682 # Elemental Focus Band: item blocklist
GREATHELM = 226976      # Soulforge Greathelm (T0.5)


class ObjectiveTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse("TPS").metric, "Tps")
        o = parse("dtps_min_hp:5000")
        self.assertEqual((o.metric, o.min_hp), ("Dtps", 5000.0))
        self.assertTrue(o.lower_better)

    def test_no_substring_matching(self):
        # The prototype's tag bug: 't05' in 'Tmi_not05' is True.
        with self.assertRaises(ValueError):
            parse("Tmi_not05")
        self.assertFalse(parse_bool("off", "t05"))
        with self.assertRaises(ValueError):
            parse_bool("not05", "t05")

    def test_scores(self):
        better, worse = {"Tmi": 40, "Dtps": 200, "Err": ""}, {"Tmi": 41, "Dtps": 190, "Err": ""}
        self.assertGreater(parse("tmi").score(better), parse("tmi").score(worse))
        self.assertGreater(parse("dtps").score(worse), parse("dtps").score(better))
        self.assertLess(parse("tps").score({"Err": "boom"}), -1e8)
        hp = parse("dtps_min_hp:5000")
        low = {"Dtps": 190, "Tmi": 40, "Err": "", "Final": [0] * 29 + [4000]}
        ok = {"Dtps": 195, "Tmi": 40, "Err": "", "Final": [0] * 29 + [5000]}
        self.assertGreater(hp.score(ok), hp.score(low))


class SeedTest(unittest.TestCase):
    def test_stride_clears_every_budget(self):
        # Iteration i is seeded RandomSeed + i; a second seed must not overlap.
        most = max(max(b.values()) for b in BUDGETS.values())
        self.assertGreater(SEED_STRIDE, 100 * most)


@unittest.skipUnless(HAVE_DB, "vendor/forever not checked out")
class SimDBTest(unittest.TestCase):
    def setUp(self):
        from wowsim_tools.simdb import db
        self.db = db()

    def test_enchant_named_by_slot(self):
        # One effectId, three slots, three names.
        self.assertIn("Bracer", self.db.enchant_name(929, 5))
        self.assertIn("Boots", self.db.enchant_name(929, 9))
        self.assertIn("Shield", self.db.enchant_name(929, 15))

    def test_faction_vendor(self):
        self.assertIn("Mokvar", self.db.sources(GREATHELM, "horde"))
        self.assertNotIn("Deliana", self.db.sources(GREATHELM, "horde"))
        self.assertIn("Deliana", self.db.sources(GREATHELM, "alliance"))

    def test_resolve(self):
        self.assertEqual(self.db.resolve("Heart of the Scale"), HOTS)
        self.assertEqual(self.db.resolve(str(HOTS)), HOTS)
        with self.assertRaises(ValueError):
            self.db.resolve("no such item zzz")


@unittest.skipUnless(HAVE_DB, "vendor/forever not checked out")
class RulesTest(unittest.TestCase):
    def setUp(self):
        from wowsim_tools.rules import Rules
        from wowsim_tools.simdb import db
        self.r = Rules("prot_v3")
        self.db = db()

    def cand(self, iid, suffix=0):
        it = self.db.item(iid)
        from wowsim_tools.consts import ITEM_TYPE_SLOTS
        return {"id": iid, "name": it["name"], "suffix": suffix,
                "slots": ITEM_TYPE_SLOTS.get(it.get("type"), [14]), "q": it["quality"],
                "ilvl": self.db.ilvl(iid), "setId": it.get("setId"),
                "src": self.db.source_tags(it), "armorType": it.get("armorType")}

    def test_exclusions(self):
        self.assertEqual(self.r.excluded_reason(self.cand(BAND)), "item blocklist")
        self.assertEqual(self.r.excluded_reason(self.cand(ELEMENTAL_FOCUS)), "item blocklist")
        self.assertEqual(self.r.excluded_reason(self.cand(SIGNET)), "limit category 710")
        self.assertEqual(self.r.excluded_reason(self.cand(FURY_VISOR)), "quest blocklist")
        self.assertEqual(self.r.excluded_reason(self.cand(GREATHELM), t05=False, t05_set=1673),
                         "T0.5 off")
        self.assertEqual(self.r.excluded_reason(self.cand(HOTS, suffix=5)), "random suffix")

    def test_allowed(self):
        for iid in (HOTS, ACA, GREATHELM):
            self.assertIsNone(self.r.excluded_reason(self.cand(iid), t05=True, t05_set=1673), iid)

    def test_hash_is_stable(self):
        from wowsim_tools.rules import Rules
        self.assertEqual(Rules("prot_v3").sha256, self.r.sha256)


@unittest.skipUnless(HAVE_DB, "vendor/forever not checked out")
class ClashTest(unittest.TestCase):
    def test_limit_category_and_unique(self):
        from wowsim_tools.optimize import Context
        self.assertTrue(Context.clash(ACA, ACA))          # not unique, but limit category 707
        self.assertTrue(Context.clash(ACA, WEAKNESS))     # same limit category
        self.assertTrue(Context.clash(HOTS, HOTS))        # unique
        self.assertFalse(Context.clash(HOTS, ACA))
        self.assertFalse(Context.clash(None, ACA))


@unittest.skipUnless(HAVE_SIM, "bin/bisrun not built")
class HarnessTest(unittest.TestCase):
    def test_siml_round_trip_and_header(self):
        from wowsim_tools.gear import Gear
        base = harness.preset_path("prot_paladin")
        g = Gear.from_preset(base)
        text = g.siml(base, header="test | header")
        lines = text.splitlines()
        self.assertEqual(lines[0], "siml 1")
        self.assertEqual(lines[1], "// test | header")   # // on line 2 is accepted; # is not
        self.assertEqual(Gear.from_siml(text, base).items, g.items)

    def test_bad_job_reports_error(self):
        o = harness.one({"name": "x", "tschool": "Nope", "statsOnly": True},
                        harness.preset_path("prot_paladin"))
        self.assertIn("unknown spell school", o["Err"])

    def test_rebase_refuses_wider_rules(self):
        # An old profile stricter than the current one means the current one
        # widens: a saved optimum may no longer be optimal, so no re-stamp.
        from wowsim_tools import api, findings
        from wowsim_tools.rules import Rules
        fid = "2026-11-18_prot_bis"
        if fid not in findings.ids():
            self.skipTest("prototype finding not imported")
        import shutil
        import tempfile
        ctx = api.context("prot_paladin", "prot_v3")
        stricter = dict(Rules("prot_v3").data)
        stricter["exclude_item_ids"] = stricter["exclude_item_ids"] + [ACA]
        old_sha = Rules("prot_v3", stricter).sha256
        real_root = findings.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            # A copy of the finding, stamped as if simmed under the stricter profile.
            shutil.copytree(real_root / fid, Path(tmp) / fid)
            findings.ROOT = Path(tmp)
            try:
                meta, res = findings.load(fid)
                meta["rules_sha256"] = old_sha
                for n in meta["sets"]:
                    meta["sets"][n]["rules_sha256"] = old_sha
                findings._write(Path(tmp) / fid / "meta.json", meta)
                with self.assertRaisesRegex(findings.FindingError, "allows items the old one did not"):
                    findings.rebase_rules(fid, ctx, stricter)
                self.assertEqual(findings.load(fid)[0]["rules_sha256"], old_sha)
            finally:
                findings.ROOT = real_root

    def test_findings_staleness(self):
        from wowsim_tools import findings
        meta = {"sim_sha": "0000000000", "db_json_sha256": "x", "rules_profile": "prot_v3",
                "rules_sha256": "y"}
        reasons = findings.staleness(meta)
        self.assertEqual(len(reasons), 3, reasons)


if __name__ == "__main__":
    unittest.main(verbosity=1)
