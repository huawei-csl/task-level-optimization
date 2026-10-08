"""HDL independence: the ARP experiment with Spire and with Verilog starting designs side by side.

    python -m tasklevel.analysis.verilog_table            # spire from profile arp, verilog from verilog_twins
    python -m tasklevel.analysis.verilog_table --spire arp --verilog verilog_twins

Output in data/analysis/: table_verilog.{tex,md} -- per benchmark and HDL the start ARP and the
equivalence and task-level winners relative to it (best of the repetitions, with mean +- std of
the per-run bests), and the Verilog/Spire start ratio.
"""
from __future__ import annotations

import argparse

from ..benches import BENCHES
from . import _common as A


def build(spire_profiles, verilog_profiles, out=None):
    out = out or A.OUT
    sp = A.cells_for(spire_profiles, hdl="spire")
    vl = A.cells_for(verilog_profiles, hdl="verilog")
    A.warn_unvalidated({**{("s",) + k: v for k, v in sp.items()}, **{("v",) + k: v for k, v in vl.items()}})
    if not sp and not vl:
        print("no complete runs; nothing to do")
        return
    tex = ["\\begin{tabular*}{\\textwidth}{@{\\extracolsep{\\fill}}llrrr}", "\\toprule",
           " & HDL & start \\arp & equivalence & task-level \\\\", "\\midrule"]
    md = ["| benchmark | HDL | start arp | equiv rel (mean±std) | task rel (mean±std) |", "|---|---|---|---|---|"]

    def cell(cells, b, arm):
        c = cells.get((b.key, arm))
        if not c or not c["best_arp"]:
            return "--", "--"
        best = c["best_arp"] / c["start_arp"]
        ms = A.meanstd([r["arp_rel"] for r in c["reps"]])
        return (f"{best:.2f} {{\\scriptsize ${ms}$}}", f"{best:.2f} ({ms.replace(chr(92) + ',', '').replace(chr(92) + 'pm', '±')})")
    for b in BENCHES:
        for hdl, cells in (("Spire", sp), ("Verilog", vl)):
            c = cells.get((b.key, "task")) or cells.get((b.key, "equiv"))
            if c is None:
                continue
            eq_t, eq_m = cell(cells, b, "equiv")
            tk_t, tk_m = cell(cells, b, "task")
            tex.append(f"{b.tex if hdl == 'Spire' else ''} & {hdl} & {A.sci(c['start_arp'])} & {eq_t} & {tk_t} \\\\")
            md.append(f"| {b.label if hdl == 'Spire' else ''} | {hdl} | {c['start_arp']:.3g} | {eq_m} | {tk_m} |")
        s, v = sp.get((b.key, "task")), vl.get((b.key, "task"))
        if s and v:
            md.append(f"| | start ratio V/S | {v['start_arp'] / s['start_arp']:.2f} | | |")
    tex += ["\\bottomrule", "\\end{tabular*}"]
    A.write(out, "table_verilog", tex, md)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spire", nargs="+", default=["arp"])
    ap.add_argument("--verilog", nargs="+", default=["verilog_twins"])
    a = ap.parse_args()
    build(a.spire, a.verilog)


if __name__ == "__main__":
    main()
