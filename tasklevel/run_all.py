"""Pipeline driver: one profile, one run directory, resumable stages.

    python -m tasklevel.run_all --profile smoke --new-run      # mint data/<tag>_<datetime>/ and run
    python -m tasklevel.run_all --profile arp                   # RESUME the profile's pinned run
    python -m tasklevel.run_all --profile arp --new-run --repeat 3   # three fresh repetitions, in sequence
    python -m tasklevel.run_all --profile arp --stages power,report  # subset (done stages are skipped)
    python -m tasklevel.run_all --profile arp --force --stages report

Stages (profile `stages:` picks the default list):
    variants   build the equivalence-track variants if missing (tasklevel.variants)
    study      baselines, agent runs, rescores          (tasklevel.study)
    power      measured circuit energy of the per-run bests (tasklevel.power; container only)
    validate   independent testbench sweep of the per-run bests (tasklevel.validate)
    report     REPORT.md + tables/figures for this run  (tasklevel.report)

Everything a run produces lives under data/<run>/ (study_results.json, power.json, runs/, logs/,
state/<stage>.done, REPORT.md, manifest.json). The profile yaml is snapshotted into the run dir on
the first invocation; the run is registered in data/manifest.json so the analysis modules find it.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime

from . import common as U
from . import config as C

ALL_STAGES = ["variants", "study", "power", "validate", "report"]


def snapshot_profile(p: dict, rp: C.RunPaths) -> None:
    snap = rp.root / f"profile_{p['name']}.yaml"
    cur = p["_file"].read_text()
    if not snap.exists():
        snap.write_text(cur)
    elif snap.read_text() != cur:
        (rp.root / f"profile_{p['name']}.{datetime.now():%Y%m%d_%H%M%S}.yaml").write_text(cur)
        U.log("profile changed since first snapshot -> timestamped copy kept")


def stage_variants(p, rp):
    from . import variants as V
    from .benches import BENCHES, HDLS
    missing = [(b, h) for b in BENCHES for h in HDLS if not b.dir(h, equiv=True).is_dir()]
    if not missing:
        U.log("variants: all present")
        return
    for b, h in missing:
        U.log(f"variants: building {b.name(h, equiv=True)}")
        V.make_variant(b, h)


def stage_study(p, rp, only=None):
    from .study import Study
    st = Study(p, rp, only=only)
    st.run()
    bad = st.incomplete()
    C.register_run(p, rp.name, status="complete" if not bad else "incomplete", incomplete_jobs=bad,
                   jobs=st.jobs, tools=U.tool_versions())
    if bad:
        U.log(f"study: {len(bad)} job(s) incomplete: {bad} -- re-run to resume")
        return False
    return True


def stage_power(p, rp):
    from .power import run_for
    run_for(rp, p)


def stage_validate(p, rp):
    from .validate import run_for
    run_for(rp, p)


def stage_report(p, rp):
    from .report import build
    build(rp, p)


STAGES = {"variants": stage_variants, "study": stage_study, "power": stage_power,
          "validate": stage_validate, "report": stage_report}


def run_pipeline(p: dict, rp: C.RunPaths, stages: list[str], force: bool, only=None) -> bool:
    rp.ensure()
    snapshot_profile(p, rp)
    C.register_run(p, rp.name)
    ok = True
    for name in stages:
        if U.stage_done(rp, name) and not force:
            U.log(f"stage {name}: already done, skipping")
            continue
        U.log(f"===== stage {name} =====")
        res = STAGES[name](p, rp) if name != "study" else STAGES[name](p, rp, only)
        if res is False:
            ok = False
            break                       # an incomplete study should not be reported as final
        U.mark_done(rp, name)
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True, help="profiles/<name>.yaml")
    ap.add_argument("--new-run", action="store_true", help="start a fresh data/<tag>_<datetime>/ and pin it")
    ap.add_argument("--repeat", type=int, default=1, help="with --new-run: this many fresh runs, one after the other")
    ap.add_argument("--stages", help="comma list (default: the profile's `stages`)")
    ap.add_argument("--only", type=lambda s: set(s.split(",")), help="study stage: only these job ids")
    ap.add_argument("--force", action="store_true", help="ignore .done markers")
    a = ap.parse_args()
    p = C.load_profile(a.profile)
    stages = a.stages.split(",") if a.stages else list(p["stages"])
    unknown = set(stages) - set(ALL_STAGES)
    if unknown:
        sys.exit(f"unknown stages {sorted(unknown)}; valid: {ALL_STAGES}")
    n = a.repeat if a.new_run else 1
    ok_all = True
    for i in range(n):
        run = C.mint_run(p) if a.new_run else C.current_run(p)
        rp = C.RunPaths(run)
        U.log(f"profile={p['name']} model={p.get('model')} run={run} -> {rp.root}")
        ok_all &= run_pipeline(p, rp, stages, a.force, a.only)
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
