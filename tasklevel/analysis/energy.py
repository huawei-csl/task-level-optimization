"""Measured energy: the ARP winners' energy (paper Fig. 4a / Fig. 6, Fig. 4b) and the MatMul
energy-directed objectives (paper Tables 4, 6, 7).

Objectives on MatMul: arp (reference), marp1/marp2 (movements^alpha x arp), earp1/earp2
(measured energy^k x arp), energy (measured circuit energy alone); both tracks. Energies come from
data/<run>/power.json: `winner:<job>` for the per-run bests (tasklevel.power default) and
`eval:<job>:<eval_N>` for every valid design (tasklevel.power --population).

    python -m tasklevel.analysis.energy                        # profiles: arp energy_objectives
    python -m tasklevel.analysis.energy --profiles glm_arp --suffix _glm     # Fig. 6

Outputs in data/analysis/:
    fig_energy{suffix}.{png,pdf}         ARP winners' measured energy relative to start, per benchmark
                                         and track (Fig. 4a; Fig. 6 with another model's profile)
    fig_energy_matmul_reps.{png,pdf}     MatMul task-level per-run bests that improve ARP: energy vs
                                         ARP, both relative, with a linear fit (Fig. 4b)
    table_energy_columnwise.{tex,md}     per objective and track: column-wise minima over ALL valid
                                         designs of the repetitions (Table 4; needs --population)
    table_energy_winners.{tex,md}        per objective and track: the winner by the in-loop objective
                                         across repetitions, as one coherent design (Table 6)
    table_energy_meanstd.{tex,md}        mean +- sample std over the repetitions' per-run bests (Table 7)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..benches import BENCHES
from ..study import JOBS
from . import _common as A

ORDER = ["arp", "marp1", "marp2", "earp1", "earp2", "energy"]
LABEL = {"arp": "\\arp", "marp1": "$\\marp_1$", "marp2": "$\\marp_2$", "earp1": "\\earp", "earp2": "$\\earp_2$", "energy": "$E$"}
MD_LABEL = {"arp": "arp", "marp1": "marp α=1", "marp2": "marp α=2", "earp1": "earp k=1", "earp2": "earp k=2", "energy": "energy"}
COLS = ["area", "delay", "cycles", "energy", "arp_rel", "e_rel", "earp_rel", "earp2_rel"]
FMT = {"area": "{:.1f}", "delay": "{:.1f}", "cycles": "{:.0f}", "energy": "{:.3g}", "arp_rel": "{:.3f}",
       "e_rel": "{:.2f}", "earp_rel": "{:.3f}", "earp2_rel": "{:.3f}"}
MD_HDR = "| energy directed | optimization | area um2 | period ps | cycles | energy fJ | arp rel | E rel | earp rel | earp2 rel |"
TEX_HDR = ["\\begin{tabular}{cl rrrr@{\\hspace{1.2em}}rrrr}", "\\toprule",
           " & & \\multicolumn{4}{c}{measured} & \\multicolumn{4}{c@{}}{relative to start} \\\\",
           "\\cmidrule(lr){3-6} \\cmidrule(l){7-10}",
           "energy directed & optimization & area & period & cycles & energy & \\arp{} & E & \\earp{} & $\\earp_2$ \\\\", "\\midrule"]


def _vals(m: dict, energy: float | None, start: dict, e0: float | None) -> dict:
    """The eight table columns of one design point (energy columns None without a measurement)."""
    a, a0 = A.arp(m), A.arp(start)
    out = dict(area=m["area"], delay=m["delay"], cycles=m["cycles"], energy=energy, arp_rel=a / a0, e_rel=None, earp_rel=None, earp2_rel=None)
    if energy is not None and e0:
        out.update(e_rel=energy / e0, earp_rel=energy * a / (e0 * a0), earp2_rel=energy ** 2 * a / (e0 ** 2 * a0))
    return out


def population(profiles, bench="matmul", hdl="spire") -> tuple[dict | None, float | None, dict, dict]:
    """(start metrics, start energy, pool, reps). pool[(arm, objective)] = every valid design of
    every complete run (metrics as evaluated, energy from power.json when measured);
    reps[(arm, objective)] = the per-run bests (rescored metrics, winner energy)."""
    start, e0, pool, reps = None, None, {}, {}
    for r in A.runs(profiles):
        sr, pw = r["study"], r["power"] or {}
        base = sr["baselines"].get(f"{bench}_{hdl}")
        if not base or base.get("status") != "ok":
            continue
        if start is None:
            start = base["metrics"]
        if e0 is None:
            e0 = (pw.get(f"baseline:{bench}_{hdl}") or {}).get("e_circuit_fj")
        for job, rec in sr["runs"].items():
            spec = JOBS.get(job)
            if not spec or spec["bench"] != bench or spec["hdl"] != hdl or rec.get("status") != "completed":
                continue
            key = (spec["arm"], spec["objective"])
            rs = sr["rescores"].get(job) or {}
            if rs.get("status") == "ok":
                reps.setdefault(key, []).append(dict(run=r["run"], metrics=rs["metrics"],
                                                     energy=(pw.get(f"winner:{job}") or {}).get("e_circuit_fj")))
            best_eval = rec.get("best_eval")
            for f in sorted(Path(rec["run_dir"]).glob("eval_*/result.json")) if rec.get("run_dir") else []:
                try:
                    e = json.loads(f.read_text())
                except Exception:
                    continue
                m = e.get("metrics") or {}
                if not e.get("passed") or not all(m.get(k) for k in ("area", "delay", "cycles")):
                    continue
                en = (pw.get(f"eval:{job}:{f.parent.name}") or {}).get("e_circuit_fj")
                if en is None and e.get("eval_index") == best_eval:
                    en = (pw.get(f"winner:{job}") or {}).get("e_circuit_fj")
                pool.setdefault(key, []).append(dict(run=r["run"], eval=f.parent.name, metrics=m, energy=en))
    return start, e0, pool, reps


def _rows(pool_or_reps):
    return [("--", "start", ("start", None))] + [("no" if obj == "arp" else "yes", f"{'equiv' if arm == 'equiv' else 'task'}-{obj}", (arm, obj))
                                                 for obj in ORDER for arm in ("equiv", "task") if (arm, obj) in pool_or_reps]


def _emit(stem, rows, out, caption_note):
    """rows: [(energy_directed, label, {col: value|None|str})]; bold column minima in tex."""
    mins = {c: min((v[c] for _, _, v in rows if isinstance(v[c], (int, float))), default=None) for c in COLS}
    tex, md = list(TEX_HDR), [MD_HDR, "|---|---|---|---|---|---|---|---|---|---|"]
    for ed, label, v in rows:
        cells_t, cells_m = [], []
        for c in COLS:
            x = v[c]
            if isinstance(x, str):
                cells_t.append(x); cells_m.append(x.replace("\\,\\pm\\,", "±").replace("$", ""))
            elif x is None:
                cells_t.append("--"); cells_m.append("--")
            else:
                s = FMT[c].format(x)
                if c == "energy":
                    mant, ex = ("%.1e" % x).split("e"); s = f"${mant}{{\\times}}10^{{{int(ex)}}}$"
                cells_t.append(f"\\textbf{{{s}}}" if x == mins[c] else s); cells_m.append(FMT[c].format(x))
        tl = label.replace("equiv-", "equiv-").replace("arp", "\\arp{}").replace("e\\arp{}1", "\\earp{}").replace("e\\arp{}2", "$\\earp_2$") \
                  .replace("m\\arp{}1", "$\\marp_1$").replace("m\\arp{}2", "$\\marp_2$").replace("-energy", "-$E$")
        tex.append(f"{ed} & {tl} & " + " & ".join(cells_t) + " \\\\")
        md.append(f"| {ed} | {label} | " + " | ".join(cells_m) + " |")
    tex += ["\\bottomrule", "\\end{tabular}"]
    tex.insert(0, f"% {caption_note}")
    A.write(out, stem, tex, md)


def tables(profiles, out):
    start, e0, pool, reps = population(profiles)
    if start is None or not reps:
        print(f"no complete MatMul runs for profiles {profiles}; energy tables skipped")
        return
    if e0 is None:
        print("note: no measured energy for the start (python -m tasklevel.power --run <run>); energy columns stay empty")
    # Table 6: the winner by the in-loop objective across repetitions, one coherent design per row
    cells = A.cells_for(profiles, hdl="spire", objectives=tuple(ORDER))
    rows = [("--", "start", _vals(start, e0, start, e0))]
    for ed, label, (arm, obj) in _rows(reps)[1:]:
        c = cells.get(("matmul", arm, obj))
        if c and c["winner"]:
            rows.append((ed, label, _vals(c["winner"]["metrics"], c["winner"].get("energy"), start, e0)))
    _emit("table_energy_winners", rows, out, "Table 6: winner by the in-loop objective across repetitions (one design per row)")
    # Table 4: column-wise minima over every valid design of the row's repetitions
    rows = [("--", "start", _vals(start, e0, start, e0))]
    for ed, label, key in _rows(pool)[1:]:
        pts = [_vals(p["metrics"], p["energy"], start, e0) for p in pool[key]]
        rows.append((ed, label, {c: min((v[c] for v in pts if v[c] is not None), default=None) for c in COLS}))
    n_e = sum(1 for k in pool for p in pool[k] if p["energy"] is not None)
    if n_e < sum(len(v) for v in pool.values()):
        print(f"note: energy measured for {n_e} of {sum(len(v) for v in pool.values())} valid designs; "
              "run `python -m tasklevel.power --run <run> --population` for complete energy columns")
    _emit("table_energy_columnwise", rows, out, "Table 4: column-wise minima over all valid designs of the repetitions (rows are not single designs)")
    # Table 7: mean +- sample std over the repetitions' per-run bests
    rows = [("--", "start", _vals(start, e0, start, e0))]
    for ed, label, key in _rows(reps)[1:]:
        vs = [_vals(p["metrics"], p["energy"], start, e0) for p in reps[key]]
        row = {}
        for c in COLS:
            xs = [v[c] for v in vs if v[c] is not None]
            row[c] = None if not xs else xs[0] if len(xs) == 1 else f"${A.meanstd(xs, FMT[c] if c != 'energy' else '{:.3g}')}$"
        rows.append((ed, label, row))
    _emit("table_energy_meanstd", rows, out, "Table 7: mean +- sample standard deviation over the repetitions' per-run bests")


def fig_winners(profiles, out, suffix=""):
    """Fig. 4a / Fig. 6: measured energy of the ARP winners relative to the start, all benchmarks."""
    cells = A.cells_for(profiles, hdl="spire")
    data = []
    for b in BENCHES:
        for arm in ("equiv", "task"):
            c = cells.get((b.key, arm))
            if c and c["winner"] and c["winner"].get("energy") and c["winner"].get("start_energy"):
                data.append((b, arm, c["winner"]["energy"] / c["winner"]["start_energy"]))
    if not data:
        print("fig_energy skipped: no measured winner energies (python -m tasklevel.power --run <run>)")
        return
    plt = A.mpl()
    fig, ax = plt.subplots(figsize=(1.3 * len(BENCHES) + 1.2, 2.4))
    x, ticks, labels = 0.0, [], []
    for b in BENCHES:
        here = [(arm, v) for bb, arm, v in data if bb is b]
        if not here:
            continue
        for arm, v in here:
            ax.bar([x], [v], width=0.8, color=A.COLORS[arm], zorder=2)
            ax.text(x, v + 0.02, f"{v:.2f}", ha="center", va="bottom", fontsize=7)
            x += 1
        ticks.append(x - 0.5 * len(here) - 0.5)
        labels.append(b.label)
        x += 0.7
    ax.axhline(1.0, color="#b7b6ae", lw=0.8, zorder=1)
    ax.set_xticks(ticks); ax.set_xticklabels(labels)
    ax.set_ylabel("energy relative to start")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=A.COLORS["equiv"], label="equivalence"), Patch(color=A.COLORS["task"], label="task-level")],
              fontsize=7, frameon=False, ncol=2, loc="upper center", bbox_to_anchor=(0.5, 1.18))
    A.savefig(fig, out, f"fig_energy{suffix}")


def fig_matmul_reps(profiles, out):
    """Fig. 4b: MatMul task-level per-run bests that improve ARP, energy vs ARP (relative), linear fit."""
    start, e0, pool, reps = population(profiles)
    pts = [(A.arp(p["metrics"]) / A.arp(start), p["energy"] / e0) for p in reps.get(("task", "arp"), [])
           if p["energy"] and e0 and A.arp(p["metrics"]) < A.arp(start)] if start and e0 else []
    if len(pts) < 2:
        print("fig_energy_matmul_reps skipped: fewer than two MatMul task-level per-run bests with measured energy")
        return
    import numpy as np
    plt = A.mpl()
    xs, ys = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
    k, d = np.polyfit(xs, ys, 1)
    fig, ax = plt.subplots(figsize=(3.4, 2.6))
    ax.scatter(xs, ys, s=18, color=A.COLORS["task"], zorder=3)
    gx = np.linspace(xs.min(), xs.max(), 20)
    ax.plot(gx, k * gx + d, "--", color="#555", lw=1, label=f"fit: {k:.2f} x + {d:.2f}")
    ax.scatter([1], [1], marker="*", s=50, color=A.COLORS["start"], zorder=4, label="start")
    ax.set_xlabel("ARP relative to start"); ax.set_ylabel("energy relative to start")
    ax.legend(fontsize=7, frameon=False)
    A.savefig(fig, out, "fig_energy_matmul_reps")


def build(profiles, out=None, suffix=""):
    out = out or A.OUT
    fig_winners(profiles, out, suffix)
    if suffix:          # another model's runs: only the winners figure (Fig. 6)
        return
    fig_matmul_reps(profiles, out)
    tables(profiles, out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profiles", nargs="+", default=["arp", "energy_objectives"])
    ap.add_argument("--suffix", default="", help="output stem suffix; with a suffix only fig_energy is produced (e.g. --profiles glm_arp --suffix _glm)")
    a = ap.parse_args()
    build(a.profiles, suffix=a.suffix)


if __name__ == "__main__":
    main()
