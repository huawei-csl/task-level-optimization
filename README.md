# Task-level optimization

Benchmarks, study launcher, validation and analysis for

> **Beyond Equivalence: Task-Level Verification Unlocks Architectural Optimizations by LLM Agents**

An LLM agent optimizes RTL under two verification contracts: an *equivalence track*, where the
testbench replays the starting design's cycle-accurate I/O trace (sequential equivalence at the
interface), and a *task-level track*, where only the final data is checked and schedule and
micro-architecture are free. Six benchmarks, both tracks, measured through one synthesis and
timing flow (Yosys/ABC, OpenROAD, ASAP7) as area x cycles x period (ARP), plus movement- and
energy-directed objectives, an upstream comparison, and the experiment repeated with Verilog
instead of Spire starting designs.

This repository contains everything needed to rerun the study: the benchmarks, the launcher that
drives the [RTLScout](https://github.com/huawei-csl/rtlscout) engine, the independent validation
testbenches and the analysis that produces the paper's tables and figures. It contains no run
data; `data/` is where your runs go.

## Quick start

```bash
git clone https://github.com/huawei-csl/task-level-optimization && cd task-level-optimization
docker run -it --rm -v "$PWD":/work -w /work ghcr.io/huawei-csl/rtlscout:slim bash
uv pip install -e ".[test]"
cp .env.template .env && vi .env                          # provider keys (or: export ANTHROPIC_API_KEY=...)
python -m pytest -q tests                                  # offline checks
python -m tasklevel.run_all --profile smoke --new-run      # one short agent run, end to end
python -m tasklevel.run_all --profile arp --new-run --repeat 3
python -m tasklevel.analysis.arp                           # -> data/analysis/table_arp.*, fig_arp.*
```

The ladder from smoke test to the full set of experiments, with runtimes and what each step
needs, is in [docs/reproduction.md](docs/reproduction.md).

## Layout

```
benchmarks/<bench>_{spire,verilog}[_equiv]/   six benchmarks x two HDLs x two tracks (docs/benchmarks.md)
benchmarks/upstream/<name>/                   the five upstream designs at their native interfaces
profiles/<name>.yaml                          one experiment each: jobs, model, steps, stages
tasklevel/                                    the launcher and stages
  run_all.py     profile -> run directory -> stages (variants, study, power, validate, report)
  variants.py    generates the _equiv variants from the task benchmarks (trace oracle)
  study.py       baselines, agent runs (rtlscout.run_benchmark), uniform rescores
  power.py       measured circuit energy of designs (gate-level simulation)
  validate.py    independent tb_v2 sweep of per-run bests; stimulus regeneration + hashes
  report.py      per-run REPORT.md, tables and figure
  upstream.py    upstream comparison sweep and table
  analysis/      cross-run tables and figures: arp, energy, alpha_dial, verilog_table
validation/<bench>/                            tb_v2 testbenches and their deterministic generators
docs/                                          reproduction, benchmarks, terminology
tests/                                         offline checks (CI runs them in the rtlscout container)
```

A run lives in `data/<tag>_<datetime>/` with `study_results.json` (baselines, agent runs,
rescores), `validation.json`, `power.json`, `runs/<job>/...` (the engine's per-step
workspaces), `logs/`, and `REPORT.md`. `data/manifest.json` registers runs by profile; the
analysis modules read only complete runs from it and apply the eligibility gates
([docs/terminology.md](docs/terminology.md)).

## Profiles

| profile | what | jobs |
|---|---|---|
| `smoke` | plumbing check | MatMul, both tracks, 5 steps |
| `arp` | the main experiment (Spire starts) | 6 benchmarks x 2 tracks, ARP |
| `verilog_twins` | HDL independence | the same with Verilog starts |
| `energy_objectives` | movement/energy objectives on MatMul | marp1, marp2, earp1, earp2, energy x 2 tracks |
| `alpha_dial` | more MatMul designs for the exponent sweep | task-level arp, marp1, marp2 |
| `glm_arp` | the main experiment with a second model | as `arp`, `openrouter:z-ai/glm-5.2` |

One invocation of a profile is one repetition of its cells; `--repeat N` runs N in sequence.

## Paper tables and figures

Every table and figure of the paper is produced by one of the commands below from the runs
registered in `data/manifest.json` (profiles in parentheses). Table 1 (benchmark interfaces) and
Fig. 1 (the interface sketch) are static and have no generator.

| paper | command | output in `data/analysis/` |
|---|---|---|
| Fig. 2a, Table 2 | `python -m tasklevel.analysis.arp` (`arp`) | `fig_arp.*`, `table_arp.*` |
| Fig. 2b | `python -m tasklevel.analysis.arp --profiles glm_arp --suffix _glm` | `fig_arp_glm.*`, `table_arp_glm.*` |
| Fig. 3 | `python -m tasklevel.analysis.arp` | `fig_decomp.*` |
| Table 3 | `python -m tasklevel.upstream` (`arp` runs + `benchmarks/upstream/`) | `table_upstream.*`, `upstream_evals.json` |
| Fig. 4a | `python -m tasklevel.analysis.energy` (`arp` runs after `tasklevel.power`) | `fig_energy.*` |
| Fig. 4b | `python -m tasklevel.analysis.energy` | `fig_energy_matmul_reps.*` |
| Table 4 | `python -m tasklevel.analysis.energy` (`energy_objectives` + `arp`, after `tasklevel.power --population`) | `table_energy_columnwise.*` |
| Table 5, Fig. 5 | no extraction script currently provided (`tasklevel.analysis.alpha_dial` implements the α sweep over the pooled MatMul designs, not the paper's table and figure layout) | `table_alpha_dial.*`, `fig_alpha_dial.*` |
| Fig. 6 | `python -m tasklevel.analysis.energy --profiles glm_arp --suffix _glm` | `fig_energy_glm.*` |
| Table 6 | `python -m tasklevel.analysis.energy` | `table_energy_winners.*` |
| Table 7 | `python -m tasklevel.analysis.energy` | `table_energy_meanstd.*` |
| Table 8 | `python -m tasklevel.analysis.verilog_table` (`arp` + `verilog_twins`) | `table_verilog.*` |
| repetition statistics (Sec. 5) | `python -m tasklevel.analysis.arp` | `table_arp_stats.*` |

Per-run reports (`data/<run>/REPORT.md`) summarize one run and are not paper items.

## Engine and tools

The agent loop and the evaluation flow are the `rtlscout` package, pinned by commit in
`pyproject.toml`; the Spire HDL is `spire-hdl` from PyPI. The container image
`ghcr.io/huawei-csl/rtlscout:slim` carries Yosys, ABC, OpenROAD/OpenSTA, Verilator and the
ASAP7 PDK. Outside the container the offline tests run, the agent and measurement stages do not.

## Status

This is research code accompanying the paper, released so that the study can be rerun. It is a
cleaned-up and partly rewritten version of the scripts the experiments were run with, not a verbatim
copy: the benchmarks, testbenches, vectors, equivalence traces and the pinned engine commit are
those of the study, while the launcher and the analysis were consolidated (profiles, run manifest,
one package) and renamed. The code is provided as-is, without warranty or support commitment; agent
runs are stochastic and depend on hosted models that change over time, so reproduced numbers will
differ from the paper's. Issues and pull requests are welcome.

## License

BSD-3-Clause-Clear (see `LICENSE`). Benchmark designs derived from other projects keep their
licenses: `THIRD_PARTY_LICENSES.md`.
