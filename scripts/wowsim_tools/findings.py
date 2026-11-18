"""Saved sim results: findings/<YYYY-MM-DD>_<topic>/.

A finding is previous data, never ground truth. Each one records what it was
simmed on, and reads as STALE when any of it no longer matches:
  - sim_sha          the wowsims/forever commit (bin/build_info.json)
  - db_json_sha256   the sim's item database
  - rules_sha256     the rules profile's canonical-JSON hash, per set

Layout:
  meta.json     ids, hashes, budget, race, encounter, status "findings", and
                one summary entry per set (objective, t05, metrics, rules)
  result.json   full per-set results: gear, enchants, eval, final stats
  summary.md    slot tables and metrics, for reading
  gear/<set>.gear.siml
  extra/        anything else the run produced (weights, screens, ties)

A set is referenced as "<finding id>/<set name>", e.g. 2026-11-18_prot_bis/tps_t05.
"""

import json
import re
import shutil
from datetime import date

import config
from . import harness
from .consts import SLOT_LABELS, SLOTS, STAT_ARMOR, STAT_DEF, STAT_HP
from .gear import Gear
from .rules import Rules, RulesError
from .simdb import db

ROOT = config.SIM_FINDINGS_DIR


class FindingError(ValueError):
    pass


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1) + "\n", encoding="utf-8")


def ids():
    if not ROOT.is_dir():
        return []
    return sorted((p.name for p in ROOT.iterdir() if (p / "meta.json").exists()), reverse=True)


def load(fid):
    d = ROOT / fid
    if not (d / "meta.json").exists():
        raise FindingError(f"no finding {fid!r}; known: {', '.join(ids()) or 'none'}")
    return _read(d / "meta.json"), _read(d / "result.json")


def _rules_hash(name):
    try:
        return Rules(name).sha256
    except RulesError:
        return None


def staleness(meta, set_meta=None):
    """Reasons this finding (or one of its sets) no longer matches the current sim."""
    st = harness.status()
    out = []
    if st["sim_sha"] and meta.get("sim_sha") and not st["sim_sha"].startswith(meta["sim_sha"][:8]):
        out.append(f"sim {meta['sim_sha'][:8]} -> now {st['sim_sha'][:8]}")
    if meta.get("db_json_sha256") and meta["db_json_sha256"] != db().sha256:
        out.append("sim item database changed")
    rname = (set_meta or {}).get("rules") or meta.get("rules_profile")
    rsha = (set_meta or {}).get("rules_sha256") or meta.get("rules_sha256")
    cur = _rules_hash(rname) if rname else None
    if rname and cur is None:
        out.append(f"rules profile {rname!r} no longer exists")
    elif rsha and cur != rsha:
        out.append(f"rules profile {rname!r} changed")
    return out


def list_findings(contains=""):
    rows = []
    for fid in ids():
        if contains and contains.lower() not in fid.lower():
            continue
        meta, _ = load(fid)
        sets = meta.get("sets", {})
        stale_sets = {n: staleness(meta, s) for n, s in sets.items()}
        rows.append({"id": fid, "date": meta.get("date"), "topic": meta.get("topic"),
                     "spec": meta.get("spec"), "rules": meta.get("rules_profile"),
                     "sim_sha": (meta.get("sim_sha") or "")[:8], "sets": list(sets),
                     "stale": sorted({r for v in stale_sets.values() for r in v})})
    return rows


def resolve_set(ref):
    """'<finding>/<set>' -> (Gear, set_meta, meta, set_result)."""
    if "/" not in ref:
        raise FindingError(f"a set reference is <finding id>/<set name>; got {ref!r}")
    fid, name = ref.split("/", 1)
    meta, res = load(fid)
    if name not in res.get("sets", {}):
        raise FindingError(f"{fid} has no set {name!r}; sets: {', '.join(res.get('sets', {}))}")
    r = res["sets"][name]
    return Gear.from_json(r), meta["sets"].get(name, {}), meta, r


def find_counterpart(spec, rules_name, objective, t05):
    """Newest non-stale set with this spec, rules, objective and t05 setting."""
    for fid in ids():
        meta, res = load(fid)
        if meta.get("spec") != spec:
            continue
        for name, sm in meta.get("sets", {}).items():
            if (sm.get("objective") == objective and sm.get("t05") is t05
                    and (sm.get("rules") or meta.get("rules_profile")) == rules_name
                    and not staleness(meta, sm)):
                return f"{fid}/{name}", Gear.from_json(res["sets"][name])
    return None, None


# --- writing ------------------------------------------------------------------


def _set_meta(r):
    e = r.get("eval", {})
    f = r.get("final") or []
    return {"objective": r.get("objective"), "t05": r.get("t05"), "rules": r.get("rules"),
            "rules_sha256": r.get("rules_sha256"), "budget": r.get("budget"),
            "metrics": {k: round(e[k], 2) for k in ("Dps", "Tps", "Dtps", "Tmi", "Cod") if k in e},
            "hp": f[STAT_HP] if len(f) > STAT_HP else None,
            "gear_file": f"gear/{r['_set']}.gear.siml"}


def summary_md(meta, res):
    sets = res["sets"]
    names = list(sets)
    faction = Rules(meta["rules_profile"]).faction if _rules_hash(meta["rules_profile"]) else "horde"
    lines = [f"# {meta['topic']}", "",
             f"Saved sim results (**findings, not ground truth**) from {meta['date']}. "
             f"Sim `{meta['sim_sha'][:8]}`, rules `{meta['rules_profile']}`, race {meta.get('race')}, "
             f"encounter {meta.get('encounter')}, budget {meta.get('budget')}.", ""]
    if meta.get("note"):
        lines += [meta["note"], ""]
    for n in names:
        r = sets[n]
        e, f = r["eval"], r.get("final") or []
        lines.append(f"- **{n}**: TPS {e['Tps']:.0f} · DPS {e['Dps']:.0f} · DTPS {e['Dtps']:.1f} · "
                     f"TMI {e['Tmi']:.1f} · HP {f[STAT_HP]:.0f} · armor {f[STAT_ARMOR]:.0f} · "
                     f"def +{f[STAT_DEF]:.0f}" if len(f) > STAT_HP else f"- **{n}**")
    lines += ["", "| Slot | " + " | ".join(names) + " |", "|---" * (len(names) + 1) + "|"]
    gears = [Gear.from_json(sets[n]) for n in names]
    for s in range(len(SLOTS)):
        cells = []
        for i, g in enumerate(gears):
            if not g.items[s]:
                cells.append("—")
                continue
            if i and g.items[s] == gears[0].items[s] and g.ench.get(s) == gears[0].ench.get(s):
                cells.append("same")
                continue
            en = db().enchant_name(g.ench.get(s), s)
            cells.append(f"**{g.item_label(s)}**" + (f" + {en}" if en else "")
                         + f" · {db().sources(g.items[s][0], faction)}")
        lines.append(f"| {SLOT_LABELS[s]} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def new_id(topic, day=None):
    slug = re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")[:40] or "finding"
    base = f"{(day or date.today().isoformat())}_{slug}"
    fid, n = base, 2
    while (ROOT / fid).exists():
        fid, n = f"{base}_{n}", n + 1
    return fid


def write(fid, topic, sets, base, common, note=None, siml_texts=None, extra_files=None):
    """sets: {name: result dict}. common: sim_sha, db hash, rules, race, ..."""
    d = ROOT / fid
    if d.exists():
        raise FindingError(f"finding {fid} already exists")
    res = {"sets": {}}
    meta = {"id": fid, "topic": topic, "date": fid[:10], "status": "findings", **common,
            "note": note, "sets": {}}
    for name, r in sets.items():
        r = dict(r, _set=name)
        meta["sets"][name] = _set_meta(r)
        r.pop("_set")
        res["sets"][name] = r
        text = (siml_texts or {}).get(name)
        if text is None:
            e = r["eval"]
            text = Gear.from_json(r).siml(base, header=(
                f"{fid}/{name} | rules {r.get('rules')} | sim {str(common.get('sim_sha'))[:8]} | "
                f"{fid[:10]} | TPS {e['Tps']:.0f}, DPS {e['Dps']:.0f}, DTPS {e['Dtps']:.1f}, TMI {e['Tmi']:.1f}"))
        (d / "gear").mkdir(parents=True, exist_ok=True)
        (d / "gear" / f"{name}.gear.siml").write_text(text, encoding="utf-8")
    _write(d / "meta.json", meta)
    _write(d / "result.json", res)
    for dst, src in (extra_files or {}).items():
        (d / "extra").mkdir(exist_ok=True)
        shutil.copyfile(src, d / "extra" / dst)
    (d / "summary.md").write_text(summary_md(meta, res), encoding="utf-8")
    return fid


def save_job_result(result, topic, base, set_name=None):
    """Save one optimize job's result as a new finding."""
    name = set_name or f"{result['objective'].replace(':', '_')}_{'t05' if result['t05'] else 'not05'}"
    common = {k: result.get(k) for k in ("spec", "sim_sha", "db_json_sha256", "budget", "race", "encounter")}
    common.update(rules_profile=result["rules"], rules_sha256=result["rules_sha256"])
    return write(new_id(topic), topic, {name: result}, base, common)


# --- prototype import -----------------------------------------------------------

PROTOTYPE_SETS = {
    # set name: (results file, siml file)
    "dtps": ("final_v2_Dtps_not05.json", "v2_protpally_DTPS.gear.siml"),
    "tps_t05": ("final_v3_Tps_t05.json", "v3_protpally_TPS_T05.gear.siml"),
    "tps_not05": ("final_v3_Tps_not05.json", "v3_protpally_TPS_noT05.gear.siml"),
    "dps_t05": ("final_v3_Dps_t05.json", "v3_protpally_DPS_T05.gear.siml"),
    "dps_not05": ("final_v3_Dps_not05.json", "v3_protpally_DPS_noT05.gear.siml"),
}
PROTOTYPE_SHA = "b83fe9df5fcd04d08061514332105ad9b769971d"


def import_prototype(kit_dir, ctx):
    """Load wowsim-kit/findings_nov18_2026 as the finding 2026-11-18_prot_bis.

    Tags are parsed structurally (v3_Tps_t05 -> objective tps, t05 on); a
    substring test would read 'Tmi_not05' as t05.
    """
    fid = "2026-11-18_prot_bis"
    if (ROOT / fid).exists():
        raise FindingError(f"{fid} is already imported")
    rules = ctx.rules
    sets, texts, notes = {}, {}, []
    for name, (rf, sf) in PROTOTYPE_SETS.items():
        r = _read(kit_dir / "results" / rf)
        ver, metric, t05tag = r["tag"].split("_")
        if t05tag not in ("t05", "not05"):
            raise FindingError(f"{rf}: unexpected tag {r['tag']!r}")
        g = Gear.from_json(r)
        bad = [(SLOTS[s], db().name(k[0]), ctx.reason(ctx.cand(k), t05tag == "t05"))
               for s, k in enumerate(g.items) if k and not ctx.allowed(ctx.cand(k), t05tag == "t05")]
        if bad:
            raise FindingError(f"{name}: items not allowed under {rules.name}: {bad}")
        if ver != "v3":
            notes.append(f"`{name}` was optimized under the prototype's {ver} rules, a superset of "
                         f"{rules.name}; every item in it is allowed under {rules.name}.")
        sets[name] = {**r, "objective": metric.lower(), "metric": metric, "t05": t05tag == "t05",
                      "rules": rules.name, "rules_sha256": rules.sha256, "budget": "prototype",
                      "race": "RaceUndead", "encounter": "boss", "final_iters": 20000 if ver == "v2" else 10000,
                      "source_tag": r["tag"]}
        texts[name] = (kit_dir / "gear_siml" / sf).read_text(encoding="utf-8")
    common = {"spec": ctx.spec, "sim_sha": PROTOTYPE_SHA, "db_json_sha256": db().sha256,
              "rules_profile": rules.name, "rules_sha256": rules.sha256, "budget": "prototype (mixed)",
              "race": "RaceUndead", "encounter": "boss"}
    note = ("Imported from the Nov 18 2026 prototype run. The db hash is the sim's db.json at the "
            "same commit. " + " ".join(notes))
    extra = {f: kit_dir / "results" / f for f in ("screen.json", "ties.json", "aoew.json",
                                                  "wfinal.json", "w_seed.json")}
    extra["prot_defaults_raidsimrequest.json"] = kit_dir / "prot_defaults_raidsimrequest.json"
    return write(fid, "Prot paladin BiS (prototype, prot_v3)", sets, ctx.base, common,
                 note=note, siml_texts=texts, extra_files=extra)
