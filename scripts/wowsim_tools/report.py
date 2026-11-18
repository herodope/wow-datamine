"""Compact text output: small markdown tables, never raw arrays."""

import math

from .consts import STAT_ARMOR, STAT_DEF, STAT_HP


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "/") for c in r) + " |")
    return "\n".join(out)


def se(sd, n):
    return sd / math.sqrt(n) if sd and n else 0.0


def metrics_line(o, iterations=None):
    """TPS/DPS/DTPS/TMI with the standard error of each mean, plus HP/armor/def."""
    n = iterations

    def m(k, label, digits=1):
        s = f"{label} {o[k]:.{digits}f}"
        if n and o.get(f"{k}Sd"):
            s += f" ±{se(o[f'{k}Sd'], n):.{digits}f}"
        return s

    parts = [m("Tps", "TPS"), m("Dps", "DPS"), m("Dtps", "DTPS"), m("Tmi", "TMI")]
    if "Cod" in o:
        parts.append(f"CoD {o['Cod'] * 100:.2f}%")
    f = o.get("Final") or o.get("final") or []
    if len(f) > STAT_HP:
        parts += [f"HP {f[STAT_HP]:.0f}", f"armor {f[STAT_ARMOR]:.0f}", f"def +{f[STAT_DEF]:.0f}"]
    return " · ".join(parts)


def gear_table(g, faction="horde", sources=True):
    rows = g.rows(faction, sources)
    if sources:
        return table(["Slot", "Item", "ID", "Enchant", "Source"], rows)
    return table(["Slot", "Item", "ID", "Enchant"], [r[:4] for r in rows])


def signed(x, digits=1):
    return f"{x:+.{digits}f}"
