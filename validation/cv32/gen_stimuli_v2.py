#!/usr/bin/env python3
"""cv32 ALU validation-v2 stimuli: 2M vectors.

Layout of vectors_v2_stim.dat (12 hex fields, no expected outputs -- goldens
are captured from the bit-exact starting design by tb_v2_capture.sv):
  1. the full original 20k vectors verbatim (their known goldens double as a
     capture-rig crosscheck),
  2. a directed block: div-by-zero / INT_MIN over all div-rem ops, signed and
     unsigned boundary operand pairs for every op, SIMD lane extremes for the
     vector modes, bmask grid sweeps for the ops that use bmask fields,
  3. operand-rerandomized bulk to 2,000,000 lines: each line copies the
     modifier fields (op vm bma bmb ive cpx sbr csh, cchk) of a random
     original line of the same op -- staying inside the validated control
     space -- with fresh uniform 32-bit operands a, b, c.

Run: python validation/cv32/gen_stimuli_v2.py
"""
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parents[2]   # task-level-optimization/
SRC = REPO / "benchmarks/cv32e40p_alu_divh_spire/vectors.dat"
DST = HERE / "vectors_v2_stim.dat"
TOTAL = 2_000_000
SEED = 42
DIV_OPS = (0x30, 0x31, 0x32, 0x33)
BOUND = (0x00000000, 0x00000001, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF,
         0xAAAAAAAA, 0x55555555, 0x00010000, 0x0000FFFF, 0xFF00FF00)
LANE8 = (0x00, 0x01, 0x7F, 0x80, 0xFF)

rng = random.Random(SEED)


def parse(path):
    lines = []
    for ln in open(path):
        if ln.startswith("#") or not ln.strip():
            continue
        f = ln.split()
        if len(f) == 14:
            lines.append(f)
    return lines


def main():
    orig = parse(SRC)
    by_op = {}
    for f in orig:
        by_op.setdefault(f[0], []).append(f)
    ops = sorted(by_op)

    out = []

    def emit(f, a, b, c):
        # modifier fields + cchk from template f, fresh operands
        out.append(f"{f[0]} {f[1]} {f[2]} {f[3]} {f[4]} {f[5]} {f[6]} {f[7]} "
                   f"{a:08x} {b:08x} {c:08x} {f[11]}")

    # 1. original 20k verbatim (stimulus fields only)
    for f in orig:
        out.append(f"{f[0]} {f[1]} {f[2]} {f[3]} {f[4]} {f[5]} {f[6]} {f[7]} "
                   f"{f[8]} {f[9]} {f[10]} {f[11]}")
    n_orig = len(out)

    # 2. directed block
    for opx in ops:
        op = int(opx, 16)
        tmpl = by_op[opx][0]
        # boundary operand grid (10x10 over a,b; c random boundary)
        for a in BOUND:
            for b in BOUND:
                emit(tmpl, a, b, rng.choice(BOUND))
        if op in DIV_OPS:
            for b in BOUND:                       # divisor a = 0 (div-by-zero)
                emit(tmpl, 0, b, 0)
            emit(tmpl, 0xFFFFFFFF, 0x80000000, 0)  # INT_MIN / -1
            emit(tmpl, 0x80000000, 0x80000000, 0)
            for _ in range(200):                   # tiny divisors, hard latencies
                emit(tmpl, rng.randint(1, 3), rng.getrandbits(32), 0)
        # SIMD lane extremes for every vector mode seen for this op (vm is
        # substituted into the template -- each mode actually exercised)
        vms = {f[1] for f in by_op[opx]}
        for vm in sorted(vms - {"0"}):      # set order varies per process; keep the file reproducible
            f2 = list(tmpl)
            f2[1] = vm
            for la in LANE8:
                for lb in LANE8:
                    a = int.from_bytes(bytes([la] * 4), "big")
                    b = int.from_bytes(bytes([lb] * 4), "big")
                    emit(f2, a, b, 0)
        # bmask sweeps where the op family uses them (nonzero in any original)
        if any(f[2] != "00" or f[3] != "00" for f in by_op[opx]):
            for bma in range(0, 32, 3):
                for bmb in range(0, 32, 3):
                    f2 = list(tmpl)
                    f2[2], f2[3] = f"{bma:02x}", f"{bmb:02x}"
                    emit(f2, rng.getrandbits(32), rng.getrandbits(32),
                         rng.getrandbits(32))
    n_directed = len(out) - n_orig

    # 3a. stratified floor: >= PATTERN_FLOOR vectors for EVERY distinct
    # original modifier pattern (op vm bma bmb ive cpx sbr csh, cchk), so a
    # bug confined to a rare op/modifier combination cannot slip through
    PATTERN_FLOOR = 50
    patterns = {}
    for f in orig:
        patterns.setdefault(tuple(f[:8]) + (f[11],), f)
    for f in patterns.values():
        for _ in range(PATTERN_FLOOR):
            emit(f, rng.getrandbits(32), rng.getrandbits(32), rng.getrandbits(32))
    n_strat = len(out) - n_orig - n_directed

    # 3b. operand-rerandomized bulk to TOTAL (original op distribution)
    while len(out) < TOTAL:
        f = rng.choice(orig)
        emit(f, rng.getrandbits(32), rng.getrandbits(32), rng.getrandbits(32))

    with open(DST, "w") as fh:
        fh.write("# op vm bma bmb ive cpx sbr csh a b c cchk   (stimuli only; "
                 "goldens captured from the starting design)\n")
        fh.write("\n".join(out) + "\n")
    print(f"{len(out)} vectors -> {DST}  (orig {n_orig}, directed {n_directed}, "
          f"stratified {n_strat}, random {len(out)-n_orig-n_directed-n_strat})")


if __name__ == "__main__":
    main()
