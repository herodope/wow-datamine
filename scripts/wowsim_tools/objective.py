"""Optimizer objectives. Parsed from structured strings, never matched by substring."""

from .consts import STAT_HP

# dtps_min_hp: DTPS cost per HP below the floor. 0.1 means 10 HP short costs as
# much as 1 DTPS.
HP_PENALTY = 0.1


class Objective:
    def __init__(self, metric, min_hp=None, name=None):
        self.metric = metric            # Dps | Tps | Dtps | Tmi
        self.min_hp = min_hp
        self.name = name or metric.lower()

    @property
    def lower_better(self):
        return self.metric in ("Dtps", "Tmi")

    def score(self, o):
        """Higher is better. Errors score -inf-ish so they never win."""
        if not o or o.get("Err"):
            return -1e9
        if self.metric == "Tmi":
            return -o["Tmi"] - 0.001 * o["Dtps"]
        if self.metric == "Dtps":
            s = -o["Dtps"] - 0.001 * o["Tmi"]
            if self.min_hp:
                hp = (o.get("Final") or [0] * (STAT_HP + 1))[STAT_HP]
                s -= HP_PENALTY * max(0.0, self.min_hp - hp)
            return s
        return o[self.metric]

    def value(self, o):
        return o[self.metric]

    def __repr__(self):
        return self.name


def parse(text):
    t = str(text).strip().lower()
    simple = {"dps": "Dps", "tps": "Tps", "dtps": "Dtps", "tmi": "Tmi"}
    if t in simple:
        return Objective(simple[t])
    if t.startswith("dtps_min_hp:"):
        try:
            hp = float(t.split(":", 1)[1])
        except ValueError:
            raise ValueError(f"dtps_min_hp needs a number, e.g. dtps_min_hp:5000; got {text!r}")
        return Objective("Dtps", min_hp=hp, name=f"dtps_min_hp:{hp:g}")
    raise ValueError(f"unknown objective {text!r}; use dps, tps, dtps, tmi or dtps_min_hp:<HP>")


def parse_bool(v, what):
    if isinstance(v, bool):
        return v
    t = str(v).strip().lower()
    if t in ("on", "true", "1", "yes"):
        return True
    if t in ("off", "false", "0", "no"):
        return False
    raise ValueError(f"{what} must be on or off; got {v!r}")
