"""Per-run report: tables and figures from one run's study_results.json (+ power.json if present).

    python -m tasklevel.report --profile arp            # the profile's pinned run
    python -m tasklevel.report --run arp_20261007_120000

Writes into data/<run>/: tables/tables.md, derived.json, figures/rel_cost.png, REPORT.md.
Every number comes from the uniform rescore of the per-run best (area, delay, cycles, reads,
writes) and the baseline of the same benchmark and HDL, so arp = area*cycles*delay is
comparable across arms. Energy appears when power.json exists for the run.
"""
from __future__ import annotations

import argparse
import json
import time

from . import common as U
from . import config as C
from .benches import BY_KEY
from .study import JOBS

C_START, C_EQUIV, C_TASK = "#898781", "#2a78d6", "#eb6834"


def arp(m: dict) -> float:
    return m["area"] * m["cycles"] * m["delay"]


def gather(rp: C.RunPaths) -> dict:
    """{(bench, hdl): {"start": metrics, "<arm>_<objective>": {...}}} for every rescored job."""
    sr = json.loads(rp.study_json.read_text())
    power = json.loads(rp.power_json.read_text()) if rp.power_json.exists() else {}
    cells: dict = {}
    for key, b in sr["baselines"].items():
        if b.get("status") == "ok":
            bench, hdl = key.rsplit("_", 1)
            e = (power.get(f"baseline:{key}") or {}).get("e_circuit_fj")
            cells[(bench, hdl)] = {"start": dict(b["metrics"], energy=e)}
    for job, r in sr["rescores"].items():
        if r.get("status") != "ok":
            continue
        spec = JOBS[job]
        cell = cells.setdefault((spec["bench"], spec["hdl"]), {})
        run = sr["runs"].get(job, {})
        e = (power.get(f"winner:{job}") or {}).get("e_circuit_fj")
        cell[f"{spec['arm']}_{spec['objective']}"] = dict(
            r["metrics"], energy=e, job=job, best_design=r.get("best_design"), target_delay=r.get("target_delay"),
            counters_frozen=r.get("counters_frozen"), n_evals=run.get("n_evals"), best_eval=run.get("best_eval"))
    return cells


def build(rp: C.RunPaths, p: dict) -> None:
    rp.ensure()
    cells = gather(rp)
    derived, md = {}, []
    md += [f"# Run {rp.name}  (profile {p['name']}, model {p.get('model')}, {p['max_steps']} steps)", "",
           "## Area-runtime product by track", "",
           "| benchmark | HDL | arm / objective | area um2 | period ps | cycles | reads | writes | arp | arp rel. | energy rel. | frozen |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for (bench, hdl), cell in sorted(cells.items()):
        if "start" not in cell:
            continue
        s = cell["start"]
        a0 = arp(s)
        label = BY_KEY[bench].label if bench in BY_KEY else bench
        rows = {"start": s} | {k: v for k, v in cell.items() if k != "start"}
        for arm, m in rows.items():
            rel = arp(m) / a0
            erel = (m["energy"] / s["energy"]) if (m.get("energy") and s.get("energy")) else None
            derived.setdefault(f"{bench}_{hdl}", {})[arm] = dict(area=m["area"], delay=m["delay"], cycles=m["cycles"],
                                                                 reads=m.get("reads"), writes=m.get("writes"), arp=arp(m),
                                                                 arp_rel=rel, energy=m.get("energy"), energy_rel=erel)
            md.append(f"| {label} | {hdl} | {arm} | {m['area']:.1f} | {m['delay']:.0f} | {m['cycles']:.0f} | "
                      f"{m.get('reads') if m.get('reads') is not None else '-'} | {m.get('writes') if m.get('writes') is not None else '-'} | "
                      f"{arp(m):.3e} | {rel:.3f} | {f'{erel:.3f}' if erel else '-'} | "
                      f"{m['counters_frozen'] if m.get('counters_frozen') is not None else ''} |")
    (rp.tables / "tables.md").write_text("\n".join(md) + "\n")
    (rp.root / "derived.json").write_text(json.dumps(derived, indent=1) + "\n")
    U.record(rp, "report", rp.tables / "tables.md", "per-run table")
    U.record(rp, "report", rp.root / "derived.json", "every derived number")

    # figure: relative arp per (bench, hdl) cell, bars per arm
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(max(5, 1.1 * len(cells) + 1), 2.8))
        x, ticks, labels = 0.0, [], []
        for (bench, hdl), cell in sorted(cells.items()):
            if "start" not in cell:
                continue
            a0 = arp(cell["start"])
            arms = [("start", 1.0, C_START)] + [(k, arp(v) / a0, C_EQUIV if k.startswith("equiv") else C_TASK)
                                                for k, v in sorted(cell.items()) if k != "start"]
            x0 = x
            for name, v, col in arms:
                ax.bar([x], [min(v, 1.5)], width=0.9, color=col, zorder=3)
                ax.text(x, min(v, 1.5) + 0.02, f"{v:.2f}", ha="center", va="bottom", fontsize=7, rotation=90)
                x += 1
            ticks.append((x0 + x - 1) / 2)
            labels.append(f"{BY_KEY[bench].label if bench in BY_KEY else bench}\n{hdl}")
            x += 0.6
        ax.axhline(1.0, color="#c3c2b7", lw=1, zorder=2)
        ax.set_xticks(ticks)
        ax.set_xticklabels(labels, fontsize=8)
        ax.set_ylim(0, 1.7)
        ax.set_ylabel("arp relative to start")
        ax.yaxis.grid(True, color="#e1e0d9", lw=0.6, zorder=0)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        fig.tight_layout()
        fig.savefig(rp.figures / "rel_cost.png", dpi=160)
        plt.close(fig)
        U.record(rp, "report", rp.figures / "rel_cost.png", "relative arp per cell")
    except Exception as e:  # a missing display/backend must not fail the run
        U.log(f"report: figure skipped ({e})")

    # REPORT.md: header + manifest of produced files + the table
    man = json.loads(rp.manifest.read_text()) if rp.manifest.exists() else {"files": []}
    sr = json.loads(rp.study_json.read_text())
    lines = [f"# REPORT — {rp.name}", "",
             f"- profile: `{p['name']}`  model: `{p.get('model')}`  steps: {p['max_steps']}",
             f"- jobs: {len(sr['runs'])} run, {sum(1 for r in sr['runs'].values() if r.get('status') == 'completed')} completed, "
             f"{sum(1 for r in sr['rescores'].values() if r.get('status') == 'ok')} rescored",
             f"- generated: {time.strftime('%Y-%m-%d %H:%M:%S')}", "", "## Files", ""]
    lines += [f"- `{f['path']}` ({f['stage']}) {f['note']}" for f in man["files"]]
    lines += ["", (rp.tables / "tables.md").read_text()]
    rp.report_md.write_text("\n".join(lines) + "\n")
    U.log(f"report -> {rp.report_md}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile")
    ap.add_argument("--run")
    a = ap.parse_args()
    if a.run:
        run = a.run
        prof = C.load_manifest()["runs"].get(run, {}).get("profile") or a.profile
    else:
        run = C.current_run(C.load_profile(a.profile))
        prof = a.profile
    build(C.RunPaths(run), C.load_profile(prof))


if __name__ == "__main__":
    main()
