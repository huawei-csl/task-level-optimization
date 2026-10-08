"""Winner selection, gates and the generated tables/figures, on synthetic runs (see conftest)."""
from __future__ import annotations

import json

import pytest

from tasklevel import config as C
from tasklevel.analysis import _common as A
from tasklevel.analysis import alpha_dial, arp, energy, verilog_table
from tasklevel.report import build as build_report
from tasklevel import upstream

from conftest import START, scaled, write_run


def test_cells_winner_gates(two_runs):
    cells = A.cells_for(["arp"], hdl="spire")
    assert set(cells) == {("matmul", "equiv"), ("matmul", "task"), ("jpeg", "equiv"), ("jpeg", "task")}
    mt = cells[("matmul", "task")]
    assert len(mt["reps"]) == 2, "the incomplete third run must not contribute"
    assert mt["winner"]["run"] == "arp_1" and abs(mt["winner"]["arp_rel"] - 0.5) < 1e-9, "tb_v2 failure excludes the 0.3 design"
    me = cells[("matmul", "equiv")]
    assert me["winner"]["run"] == "arp_1" and abs(me["winner"]["arp_rel"] - 0.8) < 1e-9, "thawed counters exclude the 0.7 design"
    jt = cells[("jpeg", "task")]
    assert jt["winner"]["run"] == "arp_2" and abs(jt["winner"]["arp_rel"] - 0.4) < 1e-9
    assert not any(c["unvalidated"] for c in cells.values())


def test_unvalidated_counts_as_eligible_but_is_flagged(data_root, capsys):
    s = START["matmul_spire"]
    write_run("arp_x", "arp", {"matmul_spire_task_arp": dict(metrics=scaled(s, 0.2))})     # no validation.json
    cells = A.cells_for(["arp"], hdl="spire")
    c = cells[("matmul", "task")]
    assert c["winner"] is not None and c["unvalidated"] == ["arp_x"]
    A.warn_unvalidated(cells)
    assert "not yet validated" in capsys.readouterr().out


def test_arp_tables_and_figure(two_runs):
    arp.build(["arp"], "spire")
    out = A.OUT
    md = (out / "table_arp.md").read_text()
    assert "| matmul 16³ |" in md and "| 0.80 | 0.50 | 0.62 |" in md        # equiv, task, task/equiv
    assert "| jpeg idct-2d |" in md and "| 0.85 | 0.40 |" in md
    tex = (out / "table_arp.tex").read_text()
    assert tex.startswith("% generated") and "\\begin{tabular}" in tex and "MatMul $16^3$" in tex
    stats = (out / "table_arp_stats.md").read_text()
    assert "| 0.75±0.05 | 2 |" in stats                                      # equiv per-run bests 0.8, 0.7
    assert (out / "fig_arp.png").exists() and (out / "fig_arp.pdf").exists()


def test_arp_decomposition_uses_winner_metrics(two_runs):
    arp.build(["arp"], "spire")
    row = [l for l in (A.OUT / "table_arp.md").read_text().splitlines() if l.startswith("| matmul")][0]
    # task winner: arp 0.5 via cycles 0.5 -> area 1.00, period 1.00, cycles 0.50
    assert row.endswith("| 1.00 | 1.00 | 0.50 |")


def test_verilog_table_with_one_hdl_missing(two_runs):
    verilog_table.build(["arp"], ["verilog_twins"])
    md = (A.OUT / "table_verilog.md").read_text()
    assert "| Spire |" in md and "| Verilog |" not in md
    tex = (A.OUT / "table_verilog.tex").read_text()
    assert "tabular*" in tex and "0.80 {\\scriptsize $0.75\\,\\pm\\,0.05$}" in tex


def test_verilog_table_both_hdls(two_runs):
    v = START["matmul_verilog"]
    write_run("vt_1", "verilog_twins", {"matmul_verilog_equiv_arp": dict(metrics=scaled(v, 0.9), frozen=True),
                                        "matmul_verilog_task_arp": dict(metrics=scaled(v, 0.45))},
              validation={"matmul_verilog_equiv_arp": dict(status="pass"), "matmul_verilog_task_arp": dict(status="pass")})
    verilog_table.build(["arp"], ["verilog_twins"])
    md = (A.OUT / "table_verilog.md").read_text()
    assert "| Verilog |" in md and "| 0.90 (0.90) | 0.45 (0.45) |" in md
    assert "start ratio V/S | 1.05 |" in md


def test_energy_table_and_population_figure(two_runs):
    s = START["matmul_spire"]
    jobs = {"matmul_spire_task_marp1": dict(metrics=scaled(s, 0.7, movements_rel=0.5)),
            "matmul_spire_equiv_marp1": dict(metrics=scaled(s, 0.9, movements_rel=1.0), frozen=True)}
    base_adp = s["area"] * s["cycles"] * s["delay"]
    power = {"baseline:matmul_spire": dict(status="ok", e_circuit_fj=100.0, adp=base_adp),
             "winner:matmul_spire_task_marp1": dict(status="ok", e_circuit_fj=60.0, adp=0.7 * base_adp),
             "eval:matmul_spire_task_marp1:eval_1": dict(status="ok", e_circuit_fj=90.0, adp=0.95 * base_adp)}
    write_run("en_1", "energy_objectives", jobs, validation={j: dict(status="pass") for j in jobs}, power=power)
    energy.build(["energy_objectives", "arp"])
    md = (A.OUT / "table_energy.md").read_text()
    assert "| arp | task | 0.50 |" in md                                   # from the arp runs
    row = [l for l in md.splitlines() if l.startswith("| marp α=1 | task")][0]
    # movements: reads 0.5x of 4096 + writes 256 -> (2048+256)/(4352) = 0.53 ; energy 0.6 ; edap 0.6*0.7
    assert row == "| marp α=1 | task | 0.70 | 0.53 | 0.60 | 0.42 |"
    assert (A.OUT / "fig_energy.png").exists()


def test_alpha_dial_pool_and_regimes(two_runs):
    s = START["matmul_spire"]
    stdout = "TB_CYCLES total=10000\nTB_READS total={r}\nTB_WRITES total={w}\nTB_SUMMARY total=1 errors=0\nPASS\n"
    evals = [(True, dict(area=500.0, delay=500.0, cycles=10000.0), stdout.format(r=4096, w=256)),   # arp 0.5, mv 1.0
             (True, dict(area=800.0, delay=500.0, cycles=10000.0), stdout.format(r=1024, w=256)),   # arp 0.8, mv 0.29
             (False, dict(area=1.0, delay=1.0, cycles=1.0), stdout.format(r=1, w=1)),               # failed: ignored
             (True, dict(area=500.0, delay=500.0, cycles=10000.0), stdout.format(r=4096, w=256))]   # duplicate: ignored
    write_run("ad_1", "alpha_dial", {"matmul_spire_task_arp": dict(metrics=scaled(s, 0.5), evals=evals)},
              validation={"matmul_spire_task_arp": dict(status="pass")})
    start, pts = alpha_dial.pool(["alpha_dial"])
    assert len(pts) == 2
    regimes = alpha_dial.sweep(start, pts, [round(i * 0.01, 2) for i in range(301)])
    assert len(regimes) == 2 and regimes[0]["point"]["area"] == 500.0 and regimes[1]["point"]["area"] == 800.0
    # crossover: 0.5 * 1^a = 0.8 * 0.294^a  ->  a = ln(0.625)/ln(0.294) ~ 0.38
    assert abs(regimes[1]["a_lo"] - 0.39) < 0.02
    alpha_dial.build(["alpha_dial"])
    assert (A.OUT / "table_alpha_dial.md").exists() and (A.OUT / "fig_alpha_dial.png").exists()
    assert json.loads((A.OUT / "alpha_dial_pool.json").read_text())["points"][0]["reads"] == 4096


def test_report_for_one_run(two_runs):
    rp = C.RunPaths("arp_1")
    build_report(rp, C.load_profile("arp"))
    assert rp.report_md.exists() and (rp.figures / "rel_cost.png").exists()
    derived = json.loads((rp.root / "derived.json").read_text())
    assert abs(derived["matmul_spire"]["task_arp"]["arp_rel"] - 0.5) < 1e-9
    assert derived["matmul_spire"]["start"]["arp_rel"] == 1.0
    assert "| matmul 16³ | spire | task_arp |" in (rp.tables / "tables.md").read_text()


def test_upstream_table_from_sweep_and_arp_runs(two_runs):
    base = START["jpeg_spire"]
    adp = base["area"] * base["cycles"] * base["delay"]
    sweep = {"jpeg_idct_2d_upstream": {"base": dict(target=500, area=1.0, delay=1.0, cycles=1.0, adp=2 * adp),
                                       "lo": dict(target=400, area=1.0, delay=1.0, cycles=1.0, adp=0.8 * adp)}}
    upstream.table(sweep, A.OUT)
    md = (A.OUT / "table_upstream.md").read_text()
    row = [l for l in md.splitlines() if l.startswith("| jpeg")][0]
    assert row.endswith("| 0.50 |"), row                 # task winner 0.4 x start / (0.8 x start) = 0.5
    assert "| matmul 16³ | --- |" in md                  # no upstream design for MatMul
    assert "jpeg idct-2d & $" in (A.OUT / "table_upstream.tex").read_text()
