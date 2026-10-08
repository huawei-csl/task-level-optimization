"""Profiles resolve to jobs; the launcher builds the engine commands it documents; nothing runs."""
from __future__ import annotations

import pytest

from tasklevel import config as C
from tasklevel.benches import BENCHES, BY_KEY, HDLS
from tasklevel.study import ARMS, JOBS, OBJECTIVES, Study, select_jobs

PROFILES = sorted(p.stem for p in C.PROFILES.glob("*.yaml"))


def test_job_matrix():
    assert len(JOBS) == len(BENCHES) * len(HDLS) * len(ARMS) + 5 * len(ARMS)
    for job, spec in JOBS.items():
        assert job == f"{spec['bench']}_{spec['hdl']}_{spec['arm']}_{spec['objective']}"
        assert spec["bench"] in BY_KEY and spec["objective"] in OBJECTIVES
        BY_KEY[spec["bench"]].dir(spec["hdl"], equiv=(spec["arm"] == "equiv")).is_dir()


@pytest.mark.parametrize("name", PROFILES)
def test_profile_loads_and_selects_jobs(name):
    p = C.load_profile(name)
    jobs = select_jobs(p["jobs"])
    assert jobs and p["model"] and p["max_steps"] > 0 and p["parallel"] >= 1
    assert set(p["stages"]) <= {"variants", "study", "power", "validate", "report"}
    assert "study" in p["stages"]


def test_select_jobs_order_and_unknown():
    assert select_jobs(["*_spire_*_arp"]) == [j for j in JOBS if j.endswith("_arp") and "_spire_" in j]
    assert len(select_jobs(["*_spire_*_arp"])) == 12 and len(select_jobs(["*_verilog_*_arp"])) == 12
    with pytest.raises(SystemExit):
        select_jobs(["nothing_like_this"])


def test_job_command_matches_documented_flags(data_root):
    p = C.load_profile("arp")
    p["env"] = {"RTLSCOUT_REASONING_EFFORT": "high"}
    st = Study(p, C.RunPaths("arp_test"), dry_run=True)
    cmd = " ".join(st._job_cmd("qr8_spire_equiv_arp"))
    assert "-m rtlscout.run_benchmark" in cmd
    assert "--benchmark qr8_1r1w_spire_equiv " in cmd + " "
    assert f"--benchmarks-root {C.BENCHMARKS}" in cmd
    assert "--language spirehdl" in cmd and "--cost-metric area_runtime_product" in cmd
    assert "--max-steps 40" in cmd and "--target-delay 500" in cmd and "--technology asap7" in cmd
    assert "--skip-netlist-sim" in cmd and "--energy-exp" not in cmd
    assert cmd.endswith("--skip-netlist-sim") or "--runs-dir" in cmd
    marp = " ".join(st._job_cmd("matmul_spire_task_marp2"))
    assert "--cost-metric access_area_runtime_product" in marp and "--energy-exp 2.0" in marp
    earp = " ".join(st._job_cmd("matmul_spire_task_earp1"))
    assert "--cost-metric energy_area_runtime_product" in earp and "--skip-netlist-sim" not in earp
    verilog = " ".join(st._job_cmd("jpeg_verilog_task_arp"))
    assert "--language verilog" in verilog and "--benchmark jpeg_idct_2d_1r1w_verilog " in verilog + " "


def test_study_dry_run_touches_nothing(data_root, capsys):
    p = C.load_profile("smoke")
    rp = C.RunPaths("smoke_dry")
    Study(p, rp, dry_run=True).run()
    assert not rp.study_json.exists()
    assert "rtlscout.run_benchmark" in capsys.readouterr().out


def test_run_pointer_and_manifest(data_root):
    p = C.load_profile("smoke")
    with pytest.raises(SystemExit):
        C.current_run(p)
    run = C.mint_run(p)
    assert C.current_run(p) == run and run.startswith("smoke_")
    C.register_run(p, run, status="complete")
    assert C.runs_for("smoke") == [run]
    C.register_run(p, run, status="incomplete")
    assert C.runs_for("smoke") == [] and C.runs_for("smoke", status=None) == [run]
