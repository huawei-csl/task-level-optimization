#!/usr/bin/env python3
"""jpeg_idct_2d_1r1w validation-v2 stimuli: 100,000 blocks (6.4M coefficients).

Layout of stim_v2.dat: 64 x 16-bit hex words per block ($readmemh, flat).
Goldens are captured from the bit-exact starting design by tb_v2.sv in
+capture mode. Block mix:
  1. the 323 campaign input blocks verbatim (their upstream-derived goldens
     crosscheck the capture rig),
  2. directed corners (the campaign generator's menu: zero, max/min DC, all
     max/min, alternating extremes, per-position impulses, +/-1 LSB),
  3. random to 100k with the campaign distribution: 30% full-range uniform,
     50% JPEG-realistic (low-frequency-heavy, small magnitudes), 20% sparse.

Run: python validation/jpeg/gen_stimuli_v2.py  (or via python -m tasklevel.validate --regen)
"""
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parents[2]   # task-level-optimization/
CAMPAIGN = REPO / "benchmarks/jpeg_idct_2d_1r1w_spire/vectors.dat"
TOTAL = 100_000
SEED = 0x77A11D

rng = random.Random(SEED)


def campaign_blocks():
    words = [int(w, 16) for w in CAMPAIGN.read_text().split()]
    assert len(words) % 128 == 0
    blocks = []
    for c in range(len(words) // 128):
        blocks.append([w & 0xFFFF for w in words[c * 128: c * 128 + 64]])
    return blocks


def main():
    blocks = campaign_blocks()
    n_campaign = len(blocks)

    # directed corners
    blocks.append([0] * 64)
    blocks.append([32767 & 0xFFFF] + [0] * 63)
    blocks.append([-32768 & 0xFFFF] + [0] * 63)
    blocks.append([32767] * 64)
    blocks.append([-32768 & 0xFFFF] * 64)
    blocks.append([((-1) ** i * 32767) & 0xFFFF for i in range(64)])
    for pos in range(8):
        b = [0] * 64
        b[pos] = 20470
        blocks.append(b)
    for pos in range(8):
        b = [0] * 64
        b[pos * 8 + pos] = -20470 & 0xFFFF
        blocks.append(b)
    blocks.append([(1 if i % 2 == 0 else -1) & 0xFFFF for i in range(64)])
    n_directed = len(blocks) - n_campaign

    while len(blocks) < TOTAL:
        r = rng.random()
        if r < 0.30:
            blocks.append([rng.randint(-32768, 32767) & 0xFFFF for _ in range(64)])
        elif r < 0.80:
            b = []
            for row in range(8):
                for col in range(8):
                    span = max(1, 2048 >> (row + col))
                    b.append(rng.randint(-span, span) & 0xFFFF)
            blocks.append(b)
        else:
            b = [0] * 64
            for _ in range(rng.randint(1, 6)):
                b[rng.randrange(64)] = rng.randint(-4096, 4096) & 0xFFFF
            blocks.append(b)

    with open(HERE / "stim_v2.dat", "w") as fh:
        for b in blocks:
            fh.write("\n".join(f"{v:04x}" for v in b) + "\n")
    print(f"{len(blocks)} blocks (campaign {n_campaign}, directed {n_directed}, "
          f"random {len(blocks) - n_campaign - n_directed}) -> {HERE / 'stim_v2.dat'}")


if __name__ == "__main__":
    main()
