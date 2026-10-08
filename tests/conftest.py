"""Shared fixtures: an isolated data root and a synthetic pair of runs.

Nothing here calls an LLM or the EDA flow; the synthetic runs have the exact study_results.json /
validation.json / power.json shapes the launcher writes, so the analysis and report code is tested
on realistic inputs. Tests that need Verilator or the engine skip when they are unavailable.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tasklevel import config as C
from tasklevel.analysis import _common as A

START = dict(matmul_spire=dict(area=1000.0, delay=500.0, cycles=10000.0, reads=4096.0, writes=256.0),
             jpeg_spire=dict(area=4000.0, delay=600.0, cycles=50000.0, reads=20000.0, writes=20000.0),
             matmul_verilog=dict(area=1050.0, delay=500.0, cycles=10000.0, reads=4096.0, writes=256.0))


def scaled(start: dict, arp_rel: float, movements_rel: float = 1.0, cycles_rel: float | None = None) -> dict:
    """Metrics with the requested relative ARP (via area) and movements (via reads)."""
    m = dict(start)
    if cycles_rel is not None:
        m["cycles"] = start["cycles"] * cycles_rel
        m["area"] = start["area"] * arp_rel / cycles_rel
    else:
        m["area"] = start["area"] * arp_rel
    m["reads"] = start["reads"] * movements_rel
    return m


def write_run(run: str, profile: str, jobs: dict, validation: dict | None = None, power: dict | None = None,
              status: str = "complete", model: str = "fake:model") -> C.RunPaths:
    """jobs: job id -> dict(metrics=..., frozen=None|bool, n_evals=int, evals=[(passed, metrics, stdout)])"""
    rp = C.RunPaths(run).ensure()
    baselines, runs, rescores = {}, {}, {}
    from tasklevel.study import JOBS
    for job, j in jobs.items():
        spec = JOBS[job]
        key = f"{spec['bench']}_{spec['hdl']}"
        baselines[key] = dict(status="ok", passed=True, cost_value=1.0, metrics=START[key])
        run_dir = rp.runs / job / "bench" / "model" / "ts"
        run_dir.mkdir(parents=True, exist_ok=True)
        for i, (passed, m, stdout) in enumerate(j.get("evals", []), start=1):
            ev = run_dir / f"eval_{i}"
            (ev / "workspace").mkdir(parents=True, exist_ok=True)
            (ev / "workspace" / "design.py").write_text("# design\n")
            (ev / "result.json").write_text(json.dumps(dict(passed=passed, cost_value=1.0, metrics=m, eval_index=i,
                                                            design_file="design.py", target_delay=None,
                                                            correctness=dict(sim_stdout=stdout))))
        best = run_dir / "best_design" / "design.py"
        best.parent.mkdir(exist_ok=True)
        best.write_text("# best\n")
        runs[job] = dict(bench=spec["bench"], hdl=spec["hdl"], arm=spec["arm"], objective=spec["objective"],
                         run_dir=str(run_dir), status="completed", completed=True, n_evals=j.get("n_evals", 5),
                         best_cost=1.0, best_eval=1, best_design_file="design.py")
        rs = dict(status="ok", passed=True, cost_value=1.0, metrics=j["metrics"], target_delay=500.0, best_design=str(best))
        if spec["arm"] == "equiv":
            rs["counters_frozen"] = j.get("frozen", True)
        rescores[job] = rs
    rp.study_json.write_text(json.dumps(dict(study=dict(profile=profile, model=model, max_steps=40),
                                             baselines=baselines, runs=runs, rescores=rescores)))
    if validation is not None:
        (rp.root / "validation.json").write_text(json.dumps(validation))
    if power is not None:
        rp.power_json.write_text(json.dumps(power))
    C.register_run(dict(name=profile, tag=profile, model=model, max_steps=40), run, status=status, jobs=list(jobs))
    return rp


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """Point the package at an empty data root (manifest, runs, analysis output)."""
    monkeypatch.setattr(C, "DATA", tmp_path)
    monkeypatch.setattr(C, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(A, "OUT", tmp_path / "analysis")
    return tmp_path


@pytest.fixture
def two_runs(data_root):
    """Two 'arp' repetitions where the validation gate and the frozen-counter check each change a winner."""
    s = START["matmul_spire"]
    j = START["jpeg_spire"]
    write_run("arp_1", "arp",
              {"matmul_spire_equiv_arp": dict(metrics=scaled(s, 0.8), frozen=True),
               "matmul_spire_task_arp": dict(metrics=scaled(s, 0.5, cycles_rel=0.5)),
               "jpeg_spire_equiv_arp": dict(metrics=scaled(j, 0.9), frozen=True),
               "jpeg_spire_task_arp": dict(metrics=scaled(j, 0.6))},
              validation={k: dict(status="pass", total_checks=1_000_000, errors=0) for k in
                          ("matmul_spire_equiv_arp", "matmul_spire_task_arp", "jpeg_spire_equiv_arp", "jpeg_spire_task_arp")})
    write_run("arp_2", "arp",
              {"matmul_spire_equiv_arp": dict(metrics=scaled(s, 0.7), frozen=False),      # thawed counters -> not eligible
               "matmul_spire_task_arp": dict(metrics=scaled(s, 0.3)),                      # fails tb_v2 -> not eligible
               "jpeg_spire_equiv_arp": dict(metrics=scaled(j, 0.85), frozen=True),
               "jpeg_spire_task_arp": dict(metrics=scaled(j, 0.4))},
              validation={"matmul_spire_task_arp": dict(status="FAIL", total_checks=1_000_000, errors=3),
                          "matmul_spire_equiv_arp": dict(status="pass"), "jpeg_spire_equiv_arp": dict(status="pass"),
                          "jpeg_spire_task_arp": dict(status="pass")})
    # an incomplete run must be ignored by every analysis
    write_run("arp_3", "arp", {"matmul_spire_task_arp": dict(metrics=scaled(s, 0.01))}, status="incomplete")
    return data_root
