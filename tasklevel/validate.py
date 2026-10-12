"""Post-hoc validation on independent testbenches (tb_v2): >1M stimuli per benchmark.

Each benchmark has a second testbench under validation/<bench>/ with directed corner cases plus
broad random stimuli; goldens come from the upstream RTL (qr8, ofdm), the bit-exact starting
design (jpeg, cv32) or an exact in-testbench model (matmul). The stimulus files are large and are
regenerated on demand by deterministic generators; validation/vector_hashes.json records the
SHA-256 of every file and regeneration must reproduce them byte for byte.

    python -m tasklevel.validate --regen            # (re)generate all stimulus files, check hashes
    python -m tasklevel.validate --profile arp      # validate every per-run best of the pinned run
    python -m tasklevel.validate --control          # positive control: start passes, a broken design fails

Results per run: data/<run>/validation.json, keyed by job: {status: pass|FAIL|..., total_checks, errors}.
A per-run best that fails is not eligible as a cell's winner (tasklevel.analysis applies this).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import common as U
from . import config as C
from .benches import BY_KEY
from .study import JOBS

V = C.VALIDATION
# bench key -> (tb_v2 for that benchmark, stimulus/vector files the testbench reads, generator, capture)
CFG = {
    "matmul":  dict(tb=V / "matmul/tb_v2.sv", files=[], gen=None, capture=None),
    "jpeg":    dict(tb=V / "jpeg/tb_v2.sv", files=[V / "jpeg/stim_v2.dat", V / "jpeg/vectors_v2.dat"],
                    gen=V / "jpeg/gen_stimuli_v2.py", capture="jpeg"),
    "cv32":    dict(tb=V / "cv32/tb_v2.sv", files=[V / "cv32/vectors_v2_stim.dat", V / "cv32/vectors_v2.dat"],
                    gen=V / "cv32/gen_stimuli_v2.py", capture="cv32"),
    "qr8":     dict(tb=V / "qr8/tb_v2.sv", files=[V / "qr8/vectors_v2.dat"], gen=V / "qr8/gen_v2.py", capture=None),
    "ofdm_tx": dict(tb=V / "ofdm/tb_v2_mod.sv", files=[V / "ofdm/vectors_v2_mod.dat"], gen=V / "ofdm/gen_v2.py", capture=None),
    "ofdm_rx": dict(tb=V / "ofdm/tb_v2_demod.sv", files=[V / "ofdm/vectors_v2_demod.dat"], gen=V / "ofdm/gen_v2.py", capture=None),
}
HASHES = V / "vector_hashes.json"
VERILATOR = ["verilator", "--binary", "--sv", "--top-module", "tb", "-o", "simv", "-Wno-DECLFILENAME",
             "-Wno-WIDTHEXPAND", "-Wno-UNUSEDSIGNAL", "-Wno-EOFNEWLINE", "-Wno-BLKSEQ", "-Wno-fatal",
             "--timescale", "1ns/1ps"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def design_rtl(design: Path, ws: Path, language: str) -> Path:
    """Place a design's RTL in ws (compile Spire, copy Verilog)."""
    if language == "spirehdl":
        from rtlscout.evaluation import _compile_spirehdl, SPIREHDL_VERILOG_OUTPUT
        for py in design.parent.glob("*.py"):
            shutil.copy2(py, ws / py.name)
        err, _ = _compile_spirehdl(ws, design.name)
        if err:
            raise RuntimeError(f"spire compile failed: {err[:300]}")
        return ws / SPIREHDL_VERILOG_OUTPUT
    dst = ws / design.name
    shutil.copy2(design, dst)
    return dst


def simulate_v2(bench: str, rtl: Path, ws: Path, plusargs: list[str] = ()) -> dict:
    cfg = CFG[bench]
    for f in cfg["files"]:
        if f.exists() and not (ws / f.name).exists():
            shutil.copy2(f, ws / f.name)
    r = subprocess.run(VERILATOR + [str(rtl), str(cfg["tb"])], cwd=ws, capture_output=True, text=True, timeout=900)
    if not (ws / "obj_dir/simv").exists():
        return dict(status="build_failed", error=r.stderr[-400:])
    r = subprocess.run(["./obj_dir/simv", *plusargs], cwd=ws, capture_output=True, text=True, timeout=7200)
    m = re.search(r"TB_SUMMARY total=(\d+) errors=(\d+)", r.stdout)
    if not m:
        return dict(status="sim_failed", error=(r.stdout + r.stderr)[-400:])
    total, errors = int(m.group(1)), int(m.group(2))
    fm = re.search(r"TB_(ERROR|WATCHDOG)[^\n]*", r.stdout)
    return dict(status="pass" if errors == 0 and "PASS" in r.stdout else "FAIL", total_checks=total, errors=errors,
                first_error=fm.group(0) if (errors and fm) else "")


# ---- stimulus regeneration -------------------------------------------------------------------
def regenerate(bench: str) -> None:
    cfg = CFG[bench]
    if cfg["gen"] is None:
        return
    U.log(f"validate: regenerating stimuli for {bench} ({cfg['gen'].name})")
    U.sh([C.PYTHON, str(cfg["gen"])], C.DATA / "logs" / f"validate_regen_{bench}.log", cwd=cfg["gen"].parent)
    if cfg["capture"]:
        # goldens from the bit-exact Spire starting design: run tb_v2 in +capture mode in the
        # validation folder itself (it reads the stimulus file and writes the vector file there)
        b = BY_KEY[bench]
        tdir = b.dir("spire")
        meta = json.loads((tdir / "metadata.json").read_text())
        with tempfile.TemporaryDirectory(prefix="tl_capture_") as td:
            ws = Path(td)
            rtl = design_rtl(tdir / meta["starting_point"], ws, "spirehdl")
            for f in cfg["files"]:
                if f.exists():
                    shutil.copy2(f, ws / f.name)
            res = simulate_v2(bench, rtl, ws, ["+capture"])
            if res["status"] not in ("pass",):
                raise RuntimeError(f"capture failed for {bench}: {res}")
            out = [f for f in cfg["files"] if f.name == "vectors_v2.dat"][0]
            if not (ws / out.name).exists():
                raise RuntimeError(f"capture for {bench} wrote no {out.name}")
            shutil.copy2(ws / out.name, out)


def check_hashes(update: bool = False) -> list[str]:
    """Compare every stimulus file against validation/vector_hashes.json; return mismatches."""
    rec = json.loads(HASHES.read_text()) if HASHES.exists() else {}
    bad = []
    for bench, cfg in CFG.items():
        for f in cfg["files"]:
            key = str(f.relative_to(V))
            if not f.exists():
                bad.append(f"{key}: missing")
                continue
            h = sha256(f)
            if update:
                rec[key] = h
            elif rec.get(key) != h:
                bad.append(f"{key}: {h[:16]} != recorded {str(rec.get(key))[:16]}")
    if update:
        HASHES.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    return bad


def ensure_vectors(bench: str) -> None:
    cfg = CFG[bench]
    if all(f.exists() for f in cfg["files"]):
        return
    regenerate(bench)
    bad = [b for b in check_hashes() if b.startswith(f"{cfg['tb'].parent.name}/")]
    if bad:
        raise RuntimeError("regenerated stimuli differ from the recorded hashes:\n  " + "\n  ".join(bad))


# ---- validating designs -------------------------------------------------------------------------
def validate_design(bench: str, design: Path, language: str) -> dict:
    ensure_vectors(bench)
    with tempfile.TemporaryDirectory(prefix="tl_val_") as td:
        ws = Path(td)
        rtl = design_rtl(design, ws, language)
        res = simulate_v2(bench, rtl, ws)
        res["design"] = str(design)
        return res


def run_for(rp: C.RunPaths, p: dict) -> None:
    rp.ensure()
    sr = json.loads(rp.study_json.read_text())
    out_path = rp.root / "validation.json"
    results = json.loads(out_path.read_text()) if out_path.exists() else {}
    for job, r in sorted(sr["rescores"].items()):
        if r.get("status") != "ok" or results.get(job, {}).get("status") in ("pass", "FAIL"):
            continue
        spec = JOBS[job]
        lang = "spirehdl" if spec["hdl"] == "spire" else "verilog"
        U.log(f"validate: {job}")
        try:
            results[job] = validate_design(spec["bench"], Path(r["best_design"]), lang)
        except Exception as e:
            results[job] = dict(status=f"crashed: {e}", design=r.get("best_design"))
        U.write_json_atomic(out_path, results)
        U.log(f"validate: {job}: {results[job]['status']} checks={results[job].get('total_checks')} errors={results[job].get('errors')}")
    U.record(rp, "validate", out_path, "tb_v2 result per per-run best")


def control(bench: str = "matmul") -> dict:
    """Positive control of the oracle: the starting design passes; a one-bit corruption fails."""
    b = BY_KEY[bench]
    tdir = b.dir("verilog")
    meta = json.loads((tdir / "metadata.json").read_text())
    start = tdir / meta["starting_point"]
    good = validate_design(bench, start, "verilog")
    src = start.read_text()
    # flip bit 0 of the result bus at its (continuous or non-blocking) assignment
    bad_src = re.sub(r"\bwr_data\s*(<=|=(?!=))\s*", r"wr_data \1 1 ^ ", src, count=1)
    if bad_src == src:
        raise RuntimeError("could not construct the corrupted control design")
    with tempfile.TemporaryDirectory(prefix="tl_ctrl_") as td:
        badf = Path(td) / start.name
        badf.write_text(bad_src)
        bad = validate_design(bench, badf, "verilog")
    return dict(start=good, corrupted=bad, ok=(good["status"] == "pass" and bad["status"] == "FAIL"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile")
    ap.add_argument("--run")
    ap.add_argument("--regen", action="store_true", help="regenerate all stimulus files and check the hashes")
    ap.add_argument("--record-hashes", action="store_true", help="(maintainers) write the current hashes")
    ap.add_argument("--control", action="store_true")
    a = ap.parse_args()
    if a.regen:
        done = set()
        for bench, cfg in CFG.items():
            if cfg["gen"] in done:          # the ofdm generator writes both halves at once
                continue
            done.add(cfg["gen"])
            regenerate(bench)
        bad = check_hashes(update=a.record_hashes)
        missing = [b for b in bad if b.endswith(": missing")]
        if a.record_hashes:
            print("hashes: recorded" if not missing else "MISSING after regeneration:\n  " + "\n  ".join(missing))
            raise SystemExit(1 if missing else 0)
        print("hashes:", "all match" if not bad else "MISMATCH:\n  " + "\n  ".join(bad))
        raise SystemExit(1 if bad else 0)
    if a.record_hashes:
        check_hashes(update=True)
        print("hashes recorded ->", HASHES)
        return
    if a.control:
        r = control()
        print(json.dumps(r, indent=1))
        raise SystemExit(0 if r["ok"] else 1)
    run = a.run or C.current_run(C.load_profile(a.profile))
    prof = C.load_manifest()["runs"].get(run, {}).get("profile") or a.profile
    run_for(C.RunPaths(run), C.load_profile(prof))


if __name__ == "__main__":
    main()
