"""Energy-directed objectives on MatMul (Spire): winner table and design-population figure.

Objectives: arp (reference), marp1/marp2 (movements^alpha x arp, alpha = 1, 2), earp1/earp2
(measured energy^k x arp), energy (measured circuit energy alone); both tracks.

    python -m tasklevel.analysis.energy                       # profiles: energy_objectives + arp
    python -m tasklevel.analysis.energy --profiles energy_objectives arp alpha_dial

Outputs in data/analysis/:
    table_energy.{tex,md}   per objective and track: winner's relative ARP, movements, measured
                            energy, EDAP (all relative to the start); energy columns need power.json
    fig_energy.{png,pdf}    every measured design point of the runs (power.json --population):
                            relative ARP vs relative energy, one series per objective and track
"""
from __future__ import annotations

import argparse

from .. import config as C
from . import _common as A

ORDER = ["arp", "marp1", "marp2", "earp1", "earp2", "energy"]
LABEL = {"arp": "\\arp", "marp1": "\\marp{1}", "marp2": "\\marp{2}", "earp1": "\\earp{1}", "earp2": "\\earp{2}", "energy": "energy"}
MD_LABEL = {"arp": "arp", "marp1": "marp α=1", "marp2": "marp α=2", "earp1": "earp k=1", "earp2": "earp k=2", "energy": "energy"}


def build(profiles, out=None):
    out = out or A.OUT
    cells = A.cells_for(profiles, hdl="spire", objectives=tuple(ORDER))
    cells = {k: v for k, v in cells.items() if k[0] == "matmul"}
    A.warn_unvalidated(cells)
    if not cells:
        print(f"no complete MatMul runs for profiles {profiles}; nothing to do")
        return
    tex = ["\\begin{tabular}{llrrrr}", "\\toprule",
           "objective & track & \\arp & movements & energy & \\edap \\\\", "\\midrule"]
    md = ["| objective | track | arp rel | movements rel | energy rel | edap rel |", "|---|---|---|---|---|---|"]
    f = lambda v: f"{v:.2f}" if v is not None else "--"
    for obj in ORDER:
        for arm in ("equiv", "task"):
            c = cells.get(("matmul", arm, obj))
            if c is None or c["winner"] is None:
                continue
            w, s = c["winner"], c["start"]
            m0, m1 = A.movements(s), A.movements(w["metrics"])
            mrel = (m1 / m0) if (m0 and m1) else None
            erel = (w["energy"] / w["start_energy"]) if (w.get("energy") and w.get("start_energy")) else None
            edap = (erel * w["arp_rel"]) if erel else None
            tex.append(f"{LABEL[obj]} & {'equivalence' if arm == 'equiv' else 'task-level'} & {f(w['arp_rel'])} & {f(mrel)} & {f(erel)} & {f(edap)} \\\\")
            md.append(f"| {MD_LABEL[obj]} | {arm} | {f(w['arp_rel'])} | {f(mrel)} | {f(erel)} | {f(edap)} |")
    tex += ["\\bottomrule", "\\end{tabular}"]
    A.write(out, "table_energy", tex, md)

    # population figure from every measured point in the runs' power.json
    pts = {}       # (arm, objective) -> [(arp_rel, energy_rel)]
    for r in A.runs(profiles):
        pw = r["power"] or {}
        base = pw.get("baseline:matmul_spire")
        if not base or base.get("status") != "ok":
            continue
        for key, rec in pw.items():
            if rec.get("status") != "ok" or not (key.startswith("eval:") or key.startswith("winner:")):
                continue
            job = key.split(":")[1]
            if not job.startswith("matmul_spire_"):
                continue
            arm, obj = job.split("_")[2], job.split("_")[3]
            pts.setdefault((arm, obj), []).append((rec["adp"] / base["adp"], rec["e_circuit_fj"] / base["e_circuit_fj"]))
    if not pts:
        print("fig_energy skipped: no measured population (run `python -m tasklevel.power --run <run> --population`)")
        return
    plt = A.mpl()
    fig, ax = plt.subplots(figsize=(4.2, 3.2))
    markers = {"arp": "o", "marp1": "s", "marp2": "D", "earp1": "^", "earp2": "v", "energy": "x"}
    for (arm, obj), xy in sorted(pts.items()):
        xs, ys = zip(*xy)
        ax.scatter(xs, ys, s=14, marker=markers.get(obj, "o"), color=A.COLORS[arm], alpha=0.75, lw=0.8,
                   label=f"{MD_LABEL[obj]}, {arm}", facecolors="none" if arm == "equiv" else None)
    ax.scatter([1], [1], s=40, marker="*", color=A.COLORS["start"], zorder=5, label="start")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("ARP relative to start")
    ax.set_ylabel("measured energy relative to start")
    ax.grid(True, which="both", color="#e6e5de", lw=0.5)
    ax.legend(fontsize=6, frameon=False, ncol=2)
    A.savefig(fig, out, "fig_energy")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profiles", nargs="+", default=["energy_objectives", "arp"])
    a = ap.parse_args()
    build(a.profiles)


if __name__ == "__main__":
    main()
