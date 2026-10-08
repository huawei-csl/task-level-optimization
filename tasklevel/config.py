"""Paths, profiles and the run manifest.

Layout (all relative to the repository root, which is the parent of this package):

    benchmarks/<name>/            the released benchmarks (folder == metadata name)
    profiles/<profile>.yaml       one experiment each (model, jobs, steps, parallelism)
    data/<tag>_<datetime>/        one run: study_results.json, power.json, state/, logs/, runs/<job>/...
    data/manifest.json            which runs exist, under which profile group (written by run_all)

The data root can be moved with TASKLEVEL_DATA=/some/dir (default: <repo>/data).
A run is pinned per profile in data/.latest_<profile>; `run_all --new-run` mints a new one.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
BENCHMARKS = REPO / "benchmarks"
UPSTREAM_BENCHMARKS = BENCHMARKS / "upstream"
PROFILES = REPO / "profiles"
VALIDATION = REPO / "validation"
DATA = Path(os.environ.get("TASKLEVEL_DATA", REPO / "data")).resolve()
MANIFEST = DATA / "manifest.json"
PYTHON = sys.executable


def load_profile(name: str) -> dict:
    f = PROFILES / f"{name}.yaml"
    if not f.exists():
        raise SystemExit(f"unknown profile '{name}' ({f} missing); available: "
                         + ", ".join(sorted(p.stem for p in PROFILES.glob("*.yaml"))))
    p = yaml.safe_load(f.read_text())
    p.setdefault("name", name)
    p.setdefault("tag", name)
    p.setdefault("max_steps", 40)
    p.setdefault("parallel", 6)
    p.setdefault("target_delay", 500)
    p.setdefault("technology", "asap7")
    p.setdefault("stages", ["variants", "study", "report"])
    p["_file"] = f
    return p


def pointer(profile: str) -> Path:
    return DATA / f".latest_{profile}"


def mint_run(profile: dict) -> str:
    """Create and pin a new run id <tag>_<YYYYmmdd_HHMMSS>."""
    run = f"{profile['tag']}_{datetime.now():%Y%m%d_%H%M%S}"
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / run).mkdir()
    pointer(profile["name"]).write_text(run + "\n")
    return run


def current_run(profile: dict, allow_missing: bool = False) -> str | None:
    env = os.environ.get("TASKLEVEL_RUN")
    if env:
        return env
    ptr = pointer(profile["name"])
    if ptr.exists():
        return ptr.read_text().strip()
    if allow_missing:
        return None
    raise SystemExit(f"no run pinned for profile '{profile['name']}'; start one with --new-run")


class RunPaths:
    """Everything a run owns lives under data/<run>/."""

    def __init__(self, run: str):
        self.name = run
        self.root = DATA / run
        self.runs = self.root / "runs"          # per-job agent run dirs + launcher logs
        self.state = self.root / "state"        # .done markers
        self.logs = self.root / "logs"
        self.study_json = self.root / "study_results.json"
        self.power_json = self.root / "power.json"
        self.manifest = self.root / "manifest.json"   # files this run produced (common.record)
        self.report_md = self.root / "REPORT.md"
        self.figures = self.root / "figures"
        self.tables = self.root / "tables"

    def ensure(self):
        for d in (self.root, self.runs, self.state, self.logs, self.figures, self.tables):
            d.mkdir(parents=True, exist_ok=True)
        return self


# ---- the global manifest: which runs exist, grouped by profile -------------------------------
def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {"runs": {}}


def save_manifest(m: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
    tmp.replace(MANIFEST)


def register_run(profile: dict, run: str, **extra) -> None:
    """run_all --new-run calls this; analysis modules read the manifest and nothing else."""
    m = load_manifest()
    rec = m["runs"].get(run, {})
    rec.update({"profile": profile["name"], "tag": profile["tag"], "model": profile.get("model"),
                "max_steps": profile.get("max_steps"), "created": rec.get("created") or datetime.now().isoformat()})
    rec.update(extra)
    m["runs"][run] = rec
    save_manifest(m)


def runs_for(profile_names, status: str | None = "complete") -> list[str]:
    """Run ids registered under any of the given profiles (newest last), optionally only complete ones."""
    if isinstance(profile_names, str):
        profile_names = [profile_names]
    m = load_manifest()
    out = [r for r, rec in sorted(m["runs"].items()) if rec.get("profile") in profile_names
           and (status is None or rec.get("status") == status) and not rec.get("excluded")]
    return out
