"""A gear set: 17 slots of (item id, random suffix) plus enchant effectIds."""

import hashlib
import json

from . import harness
from .consts import SLOTS, SLOT_LABELS
from .simdb import db


class Gear:
    def __init__(self, items=None, ench=None):
        self.items = list(items) if items else [None] * len(SLOTS)
        self.ench = {int(k): int(v) for k, v in (ench or {}).items() if v}

    # --- conversion ---------------------------------------------------------

    def job_items(self, ench=None):
        e = self.ench if ench is None else ench
        return [{"slot": s, "id": it[0], "suffix": it[1], "enchant": e.get(s, 0)}
                for s, it in enumerate(self.items) if it]

    def job(self, name, **kw):
        return {"name": name, "items": self.job_items(), "full": True, **kw}

    def to_json(self):
        return {"gear": [list(x) if x else None for x in self.items],
                "ench": {str(k): v for k, v in sorted(self.ench.items())}}

    @classmethod
    def from_json(cls, d):
        return cls([tuple(x) if x else None for x in d["gear"]], d.get("ench") or {})

    @classmethod
    def from_out_items(cls, items):
        g = cls()
        for it in items or []:
            g.items[it["slot"]] = (it["id"], it.get("suffix", 0))
            if it.get("enchant"):
                g.ench[it["slot"]] = it["enchant"]
        return g

    @classmethod
    def from_siml(cls, text, base):
        """Resolve .gear.siml text to ids through the sim's own name table."""
        o = harness.one({"name": "parse", "gear": text, "statsOnly": True}, base)
        if o["Err"]:
            raise ValueError(o["Err"])
        return cls.from_out_items(o.get("Items"))

    @classmethod
    def from_preset(cls, base):
        o = harness.one({"name": "preset", "statsOnly": True}, base)
        if o["Err"]:
            raise ValueError(o["Err"])
        return cls.from_out_items(o.get("Items"))

    def siml(self, base, header=None):
        """The set as .gear.siml text, slugs from the sim's printer.

        A // comment is allowed on line 2 (a # comment is not).
        """
        o = harness.one(self.job("print", statsOnly=True, printGear=True), base)
        if o["Err"]:
            raise ValueError(o["Err"])
        text = o["Siml"]
        if header:
            first, _, rest = text.partition("\n")
            text = f"{first}\n// {' '.join(header.split())}\n{rest.lstrip(chr(10))}"
        return text

    # --- edits ----------------------------------------------------------------

    def copy(self):
        return Gear(self.items, self.ench)

    def with_slot(self, slot, key):
        g = self.copy()
        g.items[slot] = key
        return g

    def key(self):
        return hashlib.sha256(json.dumps(self.to_json(), sort_keys=True).encode()).hexdigest()[:16]

    def __eq__(self, other):
        return isinstance(other, Gear) and self.items == other.items and self.ench == other.ench

    # --- display ----------------------------------------------------------------

    def item_label(self, slot):
        it = self.items[slot]
        if not it:
            return "—"
        name = db().name(it[0])
        return name + (f" [suffix {it[1]}]" if it[1] else "")

    def rows(self, faction="horde", sources=True):
        out = []
        for s in range(len(SLOTS)):
            it = self.items[s]
            if not it:
                out.append((SLOT_LABELS[s], "—", "", "", ""))
                continue
            out.append((SLOT_LABELS[s], self.item_label(s), str(it[0]),
                        db().enchant_name(self.ench.get(s), s),
                        db().sources(it[0], faction) if sources else ""))
        return out
