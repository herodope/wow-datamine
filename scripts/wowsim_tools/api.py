"""The sim questions, as functions returning compact text.

Shared by scripts/wowsim_mcp_server.py and scripts/wowsim.py, so the MCP
tools and the CLI answer identically. Every answer names the sim sha, the
rules profile, the iterations and the noise of what it reports.
"""

import json
import re
from pathlib import Path

import config
from . import analysis, findings, harness, jobs
from . import weights as W
from .consts import BUDGETS, METRICS, SLOT_ALIASES, SLOT_INDEX, SLOTS, STAT_HP
from .gear import Gear
from .objective import parse as parse_objective, parse_bool
from .optimize import Context, Optimizer
from .report import gear_table, metrics_line, signed, table
from .rules import Rules, list_profiles
from .simdb import db

DEFAULT_SPEC = "prot_paladin"
STATE = config.SIM_STATE_DIR / "state.json"

_ctx_cache = {}


# --- shared state -------------------------------------------------------------


def _state():
    return jobs.read_json(STATE, {})


def _spec_def(spec):
    specs = harness.load_specs()
    if spec not in specs:
        raise ValueError(f"unknown spec {spec!r}; known: {', '.join(sorted(specs))}")
    return specs[spec]


def default_rules(spec=None):
    """The rules profile last used for this spec, else the spec's default_rules.

    Remembered per spec: profiles carry spec constraints (prot_v3 is plate
    only), so one spec's choice must not carry over to another.
    """
    spec = spec or DEFAULT_SPEC
    profiles = list_profiles()
    if not profiles:
        raise ValueError(f"no rules profiles under {harness.rel(config.RULES_DIR)}")
    last = (_state().get("last_rules_by_spec") or {}).get(spec)
    if last in profiles:
        return last
    dflt = _spec_def(spec).get("default_rules")
    if dflt in profiles:
        return dflt
    return profiles[-1]


def default_metric(spec=None):
    return _spec_def(spec or DEFAULT_SPEC).get("default_metric", "tps")


def _remember_rules(spec, name):
    st = _state()
    by_spec = st.get("last_rules_by_spec") or {}
    if by_spec.get(spec) != name:
        by_spec[spec] = name
        st["last_rules_by_spec"] = by_spec
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st, indent=1), encoding="utf-8")


def context(spec=None, rules=None, race=None, log=None):
    spec = spec or DEFAULT_SPEC
    _spec_def(spec)
    rname = rules or default_rules(spec)
    key = (spec, rname, race, harness.build_info().get("sim_sha"))
    if key not in _ctx_cache or log is not None:
        r = Rules(rname)
        _ctx_cache[key] = Context(spec, r, race, log=log or (lambda m: None))
    _remember_rules(spec, rname)
    return _ctx_cache[key]


PALADIN = 2


def race_label(race, spec=None):
    """The race as printed in results. Undead paladins are new in WoW Forever,
    so the note sits beside the race, where a reader would question it."""
    sd = harness.load_specs().get(spec or DEFAULT_SPEC) or {}
    if race == "RaceUndead" and sd.get("class") == PALADIN:
        return f"{race} (Undead paladins are valid in WoW Forever, not an error)"
    return str(race)


def header(ctx, extra=""):
    st = harness.status()
    sha = (st["sim_sha"] or "unbuilt")[:8]
    h = f"wowsims/forever {sha} · {ctx.spec} · rules {ctx.rules.describe()} · {race_label(ctx.race, ctx.spec)}"
    if st["problems"]:
        h += "\nWARNING: " + "; ".join(st["problems"])
    return h + (f" · {extra}" if extra else "")


def parse_slot(slot):
    s = str(slot).strip().lower().replace("-", "_")
    if s.isdigit() and int(s) < len(SLOTS):
        return int(s)
    if s in SLOT_INDEX:
        return SLOT_INDEX[s]
    if s in SLOT_ALIASES:
        return SLOT_ALIASES[s]
    if s.replace(" ", "_") in SLOT_INDEX:
        return SLOT_INDEX[s.replace(" ", "_")]
    raise ValueError(f"unknown slot {slot!r}; use one of {', '.join(SLOTS)}")


def load_gear(ctx, gear):
    """(Gear, label). gear: .gear.siml text, a findings set '<id>/<set>', a job
    id, a path to a .siml file, or 'preset' for the spec preset's own gear."""
    g = (gear or "preset").strip()
    if g == "preset":
        return Gear.from_preset(ctx.base), "the spec preset's gear"
    if "\n" in g or g.startswith("siml"):
        return Gear.from_siml(g, ctx.base), "the given .gear.siml"
    if re.fullmatch(r"\d{4}-\d\d-\d\d_[\w.-]+/[\w.:-]+", g):
        gg, _, _, _ = findings.resolve_set(g)
        return gg, f"finding {g}"
    if (jobs.ROOT / g / "args.json").exists():
        return Gear.from_json(jobs.result(g)), f"job {g}"
    p = Path(g)
    if p.suffix == ".siml" and p.is_file():
        return Gear.from_siml(p.read_text(encoding="utf-8"), ctx.base), p.name
    raise ValueError("gear must be .gear.siml text, a findings set id like "
                     "2026-11-18_prot_bis/tps_t05, an optimize job id, a .siml path, or 'preset'")


def _enc(encounter, targets, tschool):
    return analysis.encounter_overrides(encounter or "boss", targets, tschool)


def _enc_label(encounter, targets, tschool):
    s = f"{encounter or 'boss'}"
    if targets and int(targets) > 1:
        s += f" ×{targets}"
    if tschool:
        s += f", {tschool} melee"
    return s


# --- tools --------------------------------------------------------------------


def status():
    st = harness.status()
    lines = [f"sim built from: {(st['sim_sha'] or 'NOT BUILT')[:10]}  (pin {st['pin'][:10]}, "
             f"checkout {(st['head'] or 'missing')[:10]})",
             f"sim item db: {db().sha256[:10]}",
             f"rules profiles: {', '.join(list_profiles())}",
             "specs: " + ", ".join(f"{s} (rules {default_rules(s)}, metric {default_metric(s)})"
                                   for s in harness.load_specs()),
             f"budgets: {', '.join(BUDGETS)}"]
    lines += [f"PROBLEM: {p}" for p in st["problems"]]
    js = jobs.list_jobs(5)
    if js:
        lines.append("recent jobs: " + "; ".join(f"{j['id']} {j['status']}" for j in js))
    return "\n".join(lines)


def sim_gear(gear="preset", spec=None, race=None, iterations=10000, targets=1,
             encounter="boss", tschool=None, rules=None):
    ctx = context(spec, rules, race)
    g, label = load_gear(ctx, gear)
    enc = _enc(encounter, targets, tschool)
    o = analysis.sim_gear(ctx, g, iterations=int(iterations), **enc)
    sets = ", ".join(o["Sets"]) or "none"
    return "\n".join([
        header(ctx, f"{iterations} iterations · {_enc_label(encounter, targets, tschool)}"),
        f"gear: {label}", "",
        metrics_line(o, int(iterations)),
        f"active sets: {sets}",
        "(± is the standard error of each mean. DTPS counts melee damage taken only; "
        "TMI is health-normalised spike damage.)"])


def stat_weights(gear="preset", metrics=("tps", "dps", "dtps", "tmi"), targets=(1,), iterations=8000,
                 spec=None, race=None, encounter=None, tschool=None, rules=None):
    ctx = context(spec, rules, race)
    g, label = load_gear(ctx, gear)
    ms = [m for m in METRICS if m.lower() in {x.lower() for x in metrics}] or list(METRICS)
    targets = [int(t) for t in (targets if isinstance(targets, (list, tuple)) else [targets])]
    enc_name = encounter or ("trash62" if any(t > 1 for t in targets) else "boss")
    out = [header(ctx, f"{iterations} iterations × 2 seeds per point"), f"measured on: {label}"]
    for t in targets:
        r = W.measure(g, ctx.base, ctx.race, iterations=int(iterations),
                      extra=_enc(enc_name, t, tschool), label=f"t{t}_")
        cols, norm = [], {}
        for m in ms:
            vals, unit = W.normalised(r["w"], m)
            norm[m] = vals
            cols.append(f"{m.upper()} ({unit}=1)" if unit else f"{m.upper()} per pt (−dmg)")
        rows = []
        for stat in r["w"]:
            row = [stat]
            for m in ms:
                v = norm[m][stat]
                unit_stat = W.NORMALISE.get(m)
                scale = abs(r["w"][W.STAT_NAMES[unit_stat]][m]) if unit_stat is not None else 1
                nz = r["noise"][stat][m]
                nz = nz / scale if nz is not None and scale else nz
                row.append(f"{v:.3f}" + (f" ±{nz:.3f}" if nz else ""))
            rows.append(row)
        b = r["base"]
        out += ["", f"**{_enc_label(enc_name, t, tschool)}** — base: {metrics_line(b, int(iterations))}",
                table(["Stat"] + cols, rows)]
    out.append("\nWeights are local to this gear (a capped stat reads ~0). DTPS weight of "
               "Stamina is 0 by definition; TMI values Stamina. ± is half the spread of two "
               "paired seeds.")
    return "\n".join(out)


def compare_items(gear, slot, items=None, top_n=None, metric=None, iterations=4000, t05=True,
                  spec=None, race=None, encounter=None, targets=1, tschool=None, rules=None):
    ctx = context(spec, rules, race)
    g, label = load_gear(ctx, gear)
    s = parse_slot(slot)
    obj = parse_objective(metric or default_metric(ctx.spec))
    t05 = parse_bool(t05, "t05")
    enc = _enc(encounter, targets, tschool)
    if items:
        alts = []
        for q in items:
            iid = db().resolve(q)
            alts.append((iid, 0))
    else:
        alts = analysis.top_candidates(ctx, g, s, int(top_n or 5), obj, t05, **enc)
    r = analysis.compare(ctx, g, s, alts, obj, iterations=int(iterations), **enc)
    rows = [[x["name"], x["id"], f"{signed(x['delta'])} ±{x['noise']:.1f}",
             *(signed(x["deltas"][m]) for m in METRICS if m != obj.metric), x["source"]]
            for x in r["rows"]]
    other = [m.upper() for m in METRICS if m != obj.metric]
    b = r["base"]
    out = [header(ctx, f"{iterations} iterations × seeds {r['seeds']}, paired"),
           f"gear: {label} · slot {r['slot']} · current: {r['current']} "
           f"({obj.metric.upper()} {b[obj.metric]:.1f})", "",
           table(["Alternative", "ID", f"Δ{obj.metric.upper()}"] + [f"Δ{m}" for m in other] + ["Source"], rows)]
    if r["skipped"]:
        out.append("\nNot simmed: " + "; ".join(f"{x['name']} ({x['why']})" for x in r["skipped"]))
    out.append(f"\nΔ is alternative minus current, mean of {len(r['seeds'])} seeds; ± is half their "
               f"spread. {'Lower' if obj.lower_better else 'Higher'} {obj.metric.upper()} is better. "
               "Differences inside the ± are ties.")
    return "\n".join(out)


def start_optimize(objective, t05, rules_profile=None, budget="quick", seed_from=None,
                   spec=None, race=None):
    return start_optimize_job(objective, t05, rules_profile, budget, seed_from, spec, race)[1]


def start_optimize_job(objective, t05, rules_profile=None, budget="quick", seed_from=None,
                       spec=None, race=None):
    """(job id, message)."""
    obj = parse_objective(objective)
    t05 = parse_bool(t05, "t05")
    if budget not in BUDGETS:
        raise ValueError(f"budget must be one of {', '.join(BUDGETS)}")
    spec = spec or DEFAULT_SPEC
    harness.preset_path(spec)
    rname = rules_profile or default_rules(spec)
    Rules(rname)
    harness.require_built()
    _remember_rules(spec, rname)
    jid = jobs.start("optimize", {"objective": obj.name, "t05": t05, "rules": rname,
                                  "budget": budget, "seed_from": seed_from, "spec": spec,
                                  "race": race})
    return jid, (f"started optimize job {jid}: objective {obj.name}, T0.5 {'on' if t05 else 'off'}, "
            f"rules {rname}, budget {budget}"
            + (f", seeded from {seed_from}" if seed_from else "")
            + ". Poll job_status; the quick budget takes a few minutes.")


def run_optimize(args, log=print, progress=None):
    """Run an optimize job in this process (the job runner calls this)."""
    ctx = context(args.get("spec"), args["rules"], args.get("race"), log=log)
    obj = parse_objective(args["objective"])
    t05 = bool(args["t05"])
    start, label, ref = None, "seed_from", args.get("seed_from")
    if ref:
        start, _ = load_gear(ctx, ref)
    elif t05:
        ref, start = findings.find_counterpart(ctx.spec, ctx.rules.name, obj.name, False)
        label = "best_not05"
        if ref:
            log(f"seed: the no-T0.5 {obj.name} set {ref}")
    opt = Optimizer(ctx, obj, t05, args.get("budget", "quick"), log=log, progress=progress)
    res = opt.run(start, label)
    res["seed_from"] = ref
    return res


def job_status(job_id=None):
    if not job_id:
        js = jobs.list_jobs(10)
        if not js:
            return "no jobs"
        return table(["Job", "Status", "Phase", "Elapsed s"],
                     [[j["id"], j["status"], j.get("phase") or "", j["elapsed_s"]] for j in js])
    st = jobs.status(job_id)
    a = st["args"]
    lines = [f"job {job_id}: {st.get('status')} · phase {st.get('phase', '-')} · "
             f"{st['elapsed_s']:.0f}s · {a.get('objective')} T0.5 {'on' if a.get('t05') else 'off'} "
             f"rules {a.get('rules')} budget {a.get('budget')}"]
    if st.get("error"):
        lines.append(f"error: {st['error']}")
    lines += ["log:"] + [f"  {ln}" for ln in st["log_tail"]]
    return "\n".join(lines)


def _result_text(r, faction):
    g = Gear.from_json(r)
    e = r["eval"]
    o = dict(e, Final=r.get("final"))
    out = [f"objective {r['objective']} · T0.5 {'on' if r['t05'] else 'off'} · rules {r['rules']} "
           f"({r['rules_sha256'][:8]}) · budget {r['budget']} · {race_label(r['race'], r.get('spec'))} · "
           f"sim {str(r.get('sim_sha'))[:8]}",
           f"final ({r.get('final_iters')} iterations): {metrics_line(o, r.get('final_iters'))}",
           f"active sets: {', '.join(r.get('sets') or []) or 'none'}", "",
           gear_table(g, faction)]
    if r.get("seeds"):
        out.append("\nseed scores: " + ", ".join(f"{k} {v:.1f}" for k, v in r["seeds"].items())
                   + f" (best: {r.get('best_seed')}; climb scores are at climb iterations)")
    if r.get("excluded"):
        out += ["", "Excluded by the rules but ahead by EP (not simmed):",
                table(["Slot", "Item", "ID", "Reason", "EP gain"],
                      [[x["slot"], x["name"], x["id"], x["reason"], x["ep_gain"]] for x in r["excluded"]])]
    return "\n".join(out)


def job_result(job_id):
    r = jobs.result(job_id)
    faction = Rules(r["rules"]).faction
    return f"job {job_id} ({r['elapsed_s']:.0f}s)\n" + _result_text(r, faction)


def export_gear_siml(result_id, spec=None):
    """.gear.siml for a job result or a findings set, with a provenance header."""
    if "/" in result_id:
        g, sm, meta, r = findings.resolve_set(result_id)
        path = findings.ROOT / result_id.split("/")[0] / sm.get("gear_file", "")
        if sm.get("gear_file") and path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            ctx = context(meta.get("spec"), sm.get("rules"))
            text = g.siml(ctx.base, header=f"{result_id}")
        stale = findings.staleness(meta, sm)
        return text + (f"\n// STALE: {'; '.join(stale)}\n" if stale else "")
    r = jobs.result(result_id)
    ctx = context(r.get("spec"), r["rules"], r.get("race"))
    e = r["eval"]
    hp = (r.get("final") or [0] * (STAT_HP + 1))[STAT_HP]
    head = (f"{result_id} | {r['objective']} T0.5 {'on' if r['t05'] else 'off'} | rules {r['rules']} "
            f"({r['rules_sha256'][:8]}) | {r['date'][:10]} | sim {str(r['sim_sha'])[:8]} | "
            f"TPS {e['Tps']:.0f}, DPS {e['Dps']:.0f}, DTPS {e['Dtps']:.1f}, TMI {e['Tmi']:.1f}, HP {hp:.0f}")
    return Gear.from_json(r).siml(ctx.base, header=head)


def findings_list(contains=""):
    rows = findings.list_findings(contains)
    if not rows:
        return "no saved findings" + (f" matching {contains!r}" if contains else "")
    return (table(["Finding", "Date", "Spec", "Rules", "Sim", "Sets", "Stale"],
                  [[r["id"], r["date"], r["spec"], r["rules"], r["sim_sha"], ", ".join(r["sets"]),
                    "; ".join(r["stale"]) or "no"] for r in rows])
            + "\n\nFindings are saved data, not ground truth. Re-sim a stale one before quoting it.")


def findings_get(finding_id, set_name=None):
    fid = finding_id.split("/")[0]
    if "/" in finding_id and not set_name:
        set_name = finding_id.split("/", 1)[1]
    meta, res = findings.load(fid)
    out = [f"finding {fid}: {meta['topic']} · {meta['date']} · status {meta['status']} (saved data, "
           f"not ground truth)",
           f"sim {meta['sim_sha'][:8]} · rules {meta['rules_profile']} · "
           f"race {race_label(meta.get('race'), meta.get('spec'))} · "
           f"encounter {meta.get('encounter')} · budget {meta.get('budget')}"]
    if meta.get("note"):
        out.append(meta["note"])
    if set_name:
        g, sm, _, r = findings.resolve_set(f"{fid}/{set_name}")
        stale = findings.staleness(meta, sm)
        out.append(f"STALE ({'; '.join(stale)}): re-sim before quoting." if stale else "current: matches this sim, db and rules")
        out += ["", f"set {set_name}:", _result_text(
            {**r, "rules": sm.get("rules") or meta["rules_profile"],
             "rules_sha256": sm.get("rules_sha256") or meta["rules_sha256"],
             "budget": r.get("budget", meta.get("budget")), "race": r.get("race", meta.get("race")),
             "sim_sha": meta["sim_sha"], "seeds": None, "excluded": None},
            Rules(meta["rules_profile"]).faction)]
        return "\n".join(out)
    rows = []
    for n, sm in meta["sets"].items():
        m = sm["metrics"]
        stale = findings.staleness(meta, sm)
        rows.append([n, sm["objective"], "on" if sm["t05"] else "off", f"{m['Tps']:.0f}", f"{m['Dps']:.0f}",
                     f"{m['Dtps']:.1f}", f"{m['Tmi']:.1f}", f"{sm['hp']:.0f}" if sm.get("hp") else "",
                     "; ".join(stale) or "no"])
    out += ["", table(["Set", "Objective", "T0.5", "TPS", "DPS", "DTPS", "TMI", "HP", "Stale"], rows),
            f"\nGet one set's gear with findings_get('{fid}/<set>'); export with export_gear_siml."]
    return "\n".join(out)


def findings_save(job_id, topic, set_name=None):
    r = jobs.result(job_id)
    ctx = context(r.get("spec"), r["rules"], r.get("race"))
    fid = findings.save_job_result(r, topic, ctx.base, set_name)
    return f"saved {job_id} as finding {fid} (findings/{fid}/)"


def close_calls(gear, metric=None, t05=True, n=4, iterations=4000, spec=None, race=None, rules=None):
    ctx = context(spec, rules, race)
    g, label = load_gear(ctx, gear)
    obj = parse_objective(metric or default_metric(ctx.spec))
    res = analysis.close_calls(ctx, g, obj, parse_bool(t05, "t05"), n=n, iterations=iterations)
    rows = []
    for r in res:
        alts = " ; ".join(f"{x['name']} {signed(x['delta'])}±{x['noise']:.1f}" for x in r["rows"][:n])
        rows.append([r["slot"], r["current"], alts])
    return "\n".join([header(ctx, f"{iterations} iterations × 2 seeds, paired"), f"gear: {label}", "",
                      table(["Slot", "Current", f"Best alternatives (Δ{obj.metric.upper()})"], rows)])
