#!/usr/bin/env python3
"""qr8 validation-v2 vectors: 16,040 jobs (~1.03M input elements), goldens
captured from the UNMODIFIED upstream qr RTL exactly as the campaign
generator does.

Job mix: the original 15-job classes, extra directed edges (scaled
identities, lower-triangular, single-hot max, symmetric, alternating-sign,
negative-heavy), then bulk random in two families -- diagonally dominant
(three magnitude tiers, as the campaign) and general matrices with entries
|x| <= 8192, which keeps every column 2-norm below sqrt(8)*8192 ~ 23.2k <
32767, i.e. inside the upstream fixed-point range (no overflow regimes, so
winner-vs-upstream disagreement cannot be blamed on undefined wrap
behavior). Deterministic (seed 1103).

Output: validation/qr8/vectors_v2.dat (stride 128/job).
Temp stimuli/golden files go to a work dir and are deleted.

Run: python validation/qr8/gen_v2.py
"""
import random
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

SRC_DEBUG = HERE          # tb_golden_dump.sv + orig/*.v (pristine upstream qr)
N, DW = 8, 16
GAP = 60
SEED = 1103
N_DD, N_MID, N_BIG, N_GEN = 4000, 2000, 2000, 8000

rng = random.Random(SEED)


def dd_random(diag_lo=4000, diag_hi=8000, off=2000):
    m = [[rng.randint(-off, off) for _ in range(N)] for _ in range(N)]
    for i in range(N):
        m[i][i] = rng.randint(diag_lo, diag_hi)
    return m


def gen_random(mag=8192):
    return [[rng.randint(-mag, mag) for _ in range(N)] for _ in range(N)]


def make_jobs():
    jobs = []
    # original directed classes
    for s in (1, 1000, 8192, 23000):
        jobs.append([[(s if i == j else 0) for j in range(N)] for i in range(N)])
    jobs.append([[0] * N for _ in range(N)])                                  # zero
    jobs.append([[(rng.randint(500, 4000) if j >= i else 0) for j in range(N)]
                 for i in range(N)])                                          # upper
    jobs.append([[(rng.randint(500, 4000) if j <= i else 0) for j in range(N)]
                 for i in range(N)])                                          # lower
    small = [[rng.randint(-8, 8) for _ in range(N)] for _ in range(N)]
    for i in range(N):
        small[i][i] = 16 + i
    jobs.append(small)
    onehot = [[0] * N for _ in range(N)]
    onehot[N - 1][0] = 8192                                                   # single-hot
    jobs.append(onehot)
    sym = gen_random(4000)
    for i in range(N):
        for j in range(i):
            sym[i][j] = sym[j][i]
    jobs.append(sym)                                                          # symmetric
    alt = [[(8000 if (i + j) % 2 == 0 else -8000) for j in range(N)] for i in range(N)]
    jobs.append(alt)                                                          # alternating signs
    negm = dd_random(diag_lo=28000, diag_hi=32000, off=12000)
    for i in range(N):
        for j in range(N):
            if i != j:
                negm[i][j] = -abs(negm[i][j])
    jobs.append(negm)                                                         # negative-heavy
    for k in range(28):                                                       # sparse random max
        m = [[0] * N for _ in range(N)]
        for _ in range(rng.randint(1, 6)):
            m[rng.randrange(N)][rng.randrange(N)] = rng.choice((-8192, 8192))
        jobs.append(m)
    # bulk
    jobs += [dd_random() for _ in range(N_DD)]
    jobs += [dd_random(diag_lo=12000, diag_hi=16000, off=6000) for _ in range(N_MID)]
    jobs += [dd_random(diag_lo=28000, diag_hi=32000, off=12000) for _ in range(N_BIG)]
    jobs += [gen_random() for _ in range(N_GEN)]
    return jobs


def a_hex(mat):
    v = 0
    for k in range(N * N):
        v |= (mat[k // N][k % N] & 0xFFFF) << (16 * k)
    return f"{v:0{N * N * 4}x}"


def main():
    jobs = make_jobs()
    print(f"{len(jobs)} jobs ({len(jobs) * N * N} input elements)")

    work = Path(tempfile.mkdtemp(prefix="qr8v2_"))
    zero = "0" * (N * N * 4)
    with open(work / "stimuli.txt", "w") as fh:
        for _ in range(4):
            fh.write(f"0 {zero}\n")
        for mat in jobs:
            h = a_hex(mat)
            fh.write(f"1 {h}\n1 {h}\n")
            for _ in range(GAP - 2):
                fh.write(f"0 {zero}\n")
        for _ in range(8):
            fh.write(f"0 {zero}\n")

    tb = (SRC_DEBUG / "tb_golden_dump.sv").read_text()
    (work / "tb_golden_dump.sv").write_text(tb)

    from rtlscout.correctness import simulate
    srcs = [work / "tb_golden_dump.sv"] + sorted((SRC_DEBUG / "orig").glob("*.v"))
    res = simulate(srcs, "tb", work)
    assert res.ok and "GOLDEN_DONE" in res.stdout, (
        f"golden sim failed:\n{res.stdout[-800:]}\n{res.stderr[-400:]}")

    goldens = []
    for ln in (work / "golden.txt").read_text().splitlines():
        d, r = ln.split()
        if d == "1":
            goldens.append(int(r, 16))
    assert len(goldens) == len(jobs), f"expected {len(jobs)}, got {len(goldens)}"

    with open(HERE / "vectors_v2.dat", "w") as fh:
        for mat, rflat in zip(jobs, goldens):
            for k in range(N * N):
                fh.write(f"{mat[k // N][k % N] & 0xFFFF:04x}\n")
            for k in range(N * N):
                fh.write(f"{(rflat >> (16 * k)) & 0xFFFF:04x}\n")
    shutil.rmtree(work, ignore_errors=True)
    print(f"vectors_v2.dat: {len(jobs)} jobs x 128 words -> {HERE / 'vectors_v2.dat'}")


if __name__ == "__main__":
    main()
