"""Measured circuit energy of a run's designs (gate-level simulation of the synthesized netlist).

Per design point: re-synthesize at the recorded target delay, gate-level-simulate the netlist over
the full benchmark workload with a per-net VCD, take the real-clock power split, and

    energy_fj = power_total x cycles x delay x 1e3        (W x ps -> fJ)
    edap      = energy_fj x area x cycles x delay

Modes:  winners (default)  every starting design + every rescored per-run best of the run
        --population       additionally every valid (passing, costed) evaluation of every job

    python -m tasklevel.power --profile arp                  # the profile's pinned run
    python -m tasklevel.power --run arp_20261007_120000 --population

Output: data/<run>/power.json, keyed `baseline:<bench>_<hdl>`, `winner:<job>`, `eval:<job>:<eval_N>`.
Resumable: measured keys are skipped. Synthesis is a fresh Yosys process per point (no in-process
state can drift between points). Needs the EDA tools of the rtlscout container.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import common as U
from . import config as C
from .benches import BY_KEY
from .study import JOBS


def measure(design: Path, bench_dir: Path, target_delay: float, cycles: float, language: str) -> dict:
    from rtlscout.evaluation import _compile_spirehdl, SPIREHDL_VERILOG_OUTPUT
    from rtlscout.cost import _tb_output_parser
    from rtlscout.tech_eval.ppa_extract.core.ppa_extraction import get_ppa
    with tempfile.TemporaryDirectory(prefix="tl_power_") as td:
        ws = Path(td)
        for dat in sorted(bench_dir.glob("*.dat")):
            shutil.copy2(dat, ws / dat.name)
        shutil.copy2(bench_dir / "tb.sv", ws / "tb.sv")
        if language == "spirehdl":
            for py in design.parent.glob("*.py"):          # designs may import sibling helper modules
                shutil.copy2(py, ws / py.name)
            err, _ = _compile_spirehdl(ws, design.name)
            if err:
                raise RuntimeError(f"spire compile failed: {err[:300]}")
            rtl = ws / SPIREHDL_VERILOG_OUTPUT
        else:
            rtl = ws / f"design{design.suffix}"
            shutil.copy2(design, rtl)
        top = json.loads((bench_dir / "metadata.json").read_text())["module_name"]
        ppa = get_ppa(rtl_path=str(rtl), target_delay=float(target_delay), worker_path=str(ws / "ppa"),
                      top_module_name=top, run_verilator=True, tb_filename=str(ws / "tb.sv"), tb_name="tb",
                      save_vcd=True, use_vcd_for_power=True, output_parser=_tb_output_parser,
                      run_in_worker_path=True, data_files=[str(d) for d in sorted(bench_dir.glob("*.dat"))])
        runtime = float(cycles) * ppa["delay"]
        energy_fj = ppa["power_total"] * runtime * 1e3
        return dict(status="ok", design=str(design), target_delay=float(target_delay),
                    area_um2=ppa["area"], delay_ps=ppa["delay"], cycles=float(cycles), runtime_ps=runtime,
                    power_internal=ppa["power_internal"], power_switching=ppa["power_switching"],
                    power_leakage=ppa["power_leakage"], power_total=ppa["power_total"],
                    e_circuit_fj=energy_fj, adp=ppa["area"] * float(cycles) * ppa["delay"],
                    edap=energy_fj * ppa["area"] * float(cycles) * ppa["delay"])


def points(rp: C.RunPaths, population: bool) -> list:
    """(key, bench_dir, design, target_delay, cycles, language)"""
    sr = json.loads(rp.study_json.read_text())
    pts = []
    for key, b in sr["baselines"].items():
        if b.get("status") != "ok":
            continue
        bench, hdl = key.rsplit("_", 1)
        tdir = BY_KEY[bench].dir(hdl)
        meta = json.loads((tdir / "metadata.json").read_text())
        pts.append((f"baseline:{key}", tdir, tdir / meta["starting_point"], 500.0, b["metrics"]["cycles"], meta.get("language", "spirehdl")))
    for job, r in sr["rescores"].items():
        if r.get("status") != "ok":
            continue
        spec = JOBS[job]
        bdir = BY_KEY[spec["bench"]].dir(spec["hdl"], equiv=(spec["arm"] == "equiv"))
        lang = "spirehdl" if spec["hdl"] == "spire" else "verilog"
        pts.append((f"winner:{job}", bdir, Path(r["best_design"]), r.get("target_delay") or 500.0, r["metrics"]["cycles"], lang))
        if population:
            run_dir = Path(sr["runs"][job]["run_dir"])
            for ev in sorted(run_dir.glob("eval_*/result.json")):
                e = json.loads(ev.read_text())
                m = e.get("metrics") or {}
                if e.get("passed") and all(m.get(k) for k in ("area", "delay", "cycles")):
                    pts.append((f"eval:{job}:{ev.parent.name}", bdir, ev.parent / "workspace" / e["design_file"],
                                e.get("target_delay") or 500.0, m["cycles"], lang))
    return pts


def run_for(rp: C.RunPaths, p: dict, population: bool = False) -> None:
    rp.ensure()
    results = json.loads(rp.power_json.read_text()) if rp.power_json.exists() else {}
    pts = points(rp, population)
    todo = [x for x in pts if results.get(x[0], {}).get("status") != "ok"]
    workers = int(p.get("power_workers", 4))
    U.log(f"power: {len(pts)} points, {len(todo)} to measure, {workers} workers")

    def one(x):
        key, bdir, design, td, cyc, lang = x
        try:
            return key, measure(design, bdir, td, cyc, lang)
        except Exception as e:
            return key, dict(status=f"failed: {e}", design=str(design))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for key, rec in (f.result() for f in as_completed([ex.submit(one, x) for x in todo])):
            results[key] = rec
            U.write_json_atomic(rp.power_json, results)
            U.log(f"power: {key}: {rec['status'][:60]}" + (f"  E={rec['e_circuit_fj']:.3g} fJ" if rec["status"] == "ok" else ""))
    U.record(rp, "power", rp.power_json, "measured circuit energy per design point")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile")
    ap.add_argument("--run")
    ap.add_argument("--population", action="store_true")
    a = ap.parse_args()
    run = a.run or C.current_run(C.load_profile(a.profile))
    prof = C.load_manifest()["runs"].get(run, {}).get("profile") or a.profile
    run_for(C.RunPaths(run), C.load_profile(prof), a.population)


if __name__ == "__main__":
    main()
