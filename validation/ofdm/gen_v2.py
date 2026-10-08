#!/usr/bin/env python3
"""ofdm mod/demod validation-v2 vectors: 25,000 symbols each (~1M complex
samples per side), goldens captured from the patched upstream RTL by REUSING
the campaign generator's machinery (gen_split_vectors.py is imported; its
TX/RX capture rig, LAT patch, noise model and below-threshold verification
run unchanged). Jobs are processed in batches of 5,000 to bound the stimulus
file and memory footprint.

Job mix: directed block (all-zero and all-one payloads per mod, single-bit
and alternating patterns, per-mod) then uniform-random payloads with the
campaign's mod ratio (mod 0/1/2 equal shares, ~4%% mod 3). Noise: the
campaign's uniform [-64, 64] model, fresh seed, below-threshold recovery
asserted for every job as in the campaign generator.

Outputs: validation/ofdm/vectors_v2_{mod,demod}.dat
(stride 93/job, campaign layout).

Run: python validation/ofdm/gen_v2.py
"""
import importlib.util
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

GEN = HERE / "gen_split_vectors.py"
spec = importlib.util.spec_from_file_location("gen_split_vectors", GEN)
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

NBITS, NSAMP = g.NBITS, g.NSAMP
TOTAL = 25_000
BATCH = 5_000
SEED = 2203
NOISE_SEED = 2211

rng = random.Random(SEED)


def make_jobs_v2():
    jobs = []
    ones = (1 << NBITS) - 1
    alt = int("a" * (NBITS // 4), 16)
    for mod in (0, 1, 2, 3):                       # directed per mod
        jobs.append((mod, 0))                      # all-zero payload
        jobs.append((mod, ones))                   # all-one payload
        jobs.append((mod, alt))                    # alternating bits
        jobs.append((mod, ~alt & ones))
        jobs.append((mod, 1))                      # single bit low
        jobs.append((mod, 1 << (NBITS - 1)))       # single bit high
        jobs.append((mod, 0x3F))                   # one full carrier
        jobs.append((mod, 0x3F << (NBITS - 6)))
    while len(jobs) < TOTAL:
        r = rng.random()
        mod = 3 if r > 0.96 else int(r * 3.125) % 3
        jobs.append((mod, rng.getrandbits(NBITS)))
    return jobs


def main():
    jobs = make_jobs_v2()
    print(f"{len(jobs)} jobs ({len(jobs) * NSAMP} samples per side)")
    bdir = g.patch_lat(g.LAT)
    nrng = random.Random(NOISE_SEED)

    def w(x):
        return f"{x & 0xFFFF:04x}"

    mod_words, demod_words = [], []
    for lo in range(0, len(jobs), BATCH):
        batch = jobs[lo:lo + BATCH]
        symbols = g.run_tx(bdir, batch)
        noisy = [[(max(-32768, min(32767, r + nrng.randint(-g.NOISE_A, g.NOISE_A))),
                   max(-32768, min(32767, i + nrng.randint(-g.NOISE_A, g.NOISE_A))))
                  for (r, i) in s] for s in symbols]
        outs = g.run_rx(bdir, batch, noisy)
        for k, ((mod, bits), out) in enumerate(zip(batch, outs)):
            expect = bits & g.used_mask(mod)
            assert out == expect, (
                f"job {lo + k} (mod={mod}): noisy recovery differs from payload")
        for (mod, bits), samples, nsamples, out in zip(batch, symbols, noisy, outs):
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
        print(f"  batch {lo}..{lo + len(batch)}: captured + verified")

    (HERE / "vectors_v2_mod.dat").write_text("\n".join(mod_words) + "\n")
    (HERE / "vectors_v2_demod.dat").write_text("\n".join(demod_words) + "\n")
    print(f"wrote vectors_v2_mod.dat ({len(mod_words)} words), "
          f"vectors_v2_demod.dat ({len(demod_words)} words)")


if __name__ == "__main__":
    main()
