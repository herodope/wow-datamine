"""One-off sim questions: a set's metrics, item comparisons, close calls."""

from . import weights as W
from .consts import (ENCOUNTERS, PAIRED, SEED_COMPARE, SEED_SCREEN, SEED_STRIDE,
                     SLOTS, SPELL_SCHOOLS)
from .simdb import db


def encounter_overrides(encounter="boss", targets=1, tschool=None):
    if encounter not in ENCOUNTERS:
        raise ValueError(f"encounter must be one of {', '.join(ENCOUNTERS)}")
    ov = dict(ENCOUNTERS[encounter])
    if targets and int(targets) > 1:
        ov["targets"] = int(targets)
    if tschool:
        t = str(tschool).lower()
        if t not in SPELL_SCHOOLS:
            raise ValueError(f"tschool must be one of {', '.join(SPELL_SCHOOLS)}")
        ov["tschool"] = SPELL_SCHOOLS[t]
    return ov


def sim_gear(ctx, gear, iterations=10000, seed=SEED_COMPARE, **enc):
    o = ctx.run([gear.job("g", iterations=iterations, seed=seed, concurrent=True, **enc)])["g"]
    if o["Err"]:
        raise ValueError(o["Err"])
    return o


def screen_slot(ctx, gear, slot, t05, iterations=1000, objective=None, **enc):
    """Sim every allowed candidate for a slot in gear, one seed. [(key, out)] best first."""
    pick = {}
    for c in ctx.pool:
        if slot in c["slots"] and ctx.allowed(c, t05):
            pick.setdefault(c["id"], (c["id"], c["suffix"]))
    other = PAIRED.get(slot)
    named = {}
    for key in pick.values():
        if key == gear.items[slot]:
            continue
        if other is not None and gear.items[other] and ctx.clash(gear.items[other][0], key[0]):
            continue
        named[f"{key[0]}|{key[1]}"] = gear.with_slot(slot, key)
    jobs = [g.job(n, iterations=iterations, seed=SEED_SCREEN, **enc) for n, g in named.items()]
    r = ctx.run(jobs)
    out = [(tuple(int(x) for x in n.split("|")), o) for n, o in r.items() if not o["Err"]]
    out.sort(key=lambda t: -objective.score(t[1]))
    return out


def top_candidates(ctx, gear, slot, n, objective, t05, **enc):
    """The n most promising alternatives: full-sim screen for proc slots, EP otherwise."""
    if slot in (ctx.sd.get("screen_slots") or []) or slot == 13:
        return [k for k, _ in screen_slot(ctx, gear, slot, t05, objective=objective, **enc)[:n]]
    w = W.measure(gear, ctx.base, ctx.race, iterations=2000, seeds=1, extra=enc)["w"]
    vec = W.ep_vector(w, objective.metric)
    best = {}
    for c in ctx.pool:
        if slot in c["slots"] and ctx.allowed(c, t05) and (c["id"], c["suffix"]) != gear.items[slot]:
            e = W.ep(c, vec)
            if c["id"] not in best or e > best[c["id"]][0]:
                best[c["id"]] = (e, (c["id"], c["suffix"]))
    other = PAIRED.get(slot)
    ranked = [k for _, k in sorted(best.values(), key=lambda t: -t[0])
              if not (other is not None and gear.items[other] and ctx.clash(gear.items[other][0], k[0]))]
    return ranked[:n]


def compare(ctx, gear, slot, alts, objective, iterations=4000, seeds=2, seed=SEED_COMPARE, **enc):
    """Swap each alternative into slot; paired deltas over `seeds` seeds.

    Returns {"base": {metric: value}, "rows": [...], "skipped": [...]}. Each
    row's delta is the mean over seeds and its noise is half the spread of the
    per-seed deltas.
    """
    other = PAIRED.get(slot)
    rows, skipped, named = [], [], {}
    for key in alts:
        key = tuple(key)
        if other is not None and gear.items[other] and ctx.clash(gear.items[other][0], key[0]):
            skipped.append({"id": key[0], "name": db().name(key[0]),
                            "why": f"cannot be worn with {gear.item_label(other)} "
                                   f"(unique or same limit category)"})
            continue
        if key == gear.items[slot]:
            continue
        named[key] = gear.with_slot(slot, key)
    jobs = []
    for k in range(seeds):
        sd = seed + k * SEED_STRIDE
        jobs.append(gear.job(f"base|{k}", iterations=iterations, seed=sd, **enc))
        for key, g in named.items():
            jobs.append(g.job(f"{key[0]}|{key[1]}|{k}", iterations=iterations, seed=sd, **enc))
    r = ctx.run(jobs)
    for k in range(seeds):
        if r[f"base|{k}"]["Err"]:
            raise ValueError(r[f"base|{k}"]["Err"])
    m = objective.metric
    for key in named:
        outs = [r[f"{key[0]}|{key[1]}|{k}"] for k in range(seeds)]
        if any(o["Err"] for o in outs):
            skipped.append({"id": key[0], "name": db().name(key[0]), "why": outs[0]["Err"][:120]})
            continue
        ds = {mm: [outs[k][mm] - r[f"base|{k}"][mm] for k in range(seeds)]
              for mm in ("Dps", "Tps", "Dtps", "Tmi")}
        rows.append({"id": key[0], "suffix": key[1], "name": db().name(key[0]),
                     "delta": sum(ds[m]) / seeds,
                     "noise": (max(ds[m]) - min(ds[m])) / 2 if seeds > 1 else None,
                     "deltas": {mm: sum(v) / seeds for mm, v in ds.items()},
                     "source": db().sources(key[0], ctx.rules.faction)})
    rows.sort(key=lambda x: x["delta"], reverse=not objective.lower_better)
    base = {mm: sum(r[f"base|{k}"][mm] for k in range(seeds)) / seeds for mm in ("Dps", "Tps", "Dtps", "Tmi")}
    return {"slot": SLOTS[slot], "current": gear.item_label(slot), "metric": m,
            "iterations": iterations, "seeds": [seed + k * SEED_STRIDE for k in range(seeds)],
            "base": base, "rows": rows, "skipped": skipped}


def close_calls(ctx, gear, objective, t05, slots=(1, 3, 10, 11, 12, 13, 14, 15), n=4,
                iterations=4000, **enc):
    """Per slot, the best alternatives and how far behind the current item they are."""
    out = []
    for s in slots:
        if not gear.items[s]:
            continue
        alts = top_candidates(ctx, gear, s, n, objective, t05, **enc)
        out.append(compare(ctx, gear, s, alts, objective, iterations=iterations, **enc))
    return out
