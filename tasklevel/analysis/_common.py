"""Shared loaders for the cross-run analyses: cells, repetitions, winners, formatting.

Terminology (as in the paper): a *cell* is one benchmark and one track (arm) -- here also one
HDL and one objective; each run of a profile is one *repetition* of its cells; the *per-run best*
is the rescored best design of one run; the *winner* is the eligible per-run best with the lowest
objective across the repetitions. Eligibility: the uniform rescore succeeded, the independent
testbench passed (data/<run>/validation.json, when the run was validated) and, on the equivalence
track, the counters are frozen (cycles, reads and writes equal the start's).
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from .. import config as C
from ..benches import BENCHES, BY_KEY
from ..study import JOBS

OUT = C.DATA / "analysis"


def arp(m: dict) -> float:
    return float(m["area"]) * float(m["cycles"]) * float(m["delay"])


def movements(m: dict) -> float | None:
    if m.get("reads") is None or m.get("writes") is None:
        return None
    return float(m["reads"]) + float(m["writes"])


def load_run(run: str) -> dict:
    rp = C.RunPaths(run)
    d = dict(run=run, study=json.loads(rp.study_json.read_text()) if rp.study_json.exists() else None,
             validation=json.loads((rp.root / "validation.json").read_text()) if (rp.root / "validation.json").exists() else None,
             power=json.loads(rp.power_json.read_text()) if rp.power_json.exists() else None)
    return d


def runs(profiles, status="complete") -> list[dict]:
    out = [load_run(r) for r in C.runs_for(profiles, status)]
    return [r for r in out if r["study"]]


def eligible(rep: dict) -> bool:
    if rep["validated"] is False:
        return False
    if rep["arm"] == "equiv" and rep.get("counters_frozen") is False:
        return False
    return True


def cells_for(profiles, hdl: str | None = None, objectives=("arp",), status="complete") -> dict:
    """Cells across the complete runs of the given profiles.

    Key: (bench, arm) when `hdl` is fixed, else (bench, hdl, arm); objectives other than "arp"
    make the key (bench[, hdl], arm, objective). Value: dict(start, start_arp, reps, winner,
    best_arp, unvalidated) where reps is the list of per-run bests (one per run, eligible or not).
    """
    cells: dict = {}
    for r in runs(profiles, status):
        sr, val, pw = r["study"], r["validation"] or {}, r["power"] or {}
        for job, rs in sr["rescores"].items():
            spec = JOBS.get(job)
            if spec is None or rs.get("status") != "ok":
                continue
            if hdl and spec["hdl"] != hdl:
                continue
            if spec["objective"] not in objectives:
                continue
            base = (sr["baselines"].get(f"{spec['bench']}_{spec['hdl']}") or {})
            if base.get("status") != "ok":
                continue
            key = [spec["bench"]] + ([] if hdl else [spec["hdl"]]) + [spec["arm"]] + \
                  ([] if objectives == ("arp",) else [spec["objective"]])
            key = tuple(key)
            cell = cells.setdefault(key, dict(bench=spec["bench"], hdl=spec["hdl"], arm=spec["arm"],
                                              objective=spec["objective"], start=base["metrics"],
                                              start_arp=arp(base["metrics"]), reps=[]))
            v = val.get(job)
            rep = dict(run=r["run"], job=job, arm=spec["arm"], metrics=rs["metrics"], arp=arp(rs["metrics"]),
                       arp_rel=arp(rs["metrics"]) / cell["start_arp"], best_design=rs.get("best_design"),
                       target_delay=rs.get("target_delay"), counters_frozen=rs.get("counters_frozen"),
                       validated=(None if v is None else v.get("status") == "pass"),
                       n_evals=(sr["runs"].get(job) or {}).get("n_evals"),
                       energy=(pw.get(f"winner:{job}") or {}).get("e_circuit_fj"),
                       start_energy=(pw.get(f"baseline:{spec['bench']}_{spec['hdl']}") or {}).get("e_circuit_fj"))
            cell["reps"].append(rep)
    for cell in cells.values():
        ok = [x for x in cell["reps"] if eligible(x)]
        scored = [(objective_value(x, cell["objective"]), x) for x in ok]
        if any(v is None for v, _ in scored):
            print(f"note: {cell['bench']} {cell['arm']} {cell['objective']}: no measured energy for "
                  f"{[x['run'] for v, x in scored if v is None]} (python -m tasklevel.power --run <run>); "
                  "selecting the winner by ARP instead")
            scored = [(x["arp"], x) for x in ok]
        cell["winner"] = min(scored, key=lambda t: t[0])[1] if scored else None
        cell["best_arp"] = cell["winner"]["arp"] if scored else None
        cell["unvalidated"] = [x["run"] for x in cell["reps"] if x["validated"] is None]
    return cells


def objective_value(rep: dict, objective: str) -> float | None:
    """The in-loop objective of a rescored design, for picking a cell's winner across repetitions:
    arp; marpN = movements^N x arp; earpN = energy^N x arp; energy. None if a needed measurement is missing."""
    a = rep["arp"]
    if objective == "arp":
        return a
    if objective.startswith("marp"):
        m = movements(rep["metrics"])
        return None if m is None else m ** float(objective[4:]) * a
    e = rep.get("energy")
    if e is None:
        return None
    if objective.startswith("earp"):
        return e ** float(objective[4:]) * a
    if objective == "energy":
        return e
    raise ValueError(objective)


def warn_unvalidated(cells: dict) -> None:
    pend = sorted({(c["bench"], c["arm"], r) for c in cells.values() for r in c["unvalidated"]})
    if pend:
        print(f"note: {len(pend)} per-run best(s) not yet validated (python -m tasklevel.validate --run <run>); "
              "they count as eligible until a validation says otherwise")


def meanstd(vals, fmt: str = "{:.2f}") -> str:
    """mean +- sample standard deviation (n-1), as the paper reports; a single value stands alone."""
    vals = [v for v in vals if v is not None]
    if not vals:
        return "--"
    if len(vals) == 1:
        return fmt.format(vals[0])
    return f"{fmt.format(statistics.mean(vals))}\\,\\pm\\,{fmt.format(statistics.stdev(vals))}"


def sci(v: float) -> str:
    mant, ex = ("%.2e" % v).split("e")
    e = int(ex)
    return f"${mant}{{\\times}}10^{{{e}}}$"


def write(out_dir: Path, stem: str, tex: list[str] | None, md: list[str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if tex is not None:
        (out_dir / f"{stem}.tex").write_text("\n".join(["% generated by tasklevel.analysis -- do not hand-edit"] + tex) + "\n")
    (out_dir / f"{stem}.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    print(f"-> {out_dir / stem}.md" + (f", .tex" if tex is not None else ""))


def savefig(fig, out_dir: Path, stem: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / f"{stem}.png", dpi=180, bbox_inches="tight")
    fig.savefig(out_dir / f"{stem}.pdf", bbox_inches="tight")
    print(f"-> {out_dir / stem}.png/.pdf")


def mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
    return plt


COLORS = {"start": "#898781", "equiv": "#2a78d6", "task": "#eb6834"}
