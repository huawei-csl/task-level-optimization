"""Shared helpers: logging, subprocesses with per-call log files, stage markers, run manifest."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import config as C


def log(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def sh(cmd, log_file: Path, cwd: Path = C.REPO, check: bool = True, env: dict | None = None) -> int:
    """Run a command, teeing stdout+stderr into log_file. Returns the exit code."""
    cmd = [str(c) for c in cmd]
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "w") as lf:
        lf.write("$ " + " ".join(cmd) + "\n\n")
        lf.flush()
        proc = subprocess.run(cmd, cwd=cwd, stdout=lf, stderr=subprocess.STDOUT, text=True,
                              env={**os.environ, **(env or {})})
    if check and proc.returncode != 0:
        tail = log_file.read_text()[-2000:]
        raise RuntimeError(f"command failed (rc={proc.returncode}): {' '.join(cmd[:4])} ...\n--- {log_file} (tail)\n{tail}")
    return proc.returncode


def stage_done(rp: C.RunPaths, name: str) -> bool:
    return (rp.state / f"{name}.done").exists()


def mark_done(rp: C.RunPaths, name: str) -> None:
    rp.state.mkdir(parents=True, exist_ok=True)
    (rp.state / f"{name}.done").write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")


def clear_done(rp: C.RunPaths, name: str) -> None:
    (rp.state / f"{name}.done").unlink(missing_ok=True)


def record(rp: C.RunPaths, stage: str, path: Path, note: str = "") -> None:
    """Append a produced file to the run's manifest (what the report lists)."""
    m = json.loads(rp.manifest.read_text()) if rp.manifest.exists() else {"run": rp.name, "files": []}
    rel = str(Path(path).resolve().relative_to(rp.root.resolve())) if Path(path).resolve().is_relative_to(rp.root.resolve()) else str(path)
    m["files"] = [f for f in m["files"] if f["path"] != rel]
    m["files"].append({"stage": stage, "path": rel, "note": note, "size": Path(path).stat().st_size if Path(path).exists() else None,
                       "time": time.strftime("%Y-%m-%d %H:%M:%S")})
    rp.manifest.write_text(json.dumps(m, indent=1) + "\n")


def write_json_atomic(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1) + "\n")
    tmp.replace(path)


def tool_versions() -> dict:
    """Versions of the EDA tools and the engine, for the run manifest."""
    out = {}
    for tool, args in (("yosys", ["-V"]), ("verilator", ["--version"]), ("openroad", ["-version"]), ("sta", ["-version"])):
        try:
            r = subprocess.run([tool] + args, capture_output=True, text=True, timeout=30)
            out[tool] = (r.stdout or r.stderr).strip().splitlines()[0][:120] if (r.stdout or r.stderr) else "?"
        except Exception as e:
            out[tool] = f"unavailable ({type(e).__name__})"
    try:
        import importlib.metadata as im
        out["rtlscout"] = im.version("rtlscout")
        out["spire-hdl"] = im.version("spire-hdl")
    except Exception:
        pass
    out["python"] = sys.version.split()[0]
    return out
