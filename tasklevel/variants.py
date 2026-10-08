"""Equivalence-track variants: the trace-oracle testbench and the recorded reference trace.

For every released starting design `<bench>_<hdl>/` this builds `<bench>_<hdl>_equiv/`:
the same description plus the equivalence note, the same data files, and a testbench that
replays the stimulus and compares the design's I/O behavior cycle by cycle against a trace
recorded from the starting design (memory-port benchmarks: {done, wr_data, wr_addr, wr_en,
rd_addr, rd_en} per cycle, bus fields masked by the reference's enables; the ALU: per-op
handshake latency). Any schedule deviation fails IN-RUN as TB_EQUIV_ERROR. The trace is
generated here by simulating the starting design under a dumper testbench, so the variant is
reproducible from the start alone.

    python -m tasklevel.variants            # (re)build all 12 variants
    python -m tasklevel.variants --only qr8 # one benchmark (both HDLs)
    python -m tasklevel.variants --check    # rebuild into a scratch dir and diff against the tree

Idempotent; regeneration is byte-identical (a test asserts it).
"""
from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import tempfile
from pathlib import Path

from . import config as C
from .benches import BENCHES, HDLS, LANGUAGE, Bench

_MEM_ORACLE = ("on every operating cycle the tuple {done, wr_data, wr_addr, wr_en, "
               "rd_addr, rd_en} must match the reference trace (`trace.dat`; bus "
               "fields are only compared on cycles where the reference asserts the "
               "corresponding enable -- idle-bus values are don't-care).")
_LAT_ORACLE = ("every operation's handshake latency (cycles until `ready_o`) must "
               "equal the reference implementation's latency for that vector "
               "(`lat.dat`), in addition to the normal result checks.")
_COUNTERS = {"mem": "/ TB_READS / TB_WRITES", "lat": "/ TB_DIV_CYCLES"}

# --------------------------------------------------------------------------
# Memory-port trace specs (bit layout of the per-cycle trace word per benchmark).
# --------------------------------------------------------------------------
MEM_TRACE = {
    "matmul": dict(
        width=40, hexw=10, tmax=131072,
        cat="{done, wr_data, wr_addr, wr_en, rd_addr, rd_en}",
        mism=("(eq_g[39] !== eq_e[39]) || (eq_g[10] !== eq_e[10]) || (eq_g[0] !== eq_e[0])\n"
              "              || (eq_e[10] && (eq_g[38:19] !== eq_e[38:19] || eq_g[18:11] !== eq_e[18:11]))\n"
              "              || (eq_e[0] && (eq_g[9:1] !== eq_e[9:1]))")),
    "qr8": dict(
        width=31, hexw=8, tmax=65536,
        cat="{done, wr_data, wr_addr, wr_en, rd_addr, rd_en}",
        mism=("(eq_g[30] !== eq_e[30]) || (eq_g[7] !== eq_e[7]) || (eq_g[0] !== eq_e[0])\n"
              "              || (eq_e[7] && (eq_g[29:14] !== eq_e[29:14] || eq_g[13:8] !== eq_e[13:8]))\n"
              "              || (eq_e[0] && (eq_g[6:1] !== eq_e[6:1]))")),
    # ofdm32_mod: wr_addr is 7 bits -> {done[29], wr_data[28:13], wr_addr[12:6],
    # wr_en[5], rd_addr[4:1], rd_en[0]}
    "ofdm_tx": dict(
        width=30, hexw=8, tmax=8192,
        cat="{done, wr_data, wr_addr, wr_en, rd_addr, rd_en}",
        mism=("(eq_g[29] !== eq_e[29]) || (eq_g[5] !== eq_e[5]) || (eq_g[0] !== eq_e[0])\n"
              "              || (eq_e[5] && (eq_g[28:13] !== eq_e[28:13] || eq_g[12:6] !== eq_e[12:6]))\n"
              "              || (eq_e[0] && (eq_g[4:1] !== eq_e[4:1]))")),
    # ofdm32_demod: rd_addr is 7 bits -> {done[29], wr_data[28:13], wr_addr[12:9],
    # wr_en[8], rd_addr[7:1], rd_en[0]}
    "ofdm_rx": dict(
        width=30, hexw=8, tmax=8192,
        cat="{done, wr_data, wr_addr, wr_en, rd_addr, rd_en}",
        mism=("(eq_g[29] !== eq_e[29]) || (eq_g[8] !== eq_e[8]) || (eq_g[0] !== eq_e[0])\n"
              "              || (eq_e[8] && (eq_g[28:13] !== eq_e[28:13] || eq_g[12:9] !== eq_e[12:9]))\n"
              "              || (eq_e[0] && (eq_g[7:1] !== eq_e[7:1]))")),
    "jpeg": dict(
        width=47, hexw=12, tmax=98304,
        cat="{done, wr_data, wr_addr, wr_en, rd_addr, rd_en}",
        mism=("(eq_g[46] !== eq_e[46]) || (eq_g[7] !== eq_e[7]) || (eq_g[0] !== eq_e[0])\n"
              "              || (eq_e[7] && (eq_g[45:14] !== eq_e[45:14] || eq_g[13:8] !== eq_e[13:8]))\n"
              "              || (eq_e[0] && (eq_g[6:1] !== eq_e[6:1]))")),
}


# Shared anchors of the two memory-port harnesses (byte-identical loop shape).
_A_LOOP = "        @(posedge clk);\n        wd = wd + 1;\n      end"
_A_CYC = "      total_cycles = total_cycles + wd;"
_A_LOAD = '    $readmemh("vectors.dat", data_all);'
_A_FINAL = '    $display("TB_CYCLES total=%0d", total_cycles);'
_A_CASE = "      case_base = idx * CASE_STRIDE;"
_A_DECL = "  int c, j, wd;"


def _apply(tb: str, pairs) -> str:
    for old, new in pairs:
        assert tb.count(old) == 1, f"tb anchor not unique: {old[:60]!r}"
        tb = tb.replace(old, new)
    return tb


def patch_mem_tb_dump(tb: str, spec: dict) -> str:
    """Dumper: original TB + per-cycle trace write (run on the starting point)."""
    return _apply(tb, [
        (_A_DECL, _A_DECL + "\n  int tf, tlf;"),
        (_A_LOAD, _A_LOAD + '\n    tf  = $fopen("trace.dat", "w");\n'
                            '    tlf = $fopen("trace_len.dat", "w");'),
        (_A_LOOP, "        @(posedge clk);\n        wd = wd + 1;\n"
                  f"        $fwrite(tf, \"%0{spec['hexw']}x\\n\", {spec['cat']});\n"
                  "      end"),
        (_A_CYC, _A_CYC + '\n      $fwrite(tlf, "%x\\n", wd);'),
        (_A_FINAL, "    $fclose(tf);\n    $fclose(tlf);\n" + _A_FINAL),
    ])


def patch_mem_tb_check(tb: str, spec: dict) -> str:
    """Checker: original TB + cycle-by-cycle comparison against trace.dat."""
    w = spec["width"]
    return _apply(tb, [
        (_A_DECL, _A_DECL + f"""
  // ---- I/O-trace equivalence oracle (see description.txt) ----
  logic [{w-1}:0] exp_trace [0:{spec['tmax']-1}];
  logic [31:0] exp_len [0:NCASES-1];
  logic [{w-1}:0] eq_e, eq_g;
  int tidx, case_exp_end, eq_shown;"""),
        (_A_LOAD, _A_LOAD + '\n    $readmemh("trace.dat", exp_trace);\n'
                            '    $readmemh("trace_len.dat", exp_len);\n'
                            "    tidx = 0;"),
        (_A_CASE, _A_CASE + "\n      case_exp_end = tidx + int'(exp_len[idx]);\n"
                            "      eq_shown = 0;"),
        (_A_LOOP, f"""        @(posedge clk);
        wd = wd + 1;
        if (tidx < case_exp_end) begin
          eq_e = exp_trace[tidx];
          eq_g = {spec['cat']};
          total_checks++;
          if ({spec['mism']}) begin
            total_errors++;
            if (eq_shown < 5) begin
              $display("TB_EQUIV_ERROR case=%0d cyc=%0d expected=%0{spec['hexw']}x got=%0{spec['hexw']}x (I/O trace differs from reference schedule)",
                       idx, wd - 1, eq_e, eq_g);
              eq_shown++;
            end
          end
          tidx++;
        end
      end"""),
        (_A_CYC, _A_CYC + """
      total_checks++;
      if (tidx != case_exp_end) begin
        total_errors++;
        $display("TB_EQUIV_ERROR case=%0d done after %0d cycles; reference schedule has %0d", idx, wd, int'(exp_len[idx]));
        tidx = case_exp_end;
      end"""),
    ])


def patch_cv32_tb_dump(tb: str) -> str:
    """cv32 dumper: counter-patched TB + per-op latency write."""
    return _apply(tb, [
        ("  int total_cycles, div_cycles;",
         "  int total_cycles, div_cycles;\n  int tf;"),
        ("    total_cycles = 0;\n    div_cycles  = 0;",
         "    total_cycles = 0;\n    div_cycles  = 0;\n"
         '    tf = $fopen("lat.dat", "w");'),
        ("      total_cycles += cycles;",
         '      $fwrite(tf, "%x\\n", cycles);\n      total_cycles += cycles;'),
        ('    $display("TB_CYCLES total=%0d", total_cycles);',
         '    $fclose(tf);\n    $display("TB_CYCLES total=%0d", total_cycles);'),
    ])


def patch_cv32_tb_check(tb: str) -> str:
    """cv32 checker: counter-patched TB + per-op latency comparison."""
    return _apply(tb, [
        ("  int total_cycles, div_cycles;",
         "  int total_cycles, div_cycles;\n"
         "  // ---- per-op latency equivalence oracle (see description.txt) ----\n"
         "  logic [31:0] exp_lat [0:24575];\n"
         "  int vidx, eq_shown;"),
        ("    total_cycles = 0;\n    div_cycles  = 0;",
         "    total_cycles = 0;\n    div_cycles  = 0;\n"
         '    $readmemh("lat.dat", exp_lat);\n'
         "    vidx = 0;\n    eq_shown = 0;"),
        ("      total_cycles += cycles;",
         """      total_checks++;
      if (cycles !== int'(exp_lat[vidx])) begin
        total_errors++;
        if (eq_shown < 5) begin
          $display("TB_EQUIV_ERROR op#%0d op=%02h latency=%0d expected=%0d (per-op latency differs from reference)",
                   vidx, op, cycles, int'(exp_lat[vidx]));
          eq_shown++;
        end
      end
      vidx++;
      total_cycles += cycles;"""),
    ])


EQUIV_NOTE = """
EQUIVALENCE CONSTRAINT (it OVERRIDES the schedule freedom described above):
this benchmark's testbench IS an I/O-level equivalence
checker. Besides the normal data/protocol checks, it replays the same stimulus
and compares your design's I/O behavior against a recorded trace of the
provided starting point (`{sp}`): {oracle}
Any deviation — a different cycle count, different access timing, a different
`done`/`ready` time — is reported as TB_EQUIV_ERROR (first mismatches shown
with cycle numbers) and FAILS the evaluation. Consequently TB_CYCLES
{counters_etc} are pinned at the starting point's values by construction.

Within that fixed I/O behavior the internal implementation is free:
re-associating/factoring arithmetic, sharing subexpressions, restructuring mux
trees, strength reduction, cheaper operator implementations, register renames
or re-encodings — anything that keeps every observable output identical in
every cycle. Only area and critical-path delay can improve — optimize those.
""".rstrip() + "\n"


def starting_point(src: Path) -> tuple[str, str]:
    """(starting-point path relative to the benchmark, language) from metadata.json."""
    m = json.loads((src / "metadata.json").read_text())
    return m["starting_point"], m.get("language", "spirehdl")


def starting_point_rtl(src: Path, workdir: Path) -> Path:
    """Place the starting point's RTL in workdir: Spire sources are compiled, Verilog is used as-is."""
    sp_rel, language = starting_point(src)
    sp = src / sp_rel
    shutil.copy2(sp, workdir / sp.name)
    if language == "spirehdl":
        from rtlscout.evaluation import _compile_spirehdl, SPIREHDL_VERILOG_OUTPUT
        err, _ = _compile_spirehdl(workdir, sp.name)
        if err:
            raise RuntimeError(f"starting point compile failed: {err[:300]}")
        return workdir / SPIREHDL_VERILOG_OUTPUT
    return workdir / sp.name


def generate_trace(src: Path, dump_tb: str, trace_files: list, dest: Path) -> None:
    """Simulate the starting point under the dumper TB; copy the trace files into dest."""
    from rtlscout.correctness import simulate
    with tempfile.TemporaryDirectory(prefix="tl_trace_") as td_:
        td = Path(td_)
        (td / "tb.sv").write_text(dump_tb)
        for dat in sorted(src.glob("*.dat")):
            shutil.copy2(dat, td / dat.name)
        rtl = starting_point_rtl(src, td)
        res = simulate([td / "tb.sv", rtl], "tb", td)
        if not (res.ok and "PASS" in res.stdout):
            raise RuntimeError(f"trace-dump simulation failed: {res.stdout[-500:]}\n{res.stderr[-300:]}")
        for tfn in trace_files:
            shutil.copy2(td / tfn, dest / tfn)


def make_variant(bench: Bench, hdl: str, out_root: Path | None = None) -> Path:
    src = bench.dir(hdl)
    dest = (out_root or C.BENCHMARKS) / bench.name(hdl, equiv=True)
    if not src.is_dir():
        raise FileNotFoundError(src)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    sp, language = starting_point(src)
    oracle = _MEM_ORACLE if bench.oracle == "mem" else _LAT_ORACLE
    note = EQUIV_NOTE.format(oracle=oracle, counters_etc=_COUNTERS[bench.oracle], sp=Path(sp).name)
    (dest / "description.txt").write_text((src / "description.txt").read_text().rstrip() + "\n" + note)

    tb = (src / "tb.sv").read_text()
    for dat in sorted(src.glob("*.dat")):
        shutil.copy2(dat, dest / dat.name)
    if bench.oracle == "mem":
        spec = MEM_TRACE[bench.key]
        dump_tb, tb = patch_mem_tb_dump(tb, spec), patch_mem_tb_check(tb, spec)
        trace_files = ["trace.dat", "trace_len.dat"]
    else:
        dump_tb, tb = patch_cv32_tb_dump(tb), patch_cv32_tb_check(tb)
        trace_files = ["lat.dat"]
    generate_trace(src, dump_tb, trace_files, dest)
    (dest / "tb.sv").write_text(tb)

    ctx_src, ctx_dst = src / "context", dest / "context"
    if ctx_src.is_dir():
        ctx_dst.mkdir()
        for item in sorted(ctx_src.iterdir()):
            if item.name.startswith("_") or item.name in ("obj_dir", "__pycache__"):
                continue
            if item.is_dir():
                shutil.copytree(item, ctx_dst / item.name)
            else:
                shutil.copy2(item, ctx_dst / item.name)

    meta = json.loads((src / "metadata.json").read_text())
    meta["name"] = bench.name(hdl, equiv=True)
    meta.setdefault("source", {})
    if isinstance(meta["source"], dict):
        meta["source"]["variant"] = (f"equivalence track of {bench.name(hdl)}: trace-oracle testbench, "
                                     "generated by tasklevel.variants")
    (dest / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    return dest


def check_all(only: str | None = None) -> list[str]:
    """Rebuild every variant into a scratch dir and report files that differ from the tree."""
    diffs = []
    with tempfile.TemporaryDirectory(prefix="tl_variants_check_") as td:
        for bench in BENCHES:
            if only and only not in (bench.key, bench.folder):
                continue
            for hdl in HDLS:
                new = make_variant(bench, hdl, Path(td))
                old = bench.dir(hdl, equiv=True)
                if not old.is_dir():
                    diffs.append(f"{old.name}: missing in tree")
                    continue
                dc = filecmp.dircmp(old, new, ignore=["__pycache__"])
                for x in dc.diff_files + dc.left_only + dc.right_only:
                    diffs.append(f"{old.name}/{x}")
                for sub, sdc in dc.subdirs.items():
                    for x in sdc.diff_files + sdc.left_only + sdc.right_only:
                        diffs.append(f"{old.name}/{sub}/{x}")
    return diffs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="benchmark key or folder stem (e.g. qr8 or qr8_1r1w)")
    ap.add_argument("--check", action="store_true", help="rebuild into scratch and diff against the tree")
    a = ap.parse_args()
    if a.check:
        d = check_all(a.only)
        print("variants: byte-identical to the tree" if not d else "DIFFERENCES:\n  " + "\n  ".join(d))
        raise SystemExit(1 if d else 0)
    for bench in BENCHES:
        if a.only and a.only not in (bench.key, bench.folder):
            continue
        for hdl in HDLS:
            dest = make_variant(bench, hdl)
            print(f"created {dest.relative_to(C.REPO)}")


if __name__ == "__main__":
    main()
