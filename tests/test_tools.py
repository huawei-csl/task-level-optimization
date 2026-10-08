"""Tests that need the EDA tools / the engine: variants reproduce byte for byte, the independent
testbench passes the start and catches a corruption. Skipped where Verilator is missing; the
long ones also need TASKLEVEL_SLOW=1."""
from __future__ import annotations

import os
import shutil

import pytest

from tasklevel import config as C
from tasklevel import validate as V

needs_tools = pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not installed")
slow = pytest.mark.skipif(os.environ.get("TASKLEVEL_SLOW") != "1", reason="set TASKLEVEL_SLOW=1")


def test_validation_config_points_at_released_files():
    for bench, cfg in V.CFG.items():
        assert cfg["tb"].exists(), cfg["tb"]
        if cfg["gen"]:
            assert cfg["gen"].exists(), cfg["gen"]
        for f in cfg["files"]:
            assert f.parent.is_relative_to(C.VALIDATION)


def test_hash_check_reports_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "HASHES", tmp_path / "vector_hashes.json")
    bad = V.check_hashes()
    files = [f for cfg in V.CFG.values() for f in cfg["files"]]
    assert len(bad) == len(files)          # none recorded -> every file is a mismatch or missing
    assert all(("missing" in b) or ("recorded None" in b) for b in bad)


@needs_tools
def test_matmul_equiv_variant_is_reproducible():
    from tasklevel.variants import check_all
    assert check_all(only="matmul") == []


@needs_tools
@slow
def test_tb_v2_positive_control():
    r = V.control("matmul")
    assert r["start"]["status"] == "pass" and r["start"]["total_checks"] > 1_000_000
    assert r["corrupted"]["status"] == "FAIL"


@needs_tools
def test_matmul_start_access_count_is_the_arithmetic_count():
    """The task testbench counts memory accesses only between reset release and done:
    the single-MAC start reads every A and B element once per product (2 * 16^3 per case)."""
    import re, shutil, tempfile
    from pathlib import Path
    from tasklevel.benches import BY_KEY
    from tasklevel import variants
    from rtlscout.correctness import simulate
    b = BY_KEY["matmul"]
    bench = b.dir("verilog")
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        for f in list(bench.glob("*.dat")) + [bench / "tb.sv"]:
            shutil.copy2(f, ws / f.name)
        rtl = variants.starting_point_rtl(bench, ws)
        r = simulate([ws / "tb.sv", rtl], "tb", ws)
        assert r.ok and "PASS" in r.stdout, r.stdout[-400:]
        cases = int(re.search(r"localparam int NCASES = (\d+);", (bench / "tb.sv").read_text()).group(1))
        reads = int(re.search(r"TB_READS total=(\d+)", r.stdout).group(1))
        writes = int(re.search(r"TB_WRITES total=(\d+)", r.stdout).group(1))
        assert writes == cases * 16 * 16 and reads == cases * 2 * 16 ** 3
