#!/usr/bin/env python3
"""Generate vectors.dat for BOTH split OFDM benchmarks (mod + demod).

The loopback benchmark (ofdm32_1r1w) is end-to-end an identity on the payload
(demod(mod(x)) = x over the ideal wire), which a strong agent exploits by
shipping a masked pass-through. The split kills that:

  ofdm32_mod_1r1w   : payload bits -> the modulated time-domain symbol at the
                      loopback tap (40 = N+CP complex samples). Every output
                      sample depends on all carriers -- no copy shortcut.
  ofdm32_demod_1r1w : time-domain samples -> recovered payload bits. Inputs
                      are the TX samples passed through a NOISY channel
                      (seeded AWGN-ish uniform noise, below the decision
                      threshold), so the input isn't any transform of the
                      golden output either.

Goldens come from the upstream RTL (validation/ofdm/orig, LAT=37/LOG2N
patched exactly as the loopback benchmark): ofdm_tx driven with the SAME 21
jobs (seed 7) captures the tap stream; its samples + noise (seed 11, uniform
integer in [-NOISE_A, NOISE_A], clipped to int16) drive ofdm_rx for the demod
goldens. The generator VERIFIES the noisy-channel recovery still bit-matches
the payload for mod 0/1/2 (i.e. the noise is below threshold) -- the golden
is the actual RX output either way.

vectors.dat layouts (16-bit hex words, $readmemh), stride 93 per job:
  mod   : word 0 = mod; 1..12 = payload bits (word k = in_bits[16(k-1)+:16]);
          13..92 = golden samples, sample s -> word 13+2s = I, 14+2s = Q
          (two's-complement 16-bit)
  demod : word 0 = mod; 1..80 = NOISY input samples, same I/Q packing
          (word 1+2s = I, 2+2s = Q); 81..92 = golden output bits

Run: python validation/ofdm/gen_split_vectors.py   (rewrites the campaign vectors.dat of all four ofdm folders)
"""
import random
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parents[2]   # task-level-optimization/

UPSTREAM = HERE           # orig/*.v = pristine upstream fft/ofdm_tx/ofdm_rx

N, CP, DW = 32, 8, 16
NBITS = 6 * N            # 192
NSAMP = N + CP           # 40 complex samples per symbol
GAP = 400
SEED = 7                 # SAME jobs as the loopback benchmark
NOISE_SEED = 11
NOISE_A = 64             # uniform integer noise in [-A, A] per I/Q sample
LAT = 37

rng = random.Random(SEED)


def make_jobs():
    jobs = []
    for mod in (0, 1, 2):
        for _ in range(6):
            jobs.append((mod, rng.getrandbits(NBITS)))
    for _ in range(3):
        jobs.append((3, rng.getrandbits(NBITS)))
    rng.shuffle(jobs)
    return jobs


def used_mask(mod):
    per_axis = {0: 0b001, 1: 0b011, 2: 0b111, 3: 0b000}[mod]
    m = 0
    for a in range(2 * N):
        m |= per_axis << (3 * a)
    return m


TX_TB = f"""\
// Golden dump for ofdm_tx at N={N}/CP={CP}: drives stimuli.txt per cycle and
// writes (out_valid, out_last, out_r, out_i) per line -> golden.txt.
module tb;
  localparam N = {N};
  logic clk, rst, in_valid, out_valid, out_last;
  logic [1:0] mod;
  logic [6*N-1:0] in_bits;
  logic signed [{DW - 1}:0] out_r, out_i;

  ofdm_tx #(.DW({DW}), .N(N), .CP({CP})) dut
    (.clk(clk), .rst(rst), .in_valid(in_valid), .mod(mod), .in_bits(in_bits),
     .out_valid(out_valid), .out_last(out_last), .out_r(out_r), .out_i(out_i));

  initial clk = 0;
  always #5 clk = ~clk;

  integer fin, fout, rc;
  string line_buf;
  logic s_v;
  logic [1:0] s_m;
  logic [6*N-1:0] s_b;

  initial begin
    in_valid = 0; mod = 0; in_bits = 0;
    rst = 1;
    repeat (4) @(negedge clk);
    rst = 0;
    fin = $fopen("stimuli.txt", "r");
    fout = $fopen("golden.txt", "w");
    if (fin == 0 || fout == 0) begin $display("ERROR: files"); $fatal(1); end
    while (!$feof(fin)) begin
      void'($fgets(line_buf, fin));
      if (line_buf.len() == 0) continue;
      rc = $sscanf(line_buf, "%h %h %h", s_v, s_m, s_b);
      if (rc != 3) continue;
      in_valid = s_v; mod = s_m; in_bits = s_b;
      @(negedge clk);
      $fwrite(fout, "%h %h %h %h\\n", out_valid, out_last, out_r, out_i);
    end
    $fclose(fin); $fclose(fout);
    $display("GOLDEN_DONE");
    $display("PASS");
    $finish;
  end
endmodule
"""

RX_TB = f"""\
// Golden dump for ofdm_rx at N={N}/CP={CP}: drives stimuli.txt per cycle
// (in_valid, in_last, in_r, in_i, mod) and writes (out_valid, out_bits).
module tb;
  localparam N = {N};
  logic clk, rst, in_valid, in_last, out_valid;
  logic [1:0] mod;
  logic signed [{DW - 1}:0] in_r, in_i;
  logic [6*N-1:0] out_bits;

  ofdm_rx #(.DW({DW}), .N(N), .CP({CP})) dut
    (.clk(clk), .rst(rst), .in_valid(in_valid), .in_last(in_last),
     .in_r(in_r), .in_i(in_i), .mod(mod),
     .out_valid(out_valid), .out_bits(out_bits));

  initial clk = 0;
  always #5 clk = ~clk;

  integer fin, fout, rc;
  string line_buf;
  logic s_v, s_l;
  logic [1:0] s_m;
  logic signed [{DW - 1}:0] s_r, s_i;

  initial begin
    in_valid = 0; in_last = 0; in_r = 0; in_i = 0; mod = 0;
    rst = 1;
    repeat (4) @(negedge clk);
    rst = 0;
    fin = $fopen("stimuli.txt", "r");
    fout = $fopen("golden.txt", "w");
    if (fin == 0 || fout == 0) begin $display("ERROR: files"); $fatal(1); end
    while (!$feof(fin)) begin
      void'($fgets(line_buf, fin));
      if (line_buf.len() == 0) continue;
      rc = $sscanf(line_buf, "%h %h %h %h %h", s_v, s_l, s_r, s_i, s_m);
      if (rc != 5) continue;
      in_valid = s_v; in_last = s_l; in_r = s_r; in_i = s_i; mod = s_m;
      @(negedge clk);
      $fwrite(fout, "%h %h\\n", out_valid, out_bits);
    end
    $fclose(fin); $fclose(fout);
    $display("GOLDEN_DONE");
    $display("PASS");
    $finish;
  end
endmodule
"""


def patch_lat(lat: int) -> Path:
    """Same LAT/LOG2N patch as the loopback benchmark, into a local build."""
    bdir = HERE / f"_build_lat{lat}"
    shutil.rmtree(bdir, ignore_errors=True)
    bdir.mkdir()
    for v in sorted((UPSTREAM / "orig").glob("*.v")):
        text = v.read_text()
        text, n = re.subn(r"localparam LAT\s*=\s*70;", f"localparam LAT   = {lat};", text)
        text, n2 = re.subn(r"localparam LOG2N\s*=\s*6;",
                           "localparam LOG2N = $clog2(N);", text)
        if v.name in ("ofdm_tx.v", "ofdm_rx.v"):
            assert n == 1 and n2 == 1, f"patch anchors not found in {v.name}"
        (bdir / v.name).write_text(text)
    return bdir


def sim(bdir, tb_text, srcs_glob, stim_lines):
    from rtlscout.correctness import simulate
    (bdir / "stimuli.txt").write_text("\n".join(stim_lines) + "\n")
    (bdir / "tb.sv").write_text(tb_text)
    srcs = [bdir / "tb.sv"] + [bdir / s for s in srcs_glob]
    res = simulate(srcs, "tb", bdir, build_timeout=300)
    assert res.ok and "GOLDEN_DONE" in res.stdout, (
        f"golden sim failed:\n{res.stdout[-600:]}\n{res.stderr[-300:]}")
    return (bdir / "golden.txt").read_text().splitlines()


def s16(v: int) -> int:
    return v - 0x10000 if v >= 0x8000 else v


def u16(v: int) -> int:
    return v & 0xFFFF


def run_tx(bdir, jobs):
    lines = ["0 0 " + "0" * (NBITS // 4)] * 4
    for mod, bits in jobs:
        lines.append(f"1 {mod:x} {bits:0{NBITS // 4}x}")
        lines += [f"0 {mod:x} {'0' * (NBITS // 4)}"] * (GAP - 1)
    lines += ["0 0 " + "0" * (NBITS // 4)] * 8
    out = sim(bdir, TX_TB, ["fft.v", "ofdm_tx.v"], lines)
    # collect contiguous out_valid bursts of NSAMP samples, out_last on final
    symbols, cur = [], []
    for ln in out:
        v, l, r, i = ln.split()
        if v == "1":
            cur.append((s16(int(r, 16)), s16(int(i, 16))))
            if l == "1":
                symbols.append(cur)
                cur = []
    assert not cur, "stream ended mid-symbol"
    assert len(symbols) == len(jobs), f"{len(symbols)} symbols for {len(jobs)} jobs"
    assert all(len(s) == NSAMP for s in symbols), "bad symbol length"
    return symbols


def run_rx(bdir, jobs, noisy):
    zero = f"0 0 {0:04x} {0:04x} 0"
    lines = [zero] * 4
    for (mod, _), samples in zip(jobs, noisy):
        # mod must be stable from before the stream (upstream samples it at
        # stream start); hold it through the whole job window
        lines += [f"0 0 {0:04x} {0:04x} {mod:x}"] * 4
        for k, (r, i) in enumerate(samples):
            last = 1 if k == NSAMP - 1 else 0
            lines.append(f"1 {last:x} {u16(r):04x} {u16(i):04x} {mod:x}")
        lines += [f"0 0 {0:04x} {0:04x} {mod:x}"] * (GAP - NSAMP - 4)
    lines += [zero] * 8
    out = sim(bdir, RX_TB, ["fft.v", "ofdm_rx.v"], lines)
    outs, prev = [], "0"
    for ln in out:
        v, b = ln.split()
        if v == "1" and prev == "0":
            outs.append(int(b, 16))
        prev = v
    assert len(outs) == len(jobs), f"{len(outs)} out pulses for {len(jobs)} jobs"
    return outs


def main():
    jobs = make_jobs()
    bdir = patch_lat(LAT)

    symbols = run_tx(bdir, jobs)

    nrng = random.Random(NOISE_SEED)
    noisy = [[(max(-32768, min(32767, r + nrng.randint(-NOISE_A, NOISE_A))),
               max(-32768, min(32767, i + nrng.randint(-NOISE_A, NOISE_A))))
              for (r, i) in samples] for samples in symbols]

    outs = run_rx(bdir, jobs, noisy)

    # noise-below-threshold verification: recovery still bit-matches payload
    for k, ((mod, bits), out) in enumerate(zip(jobs, outs)):
        expect = bits & used_mask(mod)
        assert out == expect, (
            f"job {k} (mod={mod}): noisy recovery differs from payload -- "
            f"NOISE_A={NOISE_A} is above the decision threshold; lower it")

    def w(x):
        return f"{x & 0xFFFF:04x}"

    mod_words, demod_words = [], []
    for (mod, bits), samples, nsamples, out in zip(jobs, symbols, noisy, outs):
        mod_words.append(w(mod))
        demod_words.append(w(mod))
        for k in range(12):
            mod_words.append(w((bits >> (16 * k)) & 0xFFFF))
        for r, i in samples:
            mod_words += [w(r), w(i)]
        for r, i in nsamples:
            demod_words += [w(r), w(i)]
        for k in range(12):
            demod_words.append(w((out >> (16 * k)) & 0xFFFF))

    for hdl in ("spire", "verilog"):
        mod_dir = REPO / "benchmarks" / f"ofdm32_mod_1r1w_{hdl}"
        demod_dir = REPO / "benchmarks" / f"ofdm32_demod_1r1w_{hdl}"
        (mod_dir / "vectors.dat").write_text("\n".join(mod_words) + "\n")
        (demod_dir / "vectors.dat").write_text("\n".join(demod_words) + "\n")
        print(f"wrote {mod_dir / 'vectors.dat'} ({len(mod_words)} words)")
        print(f"wrote {demod_dir / 'vectors.dat'} ({len(demod_words)} words)")
    print(f"noise A={NOISE_A}: payload recovery verified for all {len(jobs)} jobs")


if __name__ == "__main__":
    main()
