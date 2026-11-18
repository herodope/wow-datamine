"""Stat weights by finite differences, and EP scoring of pool items.

Each weight is (metric with +delta of one stat - metric of the base) / delta,
simmed with one seed for every job (common random numbers), so the difference
is paired. Two seeds are run and the weight is their mean; the noise figure is
half their spread, i.e. a measured standard error of a two-sample mean.

Weights are local: the slope at the gear they were measured on. A capped stat
(hit, for one) reads ~0 there and can be worth more on other gear. Always say
which set the weights came from.

DTPS here counts melee damage taken only, and Stamina's DTPS weight is 0 by
definition (more HP does not reduce damage taken); TMI, which is normalised by
health, is the metric that values Stamina.
"""

from . import harness
from .consts import LOWER_BETTER, METRICS, SEED_STRIDE, SEED_WEIGHTS, STAT_NAMES, WEIGHT_DELTAS

# Normalisation stat per metric: Sta = 1 for TMI, SP = 1 for TPS/DPS. DTPS is
# reported per point (Stamina's DTPS weight is 0, so it cannot be the unit).
NORMALISE = {"Tmi": 2, "Tps": 5, "Dps": 5, "Dtps": None}


def measure(gear, base, race, iterations=8000, seed=SEED_WEIGHTS, stats=None,
            extra=None, seeds=2, label=""):
    """{"base": out, "w": {stat_name: {metric: per-point}}, "noise": {...}}.

    gear: a Gear, or None for the preset's own gear.
    extra: job overrides (targets, tlevel, ...).
    """
    stats = list(stats or WEIGHT_DELTAS)
    extra = dict(extra or {})
    jobs = []
    for k in range(seeds):
        sd = seed + k * SEED_STRIDE
        common = {"iterations": iterations, "seed": sd, "race": race, **extra}
        if gear is not None:
            common.update(items=gear.job_items(), full=True)
        jobs.append({"name": f"{label}b{k}", **common})
        for s in stats:
            jobs.append({"name": f"{label}{s}|{k}", **common, "bonus": {str(s): WEIGHT_DELTAS[s]}})
    r = harness.run(jobs, base)
    for o in r.values():
        if o["Err"]:
            raise harness.SimError(o["Err"])
    w, noise = {}, {}
    for s in stats:
        n = STAT_NAMES[s]
        w[n], noise[n] = {}, {}
        for m in METRICS:
            ds = [(r[f"{label}{s}|{k}"][m] - r[f"{label}b{k}"][m]) / WEIGHT_DELTAS[s] for k in range(seeds)]
            w[n][m] = sum(ds) / len(ds)
            noise[n][m] = (max(ds) - min(ds)) / 2 if len(ds) > 1 else None
    return {"base": r[f"{label}b0"], "w": w, "noise": noise,
            "iterations": iterations, "seeds": [seed + k * SEED_STRIDE for k in range(seeds)]}


def normalised(w, metric):
    """Weights for one metric, sign-flipped so bigger is better, and normalised."""
    sign = -1 if metric in LOWER_BETTER else 1
    raw = {k: sign * v[metric] for k, v in w.items()}
    unit = NORMALISE.get(metric)
    un = STAT_NAMES.get(unit) if unit is not None else None
    if un and raw.get(un):
        return {k: v / raw[un] for k, v in raw.items()}, un
    return raw, None


def ep_vector(w, metric):
    """Per-stat EP vector (index -> value, bigger is better) from measured weights."""
    sign = -1 if metric in LOWER_BETTER else 1
    by_name = {v: k for k, v in STAT_NAMES.items()}
    vec = [0.0] * (max(STAT_NAMES) + 1)
    for name, ms in w.items():
        vec[by_name[name]] = sign * ms[metric]
    return vec


def ep(cand, vec):
    return sum(a * b for a, b in zip(cand["st"], vec))
