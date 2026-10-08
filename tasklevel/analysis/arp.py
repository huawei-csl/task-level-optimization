"""ARP results across repetitions: winner table, decomposition, repetition statistics, figure.

    python -m tasklevel.analysis.arp                       # profiles: arp (Spire)
    python -m tasklevel.analysis.arp --profiles glm_arp    # another model's runs
    python -m tasklevel.analysis.arp --hdl verilog --profiles verilog_twins

Outputs in data/analysis/:
    table_arp.{tex,md}        per benchmark: start ARP, equivalence and task-level winner (relative),
                              task/equivalence ratio, and the winner's area / period / cycles ratios
    table_arp_stats.{tex,md}  mean +- std of the per-run bests' relative ARP, with n, per cell
    fig_arp.{png,pdf}         per benchmark: per-run bests (dots) and winner (bar) relative to start (Fig. 2)
    fig_decomp.{png,pdf}      each winner's area, period and cycles relative to the start (Fig. 3)
"""
from __future__ import annotations

import argparse

from ..benches import BENCHES
from . import _common as A


def build(profiles, hdl="spire", out=None, suffix=""):
    out = out or A.OUT
    cells = A.cells_for(profiles, hdl=hdl)
    A.warn_unvalidated(cells)
    if not cells:
        print(f"no complete runs for profiles {profiles} (hdl={hdl}); nothing to do")
        return
    tex = ["\\begin{tabular}{lrrrrrrr}", "\\toprule",
           " & start \\arp & equiv. & task & task/equiv. & \\multicolumn{3}{c}{task winner: area / period / cycles} \\\\", "\\midrule"]
    md = [f"| benchmark ({hdl}) | start arp | equiv rel | task rel | task/equiv | area | period | cycles |",
          "|---|---|---|---|---|---|---|---|"]
    stats_tex = ["\\begin{tabular}{lcccc}", "\\toprule",
                 " & \\multicolumn{2}{c}{equivalence} & \\multicolumn{2}{c}{task-level} \\\\",
                 " & rel. \\arp & $n$ & rel. \\arp & $n$ \\\\", "\\midrule"]
    stats_md = [f"| benchmark ({hdl}) | equiv mean±std | n | task mean±std | n |", "|---|---|---|---|---|"]
    rows = []
    for b in BENCHES:
        eq, tk = cells.get((b.key, "equiv")), cells.get((b.key, "task"))
        if eq is None and tk is None:
            continue
        start_arp = (tk or eq)["start_arp"]
        s = (tk or eq)["start"]

        def rel(c):
            return (c["best_arp"] / start_arp) if (c and c["best_arp"]) else None
        req, rtk = rel(eq), rel(tk)
        w = tk["winner"] if tk and tk["winner"] else None
        dec = (w["metrics"]["area"] / s["area"], w["metrics"]["delay"] / s["delay"], w["metrics"]["cycles"] / s["cycles"]) if w else None
        f = lambda v: f"{v:.2f}" if v is not None else "--"
        ratio = (rtk / req) if (req and rtk) else None
        tex.append(f"{b.tex} & {A.sci(start_arp)} & {f(req)} & {f(rtk)} & {f(ratio)} & "
                   + (" & ".join(f"{d:.2f}" for d in dec) if dec else "-- & -- & --") + " \\\\")
        md.append(f"| {b.label} | {start_arp:.3g} | {f(req)} | {f(rtk)} | {f(ratio)} | "
                  + (" | ".join(f"{d:.2f}" for d in dec) if dec else "-- | -- | --") + " |")
        se = A.meanstd([r["arp_rel"] for r in eq["reps"]]) if eq else "--"
        st = A.meanstd([r["arp_rel"] for r in tk["reps"]]) if tk else "--"
        stats_tex.append(f"{b.tex} & ${se}$ & {len(eq['reps']) if eq else 0} & ${st}$ & {len(tk['reps']) if tk else 0} \\\\")
        stats_md.append(f"| {b.label} | {se.replace(chr(92) + ',', '').replace(chr(92) + 'pm', '±')} | {len(eq['reps']) if eq else 0} | "
                        f"{st.replace(chr(92) + ',', '').replace(chr(92) + 'pm', '±')} | {len(tk['reps']) if tk else 0} |")
        rows.append((b, eq, tk))
    tex += ["\\bottomrule", "\\end{tabular}"]
    stats_tex += ["\\bottomrule", "\\end{tabular}"]
    A.write(out, f"table_arp{suffix}", tex, md)
    A.write(out, f"table_arp_stats{suffix}", stats_tex, stats_md)

    plt = A.mpl()
    fig, ax = plt.subplots(figsize=(1.3 * len(rows) + 1.2, 2.6))
    x = 0.0
    ticks, labels = [], []
    for b, eq, tk in rows:
        for arm, c in (("equiv", eq), ("task", tk)):
            if c is None:
                x += 1
                continue
            v = (c["best_arp"] / c["start_arp"]) if c["best_arp"] else None
            if v is not None:
                ax.bar([x], [v], width=0.8, color=A.COLORS[arm], alpha=0.85, zorder=2)
            for r in c["reps"]:
                ax.plot([x], [r["arp_rel"]], "o", ms=3.2, mfc="white" if not A.eligible(r) else "black",
                        mec="black", mew=0.6, zorder=3)
            x += 1
        ticks.append(x - 1.5)
        labels.append(b.label)
        x += 0.7
    ax.axhline(1.0, color="#b7b6ae", lw=0.8, zorder=1)
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels)
    ax.set_ylabel("ARP relative to start")
    ax.set_ylim(0, max(1.15, ax.get_ylim()[1]))
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    ax.legend(handles=[Patch(color=A.COLORS["equiv"], label="equivalence winner"), Patch(color=A.COLORS["task"], label="task-level winner"),
                       Line2D([], [], marker="o", ls="", mfc="black", mec="black", ms=3.2, label="per-run best"),
                       Line2D([], [], marker="o", ls="", mfc="white", mec="black", ms=3.2, label="per-run best, not eligible")],
              fontsize=7, frameon=False, ncol=2, loc="upper center", bbox_to_anchor=(0.5, 1.22))
    A.savefig(fig, out, f"fig_arp{suffix}")

    # Fig. 3: decomposition of each winner into area, period and cycles relative to the start
    fig, axes = plt.subplots(1, 3, figsize=(1.3 * len(rows) + 2.5, 2.4), sharey=False)
    for ax, (fld, name) in zip(axes, (("area", "area"), ("delay", "period"), ("cycles", "cycles"))):
        x, ticks, labels = 0.0, [], []
        for b, eq, tk in rows:
            for arm, c in (("equiv", eq), ("task", tk)):
                if c and c["winner"]:
                    v = c["winner"]["metrics"][fld] / c["start"][fld]
                    ax.bar([x], [v], width=0.8, color=A.COLORS[arm], zorder=2)
                x += 1
            ticks.append(x - 1.5)
            labels.append(b.label)
            x += 0.7
        ax.axhline(1.0, color="#b7b6ae", lw=0.8, zorder=1)
        ax.set_xticks(ticks)
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
        ax.set_title(f"{name} / start", fontsize=8)
        ax.set_yscale("log")
    fig.tight_layout()
    A.savefig(fig, out, f"fig_decomp{suffix}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profiles", nargs="+", default=["arp"])
    ap.add_argument("--hdl", default="spire", choices=["spire", "verilog"])
    ap.add_argument("--suffix", default="", help="appended to output file stems (e.g. _glm)")
    a = ap.parse_args()
    build(a.profiles, a.hdl, suffix=a.suffix)


if __name__ == "__main__":
    main()
