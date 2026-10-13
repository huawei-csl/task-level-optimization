"""The movement-exponent dial: which MatMul design wins as alpha in movements^alpha x ARP varies.

Pools every valid evaluation (passing, costed) of the MatMul Spire task-level runs under the given
profiles -- the designs the agent produced under the arp, marp1 and marp2 objectives -- and
sweeps alpha from 0 to 3. For each alpha the pooled design with the lowest movements^alpha x ARP
is selected; contiguous alpha ranges that select the same design are its *regimes*.

    python -m tasklevel.analysis.alpha_dial                      # profiles: arp energy_objectives alpha_dial
    python -m tasklevel.analysis.alpha_dial --arms task equiv --stats

Outputs in data/analysis/:
    table_alpha_dial.{tex,md}  (Table 5) one row per regime: alpha range, the design's relative movements,
                               ARP, area, period, cycles (and with --stats its flip-flop/logic cell
                               multiplier and other cell counts from a yosys generic-cell pass), its source job and evaluation
    fig_alpha_dial.{png,pdf}   selected design's relative movements and ARP against alpha (step plot)
    fig_alpha_dial_stats.{png,pdf}  with --stats: flip-flop and logic cell counts against alpha (Fig. 5)
    alpha_dial_pool.json       the pooled points
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import config as C
from ..benches import BY_KEY
from ..study import JOBS
from . import _common as A

RE_RW = re.compile(r"TB_READS total=(\d+)[\s\S]*?TB_WRITES total=(\d+)")


POOL_OBJECTIVES = ("arp", "marp1", "marp2")


def pool(profiles, arms=("task",), objectives=POOL_OBJECTIVES) -> tuple[dict, list[dict]]:
    """(start metrics, points). A point: area, delay, cycles, reads, writes, job, run, eval, design."""
    start, pts, seen = None, [], set()
    for r in A.runs(profiles):
        sr = r["study"]
        base = sr["baselines"].get("matmul_spire")
        if base and base.get("status") == "ok" and start is None:
            start = base["metrics"]
        for job, rec in sr["runs"].items():
            spec = JOBS.get(job)
            if not spec or spec["bench"] != "matmul" or spec["hdl"] != "spire" or spec["arm"] not in arms \
                    or spec["objective"] not in objectives:
                continue
            if not rec.get("run_dir"):
                continue
            for f in sorted(Path(rec["run_dir"]).glob("eval_*/result.json")):
                try:
                    e = json.loads(f.read_text())
                except Exception:
                    continue
                m = e.get("metrics") or {}
                if not e.get("passed") or not all(m.get(k) for k in ("area", "delay", "cycles")):
                    continue
                rw = (m.get("reads"), m.get("writes"))
                if rw[0] is None:
                    mm = RE_RW.search(((e.get("correctness") or {}).get("sim_stdout") or ""))
                    if not mm:
                        continue
                    rw = (int(mm.group(1)), int(mm.group(2)))
                key = (round(m["area"], 3), round(m["delay"], 3), m["cycles"], rw[0], rw[1])
                if key in seen:
                    continue
                seen.add(key)
                pts.append(dict(area=m["area"], delay=m["delay"], cycles=m["cycles"], reads=rw[0], writes=rw[1],
                                job=job, run=r["run"], eval=f.parent.name,
                                design=str(f.parent / "workspace" / (e.get("design_file") or "design.py"))))
    return start, pts


def sweep(start: dict, pts: list[dict], alphas) -> list[dict]:
    """Regimes: [{a_lo, a_hi, point}] in increasing alpha."""
    def cost(p, a):
        return (p["reads"] + p["writes"]) ** a * p["area"] * p["cycles"] * p["delay"]
    regimes = []
    for a in alphas:
        best = min(pts, key=lambda p: cost(p, a))
        if regimes and regimes[-1]["point"] is best:
            regimes[-1]["a_hi"] = a
        else:
            regimes.append(dict(a_lo=a, a_hi=a, point=best))
    return regimes


def logic_stats(design: Path) -> dict:
    """Flip-flop, multiplier and other cell counts after a generic yosys pass (proc, flatten,
    simplemap of the FFs; no technology mapping). `design` is a Spire .py (compiled first) or Verilog."""
    if shutil.which("yosys") is None:
        return {}
    with tempfile.TemporaryDirectory(prefix="tl_stats_") as td:
        ws = Path(td)
        if design.suffix == ".py":
            from rtlscout.evaluation import _compile_spirehdl, SPIREHDL_VERILOG_OUTPUT
            for py in design.parent.glob("*.py"):
                shutil.copy2(py, ws / py.name)
            err, _ = _compile_spirehdl(ws, design.name)
            if err:
                return {"error": err[:200]}
            rtl = ws / SPIREHDL_VERILOG_OUTPUT
        else:
            rtl = design
        out = ws / "stat.json"
        # `tee -o` writes the JSON to a file: yosys' stdout is its log, which -q would silence
        script = (f"read_verilog -sv {rtl}; hierarchy -auto-top; proc; flatten; opt_clean; "
                  f"simplemap t:$*dff*; tee -q -o {out} stat -json")
        r = subprocess.run(["yosys", "-q", "-p", script], capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not out.exists():
            return {"error": (r.stderr or r.stdout)[-200:]}
        st = json.loads(out.read_text())
        cells = (st.get("design") or {}).get("num_cells_by_type") or {}
        ff = sum(n for t, n in cells.items() if re.match(r"\$_(S?DFF|DLATCH)", t))
        mul = sum(n for t, n in cells.items() if t == "$mul")
        return dict(ff=ff, mul=mul, logic=sum(cells.values()) - ff)


def build(profiles, arms=("task",), stats=False, out=None):
    out = out or A.OUT
    start, pts = pool(profiles, arms)
    if not start or not pts:
        print(f"no pooled MatMul points for profiles {profiles} (arms {arms}); nothing to do")
        return
    out.mkdir(parents=True, exist_ok=True)
    (out / "alpha_dial_pool.json").write_text(json.dumps(dict(start=start, points=pts), indent=1) + "\n")
    alphas = [round(i * 0.01, 2) for i in range(0, 301)]
    regimes = sweep(start, pts, alphas)
    m0 = start["reads"] + start["writes"]
    a0 = A.arp(start)
    tex = ["\\begin{tabular}{crrrrrr" + ("rrr" if stats else "") + "}", "\\toprule",
           "$\\alpha$ range & movements & \\arp & area & period & cycles" + (" & FFs & mult. & logic" if stats else "") + " \\\\", "\\midrule"]
    md = ["| α range | movements rel | arp rel | area rel | period rel | cycles rel |" + (" FFs | mult. | logic |" if stats else "") + " source |",
          "|---|---|---|---|---|---|" + ("---|---|---|" if stats else "") + "---|"]
    for rg in regimes:
        p = rg["point"]
        mv, ar = (p["reads"] + p["writes"]) / m0, A.arp(p) / a0
        dec = (p["area"] / start["area"], p["delay"] / start["delay"], p["cycles"] / start["cycles"])
        st = logic_stats(Path(p["design"])) if stats else {}
        rg["stats"] = st
        rng = f"{rg['a_lo']:.2f}--{rg['a_hi']:.2f}" if rg["a_lo"] != rg["a_hi"] else f"{rg['a_lo']:.2f}"
        tex.append(f"{rng} & {mv:.2f} & {ar:.2f} & " + " & ".join(f"{d:.2f}" for d in dec)
                   + (f" & {st.get('ff', '--')} & {st.get('mul', '--')} & {st.get('logic', '--')}" if stats else "") + " \\\\")
        md.append(f"| {rng} | {mv:.2f} | {ar:.2f} | " + " | ".join(f"{d:.2f}" for d in dec) + " |"
                  + (f" {st.get('ff', '--')} | {st.get('mul', '--')} | {st.get('logic', '--')} |" if stats else "") + f" {p['job']} {p['eval']} ({p['run']}) |")
    tex += ["\\bottomrule", "\\end{tabular}"]
    A.write(out, "table_alpha_dial", tex, md)
    print(f"pool: {len(pts)} distinct valid designs from {len({p['run'] for p in pts})} run(s); {len(regimes)} regime(s)")

    plt = A.mpl()
    fig, ax = plt.subplots(figsize=(4.2, 2.6))
    xs, mvs, ars = [], [], []
    for rg in regimes:
        p = rg["point"]
        xs += [rg["a_lo"], rg["a_hi"] + 0.01]
        mvs += [(p["reads"] + p["writes"]) / m0] * 2
        ars += [A.arp(p) / a0] * 2
    ax.plot(xs, mvs, color="#2a78d6", lw=1.6, label="movements (rel.)")
    ax.plot(xs, ars, color="#eb6834", lw=1.6, label="ARP (rel.)")
    for rg in regimes[1:]:
        ax.axvline(rg["a_lo"], color="#d8d7cf", lw=0.7, zorder=0)
    ax.set_xlabel("movement exponent α")
    ax.set_ylabel("relative to start")
    ax.set_xlim(0, 3)
    ax.legend(fontsize=7, frameon=False)
    A.savefig(fig, out, "fig_alpha_dial")

    if stats and all(rg["stats"].get("ff") is not None for rg in regimes):
        # Fig. 5: flip-flop and logic cell counts of the selected design against alpha
        fig, ax = plt.subplots(figsize=(4.2, 2.6))
        ax2 = ax.twinx()
        xs = []
        ffs, lgs = [], []
        for rg in regimes:
            xs += [rg["a_lo"], rg["a_hi"] + 0.01]
            ffs += [rg["stats"]["ff"]] * 2
            lgs += [rg["stats"]["logic"]] * 2
        ax.plot(xs, ffs, color="#2a78d6", lw=1.6, label="flip-flops")
        ax2.plot(xs, lgs, color="#eb6834", lw=1.6, label="logic cells")
        for rg in regimes[1:]:
            ax.axvline(rg["a_lo"], color="#d8d7cf", lw=0.7, zorder=0)
        ax.set_xlabel("movement exponent α"); ax.set_xlim(0, 3)
        ax.set_ylabel("flip-flops", color="#2a78d6"); ax2.set_ylabel("logic cells", color="#eb6834")
        ax2.spines["right"].set_visible(True)
        A.savefig(fig, out, "fig_alpha_dial_stats")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profiles", nargs="+", default=["arp", "energy_objectives", "alpha_dial"])
    ap.add_argument("--arms", nargs="+", default=["task"], choices=["task", "equiv"])
    ap.add_argument("--stats", action="store_true", help="add yosys flip-flop/logic counts per regime (needs yosys)")
    a = ap.parse_args()
    build(a.profiles, tuple(a.arms), a.stats)


if __name__ == "__main__":
    main()
