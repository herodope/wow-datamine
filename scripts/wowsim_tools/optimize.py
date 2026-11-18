"""Gear optimizer: EP-greedy seeds, a full-sim screen, a sim-scored hill climb.

Steps, each simmed with one seed for every candidate (common random numbers):
  1. Stat weights at the starting gear, for EP.
  2. Seeds: EP-greedy (or a given set with disallowed slots refilled), plus a
     forced-set seed per configured set (PvP 6pc; Soulforge 8pc when T0.5 is
     on, tank pieces for dtps/tmi and str/SP pieces for tps/dps; Lightforge
     when it is off).
  3. Screen: every allowed trinket, weapon, shield and libram is simmed in the
     first seed, because procs and on-use effects do not show up in EP.
  4. Climb: per seed, slot by slot in a shuffled order, sim every candidate
     swap and keep the best one that beats the current score by 0.05%. Seeds
     climb in parallel.
  5. Enchant pass on the best seed, an optional extra climb pass with the
     chosen enchants, then one final sim at the budget's final iterations.

Constraints the sim does not enforce and this module does: limitCategory
(707 Pix Xizzix trinkets, 710 Watchers signets, ...) and unique items across
the ring and trinket pairs.
"""

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import harness, pool
from . import weights as W
from .consts import (BUDGETS, LOWER_BETTER, PAIRED, SEED_CLIMB, SEED_ENCH,
                     SEED_FINAL, SEED_SCREEN, SEED_WEIGHTS, SLOTS)
from .gear import Gear
from .simdb import db

IMPROVE = 0.0005   # a swap must beat the current score by this fraction


class Context:
    """Spec, rules and pool: everything a sim question needs besides the gear."""

    def __init__(self, spec, rules, race=None, log=print):
        self.spec = spec
        self.sd = harness.load_specs()[spec]
        self.base = harness.preset_path(spec)
        self.rules = rules
        self.race = race or rules.race
        self.log = log
        self.pool = pool.load(spec, log)
        self.bykey = {(c["id"], c["suffix"]): c for c in self.pool}
        self.t05_set = self.sd.get("t05_set")

    def run(self, jobs, **kw):
        for j in jobs:
            j.setdefault("race", self.race)
        return harness.run(jobs, self.base, **kw)

    def allowed(self, c, t05):
        return self.rules.allowed(c, t05, self.t05_set)

    def reason(self, c, t05):
        return self.rules.excluded_reason(c, t05, self.t05_set)

    def cand(self, key):
        """Pool entry for (id, suffix); items outside the pool get a stub."""
        c = self.bykey.get(tuple(key))
        if c:
            return c
        it = db().item(key[0]) or {"name": f"item {key[0]}", "quality": 0}
        return {"id": key[0], "name": it["name"], "suffix": key[1], "slots": [-1],
                "q": it.get("quality", 0), "ilvl": db().ilvl(key[0]), "src": [],
                "setId": it.get("setId"), "armorType": it.get("armorType"), "st": []}

    @staticmethod
    def clash(a, b):
        """Two item ids that cannot be worn together in a ring or trinket pair."""
        if a is None or b is None:
            return False
        if a == b and db().unique(a):
            return True
        # Same item twice is still a clash when it has a limit category
        # (Adaptive Combat Assistant: not unique, limit category 707).
        la, lb = db().limcat(a), db().limcat(b)
        return la is not None and la == lb

    def default_ench(self, g):
        opts = self.sd.get("enchant_options", {})
        return {int(s): v[0] for s, v in opts.items() if g.items[int(s)]}


class Optimizer:
    def __init__(self, ctx, objective, t05, budget="quick", log=None, progress=None):
        if budget not in BUDGETS:
            raise ValueError(f"budget must be one of {', '.join(BUDGETS)}")
        self.ctx = ctx
        self.obj = objective
        self.t05 = t05
        self.budget_name = budget
        self.b = BUDGETS[budget]
        self._lock = threading.Lock()
        self._log = log or ctx.log
        self.progress = progress or (lambda phase: None)
        self.screen = {}
        self.W = None

    def log(self, msg):
        with self._lock:
            self._log(msg)

    # --- helpers ---------------------------------------------------------------

    def allowed_in(self, slot):
        return [c for c in self.ctx.pool if slot in c["slots"] and self.ctx.allowed(c, self.t05)]

    def best_suffix_by_id(self, slot, vec):
        best = {}
        for c in self.allowed_in(slot):
            e = W.ep(c, vec)
            if c["id"] not in best or e > best[c["id"]][0]:
                best[c["id"]] = (e, c)
        return best

    def sim(self, named_gears, iters, seed, **kw):
        jobs = [g.job(n, iterations=iters, seed=seed, **kw) for n, g in named_gears.items()]
        return self.ctx.run(jobs)

    # --- seeds -----------------------------------------------------------------

    def epgreedy(self, vec, forced=None):
        forced = forced or {}
        g = Gear()
        for s in range(len(SLOTS)):
            if s in forced:
                g.items[s] = tuple(forced[s])
                continue
            ranked = sorted(self.allowed_in(s), key=lambda c: -W.ep(c, vec))
            for c in ranked:
                other = PAIRED.get(s)
                if other is not None and g.items[other] and self.ctx.clash(g.items[other][0], c["id"]):
                    continue
                g.items[s] = (c["id"], c["suffix"])
                break
        g.ench = self.ctx.default_ench(g)
        return g

    def refill(self, g, vec):
        """Keep the allowed, non-clashing slots of g; EP-fill the rest."""
        keep = {}
        for s, it in enumerate(g.items):
            if it and self.ctx.allowed(self.ctx.cand(it), self.t05):
                keep[s] = it
        for a, b in ((10, 11), (12, 13)):
            if a in keep and b in keep and self.ctx.clash(keep[a][0], keep[b][0]):
                del keep[b]
        out = self.epgreedy(vec, keep)
        out.ench = {s: e for s, e in g.ench.items() if out.items[s]} or out.ench
        for s, e in self.ctx.default_ench(out).items():
            out.ench.setdefault(s, e)
        return out

    def set_pieces(self, set_id, names=None):
        """{slot: key} for a set's allowed pieces; with names, those pieces only.

        Several items can share a name at different ilvls; the highest ilvl wins.
        """
        out = {}
        for c in self.ctx.pool:
            if c.get("setId") != set_id or not self.ctx.allowed(c, self.t05):
                continue
            if names is not None and c["name"] not in names:
                continue
            s = c["slots"][0]
            if s not in out or (c["ilvl"] or 0) > (self.ctx.cand(out[s])["ilvl"] or 0):
                out[s] = (c["id"], c["suffix"])
        return out

    def with_pieces(self, start, pieces, vec):
        """A forced-set seed: the set's pieces over the start set, else over EP-greedy.

        Over a start set, the other slots keep a climbed set's items, so the
        seed can reach a multi-piece bonus a one-slot climb cannot cross to.
        """
        if start is None:
            return self.epgreedy(vec, pieces)
        g = start.copy()
        for s, k in pieces.items():
            g.items[s] = k
        for a, b in ((10, 11), (12, 13)):
            if g.items[a] and g.items[b] and self.ctx.clash(g.items[a][0], g.items[b][0]):
                g.items[b] = None
        return self.refill(g, vec)

    def seeds(self, vec, start=None, start_label="seed_from"):
        sd = self.ctx.sd
        seeds = {}
        if start is not None:
            start = seeds[start_label] = self.refill(start, vec)
        else:
            seeds["ep"] = self.epgreedy(vec)
        for name, sid in (sd.get("seed_sets") or {}).items():
            pieces = self.set_pieces(sid)
            total = len(db().sets.get(sid, []))
            if pieces and len(pieces) == total:
                seeds[name] = self.with_pieces(start, pieces, vec)
            else:
                self.log(f"seed {name}: skipped, {len(pieces)}/{total} pieces allowed")
        if self.t05 and self.ctx.t05_set:
            variant = "tank" if self.obj.metric in LOWER_BETTER else "dps"
            names = set(sd["t05_variants"][variant])
            pieces = self.set_pieces(self.ctx.t05_set, names)
            seeds[f"t05_{variant}8"] = self.with_pieces(start, pieces, vec)
        elif not self.t05:
            for name, sid in (sd.get("no_t05_seed_sets") or {}).items():
                pieces = self.set_pieces(sid)
                if pieces:
                    seeds[name] = self.with_pieces(start, pieces, vec)
        return seeds

    # --- screen ------------------------------------------------------------------

    def run_screen(self, g, vec):
        """Sim every allowed candidate of the screen slots in g. {slot: {key: out}}."""
        slots = self.ctx.sd.get("screen_slots", [])
        named, meta = {}, {}
        for s in slots:
            for _, c in self.best_suffix_by_id(s, vec).values():
                key = (c["id"], c["suffix"])
                other = PAIRED.get(s)
                if other is not None and g.items[other] and self.ctx.clash(g.items[other][0], c["id"]):
                    continue
                n = f"s{s}|{key[0]}|{key[1]}"
                named[n] = g.with_slot(s, key)
                meta[n] = (s, key)
        self.log(f"screen: {len(named)} candidates in slots "
                 f"{', '.join(SLOTS[s] for s in slots)} at {self.b['screen_iters']} iterations")
        r = self.sim(named, self.b["screen_iters"], SEED_SCREEN)
        out = {s: {} for s in slots}
        for n, (s, key) in meta.items():
            if not r[n]["Err"]:
                out[s][key] = r[n]
        self.screen = out
        return out

    # --- climb -------------------------------------------------------------------

    def cands_for(self, s, g, vec, n_armor):
        best = self.best_suffix_by_id(s, vec)
        ranked = [c for _, c in sorted(best.values(), key=lambda t: -t[0])]
        scr = self.screen.get(12 if s == 13 else s)
        if scr:
            keys = sorted(scr, key=lambda k: -self.obj.score(scr[k]))
            out = [self.ctx.cand(k) for k in keys][: self.b["screen_top"]]
        else:
            out = ranked[:n_armor]
        climb_sets = set(self.ctx.sd.get("climb_set_ids") or [])
        for _, c in best.values():
            if c.get("setId") in climb_sets and c not in out:
                out.append(c)
        other = PAIRED.get(s)
        res = []
        for c in out:
            key = (c["id"], c["suffix"])
            if key == g.items[s]:
                continue
            if other is not None and g.items[other] and self.ctx.clash(g.items[other][0], c["id"]):
                continue
            res.append(key)
        return res

    def climb(self, g, vec, label, passes, iters, seed=SEED_CLIMB):
        g = g.copy()
        cur = self.obj.score(self.sim({"b": g}, iters, seed)["b"])
        for p in range(passes):
            improved = False
            order = list(range(len(SLOTS)))
            random.Random(p).shuffle(order)
            for s in order:
                cs = self.cands_for(s, g, vec, self.b["armor_cands"])
                if not cs:
                    continue
                named = {f"{k[0]}|{k[1]}": g.with_slot(s, k) for k in cs}
                r = self.sim(named, iters, seed)
                bestk, bests = None, cur
                for k in cs:
                    sc = self.obj.score(r[f"{k[0]}|{k[1]}"])
                    if sc > bests + abs(cur) * IMPROVE:
                        bestk, bests = k, sc
                if bestk:
                    self.log(f"  [{label}] p{p} {SLOTS[s]}: {g.item_label(s)} -> "
                             f"{db().name(bestk[0])}  {cur:.2f} -> {bests:.2f}")
                    g.items[s] = bestk
                    for k2, v in self.ctx.default_ench(g).items():
                        g.ench.setdefault(k2, v)
                    cur, improved = bests, True
            if not improved:
                break
        return g, cur

    # --- enchants ------------------------------------------------------------------

    def ench_pass(self, g, rounds=2):
        opts = {int(s): v for s, v in self.ctx.sd.get("enchant_options", {}).items() if g.items[int(s)]}
        ench = dict(g.ench)
        for s, v in opts.items():
            ench.setdefault(s, v[0])
        for rnd in range(rounds):
            named, meta = {}, {}
            for s, v in opts.items():
                for e in v:
                    g2 = g.copy()
                    g2.ench = dict(ench)
                    g2.ench[s] = e
                    n = f"e{s}|{e}"
                    named[n], meta[n] = g2, (s, e)
            r = self.sim(named, self.b["ench_iters"], SEED_ENCH)
            changed = False
            for s, v in opts.items():
                best = max(v, key=lambda e: self.obj.score(r[f"e{s}|{e}"]))
                if best != ench[s]:
                    self.log(f"  enchant {SLOTS[s]}: {db().enchant_name(ench[s], s)} -> "
                             f"{db().enchant_name(best, s)}")
                    ench[s] = best
                    changed = True
            if not changed:
                break
        g = g.copy()
        g.ench = ench
        return g

    # --- excluded report ---------------------------------------------------------

    def excluded_above(self, g, vec, limit=15):
        """Items the rules excluded that outrank the chosen item by EP, per slot."""
        rows = []
        seen = set()
        for s in range(len(SLOTS)):
            if not g.items[s]:
                continue
            chosen = W.ep(self.ctx.cand(g.items[s]), vec) if self.ctx.cand(g.items[s]).get("st") else 0
            per = []
            for c in self.ctx.pool:
                if s not in c["slots"] or c["id"] in seen:
                    continue
                why = self.ctx.reason(c, self.t05)
                if why is None or why == "random suffix":
                    continue
                gain = W.ep(c, vec) - chosen
                if gain > 0:
                    per.append((gain, c, why))
            per.sort(key=lambda t: -t[0])
            for gain, c, why in per[:2]:
                seen.add(c["id"])
                rows.append({"slot": SLOTS[s], "id": c["id"], "name": c["name"],
                             "reason": why, "ep_gain": round(gain, 2)})
        rows.sort(key=lambda r: -r["ep_gain"])
        return rows[:limit]

    # --- run ---------------------------------------------------------------------

    def run(self, start=None, start_label="seed_from"):
        t0 = time.time()
        b = self.b
        self.progress("weights")
        wgear = start if start is not None else Gear.from_preset(self.ctx.base)
        self.log(f"weights: measuring at {'the seed set' if start is not None else 'the preset gear'}, "
                 f"{b['weight_iters']} iterations")
        self.W = W.measure(wgear, self.ctx.base, self.ctx.race, iterations=b["weight_iters"],
                           seed=SEED_WEIGHTS, seeds=1)["w"]
        vec = W.ep_vector(self.W, self.obj.metric)

        self.progress("seeds")
        seeds = self.seeds(vec, start, start_label)
        self.log(f"seeds: {', '.join(seeds)}")

        self.progress("screen")
        self.run_screen(next(iter(seeds.values())), vec)

        self.progress("climb")
        self.log(f"climb: {len(seeds)} seed(s) in parallel, {b['passes']} pass(es), "
                 f"{b['climb_iters']} iterations")
        with ThreadPoolExecutor(max_workers=len(seeds)) as ex:
            futs = {n: ex.submit(self.climb, g, vec, n, b["passes"], b["climb_iters"])
                    for n, g in seeds.items()}
            results = {n: f.result() for n, f in futs.items()}
        for n, (_, sc) in results.items():
            self.log(f"  seed {n}: score {sc:.2f}")
        best_name = max(results, key=lambda n: results[n][1])
        g = results[best_name][0]

        self.progress("enchants")
        self.log(f"enchants: best seed {best_name}, {b['ench_iters']} iterations")
        g = self.ench_pass(g)
        if b["post_ench_passes"]:
            self.progress("climb with enchants")
            g, _ = self.climb(g, vec, "post-ench", b["post_ench_passes"], b["climb_iters"] * 2, seed=304)

        self.progress("final")
        self.log(f"final: {b['final_iters']} iterations")
        o = self.sim({"final": g}, b["final_iters"], SEED_FINAL, concurrent=True)["final"]
        if o["Err"]:
            raise harness.SimError(o["Err"])
        st = harness.status()
        return {
            "kind": "optimize", "spec": self.ctx.spec, "objective": self.obj.name,
            "metric": self.obj.metric, "t05": self.t05, "rules": self.ctx.rules.name,
            "rules_sha256": self.ctx.rules.sha256, "budget": self.budget_name,
            "race": self.ctx.race, "encounter": "boss", "sim_sha": st["sim_sha"],
            "db_json_sha256": db().sha256,
            "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **g.to_json(),
            "desc": {SLOTS[s]: g.item_label(s) for s in range(len(SLOTS)) if g.items[s]},
            "eval": {k: o[k] for k in ("Dps", "Tps", "Dtps", "Tmi", "Cod", "DpsSd", "TpsSd", "DtpsSd", "TmiSd")},
            "final_iters": b["final_iters"], "final": o["Final"], "sets": o["Sets"],
            "seeds": {n: round(sc, 3) for n, (_, sc) in results.items()}, "best_seed": best_name,
            "excluded": self.excluded_above(g, vec),
            "elapsed_s": round(time.time() - t0, 1),
        }
