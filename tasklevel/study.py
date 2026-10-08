"""Study launcher: starting-point baselines, agent runs, and the uniform rescore of every per-run best.

One *job* = one agent run on one benchmark variant under one objective, e.g.
`matmul_spire_task_arp` (task-level track, Spire start, ARP objective) or
`qr8_verilog_equiv_arp` (equivalence track, Verilog start). A profile (profiles/<name>.yaml)
selects the jobs, the model and the step budget; repetitions are separate runs of the same
profile (`run_all --new-run`, or `--repeat N`).

Objectives:  arp   = area x cycles x period          (area_runtime_product)
             marpN = movements^N x arp               (access_area_runtime_product, --energy-exp N)
             earpN = energy^N x arp, measured energy (energy_area_runtime_product, --energy-exp N)
             energy = measured circuit energy        (energy)

Per job the launcher records in data/<run>/study_results.json:
  baselines[<bench>_<hdl>]  the starting design measured under the rescore metric
  runs[job]                 run dir, status, best cost/eval as the agent saw them
  rescores[job]             the per-run best re-measured under the rescore metric at the winning
                            target delay (area, delay, cycles, reads/writes/energy); for the
                            equivalence track also `counters_frozen` (cycles/reads/writes == start)

Resumable: completed jobs and recorded baselines/rescores are skipped on re-invocation.
Usage (normally via `python -m tasklevel.run_all`):
    python -m tasklevel.study --profile arp [--only job1,job2] [--dry-run]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from . import common as U
from . import config as C
from .benches import BENCHES, BY_KEY, HDLS, LANGUAGE, rescore_metric

# ---- objectives --------------------------------------------------------------------------------
OBJECTIVES = {
    # objective -> (rtlscout cost metric, --energy-exp, needs gate-level netlist sim)
    "arp":    ("area_runtime_product", None, False),
    "marp1":  ("access_area_runtime_product", 1.0, False),
    "marp2":  ("access_area_runtime_product", 2.0, False),
    "earp1":  ("energy_area_runtime_product", 1.0, True),
    "earp2":  ("energy_area_runtime_product", 2.0, True),
    "energy": ("energy", None, True),
}
ARMS = ("equiv", "task")


def all_jobs() -> dict:
    """job id -> dict(bench, hdl, arm, objective). Every (bench, hdl, arm) x arp, plus the MatMul
    movement/energy objectives (Spire, both tracks) used by the energy and alpha-dial experiments."""
    jobs = {}
    for b in BENCHES:
        for hdl in HDLS:
            for arm in ARMS:
                jobs[f"{b.key}_{hdl}_{arm}_arp"] = dict(bench=b.key, hdl=hdl, arm=arm, objective="arp")
    for obj in ("marp1", "marp2", "earp1", "earp2", "energy"):
        for arm in ARMS:
            jobs[f"matmul_spire_{arm}_{obj}"] = dict(bench="matmul", hdl="spire", arm=arm, objective=obj)
    return jobs


JOBS = all_jobs()


def select_jobs(patterns) -> list[str]:
    """Expand profile job patterns (exact ids or fnmatch globs) in table order."""
    import fnmatch
    if isinstance(patterns, str):
        patterns = [patterns]
    out = []
    for j in JOBS:
        if any(fnmatch.fnmatch(j, p) for p in patterns) and j not in out:
            out.append(j)
    unknown = [p for p in patterns if not any(fnmatch.fnmatch(j, p) for j in JOBS)]
    if unknown:
        raise SystemExit(f"profile jobs match nothing: {unknown}; known jobs: {', '.join(JOBS)}")
    return out


# ---- helpers -----------------------------------------------------------------------------------
_RUNDIR_RE = re.compile(r"Results stored in:\s*(\S+)")


def _parse_eval_json(text: str) -> dict:
    i = text.find("{")
    if i < 0:
        return {}
    try:
        return json.loads(text[i:])
    except Exception:
        return {}


def eval_design(design: Path, bench_dir: Path, metric: str, log: Path, profile: dict,
                target_delay: float | None = None, energy_exp: float | None = None,
                netlist_sim: bool = False) -> dict:
    """Score one design with the engine's evaluator; returns {status, passed, cost_value, metrics}."""
    cmd = [C.PYTHON, "-m", "rtlscout.run_eval", str(design), "--benchmark", str(bench_dir),
           "--cost-metric", metric, "--target-delay", str(target_delay or profile["target_delay"]),
           "--technology", profile["technology"], "--json"]
    if not netlist_sim:
        cmd.append("--skip-netlist-sim")
    if energy_exp is not None:
        cmd += ["--energy-exp", str(energy_exp)]
    U.sh(cmd, log, check=False)
    j = _parse_eval_json(log.read_text())
    return dict(status="ok" if j.get("cost_value") is not None else "failed", passed=j.get("passed"),
                cost_value=j.get("cost_value"), metrics=j.get("metrics") or {}, log=str(log))


def default_design_file(hdl: str) -> str:
    return "design.py" if hdl == "spire" else "design.sv"


def collect_run(run_dir: Path | None) -> dict:
    out: dict = dict(run_dir=str(run_dir) if run_dir else None, best_cost=None, best_eval=None,
                     best_design_file=None, n_evals=0, completed=False)
    if not run_dir:
        return out
    out["completed"] = (run_dir / "summary.txt").exists()
    out["n_evals"] = len(glob.glob(str(run_dir / "eval_*")))
    try:
        meta = json.loads((run_dir / "best_design" / "_best_meta.json").read_text())
        out["best_cost"], out["best_eval"], out["best_design_file"] = meta.get("best_cost"), meta.get("eval_index"), meta.get("design_file")
    except Exception:
        pass
    return out


def collect_equiv_best(run_dir: Path, cycles0: float, design_file: str) -> dict:
    """Best PASSING eval whose cycle count equals the start's (the trace oracle already enforces
    the schedule in-run; this is the redundant safety net the rescore re-verifies)."""
    out = dict(best_cost=None, best_eval=None, best_design_path=None, n_frozen_evals=0, n_thawed_evals=0)
    for f in sorted(run_dir.glob("eval_*/result.json")):
        try:
            r = json.loads(f.read_text())
        except Exception:
            continue
        if not r.get("passed") or r.get("cost_value") is None:
            continue
        cyc = (r.get("metrics") or {}).get("cycles")
        if cyc is None or float(cyc) != float(cycles0):
            out["n_thawed_evals"] += 1
            continue
        out["n_frozen_evals"] += 1
        if out["best_cost"] is None or r["cost_value"] < out["best_cost"]:
            out.update(best_cost=r["cost_value"], best_eval=r.get("eval_index"),
                       best_design_path=str(f.parent / "workspace" / (r.get("design_file") or design_file)))
    return out


# ---- the study ---------------------------------------------------------------------------------
class Study:
    def __init__(self, profile: dict, rp: C.RunPaths, only: set | None = None, dry_run: bool = False):
        self.p, self.rp, self.only, self.dry = profile, rp.ensure(), only, dry_run
        self.lock = threading.Lock()
        self.jobs = select_jobs(profile["jobs"])
        if only:
            self.jobs = [j for j in self.jobs if j in only]
        if rp.study_json.exists():
            self.data = json.loads(rp.study_json.read_text())
        else:
            self.data = dict(study=dict(profile=profile["name"], model=profile.get("model"),
                                        max_steps=profile["max_steps"], started=datetime.now().isoformat()),
                             baselines={}, runs={}, rescores={})

    def _write(self):
        U.write_json_atomic(self.rp.study_json, self.data)

    # -- benchmark dirs per job
    @staticmethod
    def _bench_dir(spec: dict) -> Path:
        b = BY_KEY[spec["bench"]]
        return b.dir(spec["hdl"], equiv=(spec["arm"] == "equiv"))

    @staticmethod
    def _task_dir(spec: dict) -> Path:
        return BY_KEY[spec["bench"]].dir(spec["hdl"])

    # -- baselines: the starting design under the rescore metric
    def run_baselines(self):
        for job in self.jobs:
            spec = JOBS[job]
            key = f"{spec['bench']}_{spec['hdl']}"
            if self.data["baselines"].get(key, {}).get("status") == "ok":
                continue
            b = BY_KEY[spec["bench"]]
            tdir = self._task_dir(spec)
            sp = tdir / json.loads((tdir / "metadata.json").read_text())["starting_point"]
            metric = rescore_metric(b)
            U.log(f"[baseline] {key}: {sp.relative_to(C.REPO)} under {metric}")
            if self.dry:
                continue
            rec = eval_design(sp, tdir, metric, self.rp.logs / f"baseline_{key}.log", self.p,
                              energy_exp=1.0 if metric.startswith("access") else None)
            with self.lock:
                self.data["baselines"][key] = rec
                self._write()
            m = rec["metrics"]
            U.log(f"[baseline] {key}: {rec['status']} area={m.get('area')} delay={m.get('delay')} cycles={m.get('cycles')}")

    # -- agent runs
    def _job_cmd(self, job: str) -> list[str]:
        spec = JOBS[job]
        metric, exp, netlist = OBJECTIVES[spec["objective"]]
        cmd = [C.PYTHON, "-m", "rtlscout.run_benchmark",
               "--benchmark", self._bench_dir(spec).name,
               "--benchmarks-root", str(C.BENCHMARKS),
               "--language", LANGUAGE[spec["hdl"]],
               "--model", self.p["model"],
               "--cost-metric", metric,
               "--max-steps", str(self.p["max_steps"]),
               "--runs-dir", str(self.rp.runs / job),
               "--target-delay", str(self.p["target_delay"]),
               "--technology", self.p["technology"]]
        if not netlist:
            cmd.append("--skip-netlist-sim")
        if exp is not None:
            cmd += ["--energy-exp", str(exp)]
        if self.p.get("wall_clock_min"):
            cmd += ["--wall-clock-min", str(self.p["wall_clock_min"])]
        return cmd

    def _run_one(self, job: str):
        spec = JOBS[job]
        cmd = self._job_cmd(job)
        log = self.rp.logs / f"run_{job}.log"
        U.log(f"[start] {job}")
        t0 = time.time()
        U.sh(cmd, log, check=False, env={k: str(v) for k, v in (self.p.get("env") or {}).items()})
        tail = log.read_text()[-6000:]
        m = list(_RUNDIR_RE.finditer(tail))
        run_dir = Path(m[-1].group(1)) if m else None
        if run_dir and not run_dir.is_absolute():
            run_dir = (C.REPO / run_dir).resolve()
        metric, exp, _ = OBJECTIVES[spec["objective"]]
        rec = dict(bench=spec["bench"], hdl=spec["hdl"], arm=spec["arm"], objective=spec["objective"],
                   cost_metric=metric, energy_exp=exp, model=self.p["model"],
                   duration_s=round(time.time() - t0, 1), log=str(log))
        rec.update(collect_run(run_dir))
        if spec["arm"] == "equiv" and run_dir:
            cycles0 = ((self.data["baselines"].get(f"{spec['bench']}_{spec['hdl']}") or {}).get("metrics") or {}).get("cycles")
            if cycles0 is not None:
                rec.update(collect_equiv_best(run_dir, cycles0, default_design_file(spec["hdl"])))
        rec["status"] = "completed" if rec["completed"] else ("launch-failed" if run_dir is None else "ended-early")
        with self.lock:
            self.data["runs"][job] = rec
            self._write()
        U.log(f"[done] {job}: {rec['status']} best={rec['best_cost']} ({rec['n_evals']} evals)")

    def run_agents(self):
        todo = [j for j in self.jobs if self.data["runs"].get(j, {}).get("status") != "completed"]
        for j in self.jobs:
            if j not in todo:
                U.log(f"[skip] {j}: already completed")
        if self.dry:
            for j in todo:
                print(" ".join(self._job_cmd(j)))
            return
        sem = threading.Semaphore(int(self.p["parallel"]))

        def worker(job):
            with sem:
                self._run_one(job)
        threads = []
        for j in todo:
            t = threading.Thread(target=worker, args=(j,), daemon=True)
            t.start()
            threads.append(t)
            time.sleep(2)           # distinct run-dir timestamps
        for t in threads:
            t.join()

    # -- rescores: every per-run best under the uniform rescore metric at its winning target delay
    def run_rescores(self):
        for job, rec in sorted(self.data["runs"].items()):
            if rec.get("status") != "completed" or not rec.get("run_dir"):
                continue
            if self.data["rescores"].get(job, {}).get("status") == "ok":
                continue
            spec = JOBS[job]
            b = BY_KEY[spec["bench"]]
            bench_dir = self._bench_dir(spec)
            best = Path(rec["best_design_path"]) if rec.get("best_design_path") else \
                Path(rec["run_dir"]) / "best_design" / (rec.get("best_design_file") or default_design_file(spec["hdl"]))
            if not best.exists():
                self.data["rescores"][job] = dict(status=f"missing-best({best})")
                self._write()
                continue
            target_delay = float(self.p["target_delay"])
            try:
                ev = json.loads((Path(rec["run_dir"]) / f"eval_{rec['best_eval']}" / "result.json").read_text())
                target_delay = float(ev.get("target_delay") or target_delay)
            except Exception:
                pass
            metric = rescore_metric(b)
            U.log(f"[rescore] {job}: {best.name} under {metric} @ target_delay={target_delay}")
            if self.dry:
                continue
            out = eval_design(best, bench_dir, metric, self.rp.logs / f"rescore_{job}.log", self.p,
                              target_delay=target_delay, energy_exp=1.0 if metric.startswith("access") else None)
            out["target_delay"] = target_delay
            out["best_design"] = str(best)
            if spec["arm"] == "equiv" and out["status"] == "ok":
                bm = (self.data["baselines"].get(f"{spec['bench']}_{spec['hdl']}") or {}).get("metrics") or {}
                out["counters_frozen"] = all(out["metrics"].get(k) == bm.get(k) for k in ("cycles", "reads", "writes"))
            with self.lock:
                self.data["rescores"][job] = out
                self._write()
            U.log(f"[rescore] {job}: {out['status']} cost={out['cost_value']}" +
                  (f" frozen={out.get('counters_frozen')}" if spec["arm"] == "equiv" else ""))

    def incomplete(self) -> list[str]:
        return [j for j in self.jobs if self.data["runs"].get(j, {}).get("status") != "completed"
                or self.data["rescores"].get(j, {}).get("status") != "ok"]

    def run(self):
        self.run_baselines()
        self.run_agents()
        if not self.dry:
            self.run_rescores()
            self.data["study"]["finished"] = datetime.now().isoformat()
            self._write()
            U.log(f"study data -> {self.rp.study_json}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--only", type=lambda s: set(s.split(",")), default=None, help="comma-separated job ids")
    ap.add_argument("--dry-run", action="store_true", help="print the agent commands, run nothing")
    ap.add_argument("--list-jobs", action="store_true")
    a = ap.parse_args()
    if a.list_jobs:
        for j, s in JOBS.items():
            print(f"{j:34s} {s}")
        return
    p = C.load_profile(a.profile)
    run = C.current_run(p, allow_missing=a.dry_run) or "dry-run"
    Study(p, C.RunPaths(run), only=a.only, dry_run=a.dry_run).run()


if __name__ == "__main__":
    main()
