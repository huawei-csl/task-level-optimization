# Reproduction

Everything runs from the repository root inside the RTLScout container (EDA tools, ASAP7, the
engine's Python environment). Agent runs call a hosted LLM; set the provider key first.

## 0. Environment

```bash
docker run -it --rm -v "$PWD":/work -w /work ghcr.io/huawei-csl/rtlscout:slim bash
uv pip install -e ".[test]"             # the container's venv has uv, no pip
cp .env.template .env && vi .env        # provider keys; .env is gitignored and loaded from the repo root
python -m pytest -q tests               # no API calls; ~1 min (set TASKLEVEL_SLOW=1 for the tb_v2 control)
python -m tasklevel.variants --check    # the released _equiv variants reproduce byte for byte
```

Spire runs quote the spire-hdl documentation in the agent's prompt. The PyPI release `spire-hdl 0.4.0`
does not ship it; if the engine stops with "Spire HDL prompts quote the spire-hdl documentation",
clone [spire-hdl](https://github.com/huawei-csl/spire-hdl) at the installed version and set
`RTLSCOUT_SPIRE_HDL_DIR=/path/to/spire-hdl` (later spire-hdl releases make this unnecessary).

The engine reads the keys from `.env` in the directory it is started from (as the RTLScout repository
does), or from the file named by `RTLSCOUT_ENV_FILE`; exporting `ANTHROPIC_API_KEY` /
`OPENROUTER_API_KEY` in the shell works as well and takes precedence.

Model ids are `<provider>:<model>`; the paper's runs used `anthropic:claude-opus-5` with 40 steps.
`glm_arp` uses `openrouter:z-ai/glm-5.2` (`OPENROUTER_API_KEY`).

Run data goes to `data/` (gitignored); point it elsewhere with `TASKLEVEL_DATA=/big/disk`.

## 1. Smoke (minutes)

```bash
python -m tasklevel.run_all --profile smoke --new-run
cat data/smoke_*/REPORT.md
```

One five-step agent run per track on MatMul, then baselines, rescores and the per-run report
(a two-row table). Confirms the tools, the key and the paths.

## 2. The ARP experiment (the paper's main result)

```bash
python -m tasklevel.run_all --profile arp --new-run --repeat 3     # 3 repetitions x 12 cells
python -m tasklevel.analysis.arp                                   # data/analysis/table_arp*, fig_arp
```

Each repetition runs the 12 jobs in parallel (`parallel: 12`; lower it in the profile for a small
machine), then the uniform rescore of every per-run best and the tb_v2 validation. One repetition
takes a few hours wall-clock and about 40 model steps per job. A killed run resumes with the same
command without `--new-run`; completed jobs are skipped.

`data/<run>/REPORT.md` is the per-run report; `python -m tasklevel.analysis.arp` combines the
complete runs of the profile (`data/manifest.json`) into winners, decomposition and statistics.

Which command produces which table or figure of the paper is listed in the README
("Paper tables and figures"); every analysis reads only complete runs from `data/manifest.json`.

## 3. Upstream comparison

```bash
python -m tasklevel.upstream        # sweeps the five upstream designs, writes data/analysis/table_upstream.*
```

Needs the arp runs for the task-level column; `--sweep-only` measures without them.

## 4. HDL independence

```bash
python -m tasklevel.run_all --profile verilog_twins --new-run --repeat 3
python -m tasklevel.analysis.verilog_table
```

## 5. Energy-directed objectives and the movement dial (MatMul)

```bash
python -m tasklevel.run_all --profile energy_objectives --new-run      # marp1/2, earp1/2, energy; both tracks
python -m tasklevel.power --profile energy_objectives --population     # optional: every valid design point
python -m tasklevel.analysis.energy
python -m tasklevel.run_all --profile alpha_dial --new-run --repeat 2  # optional: a larger design pool
python -m tasklevel.analysis.alpha_dial --stats
```

The `earp*`/`energy` jobs run the gate-level power simulation inside the loop, so their
evaluations are several times slower than `arp`.

## 6. Another model

```bash
python -m tasklevel.run_all --profile glm_arp --new-run --repeat 3
python -m tasklevel.analysis.arp --profiles glm_arp --suffix _glm
```

Copy a profile and change `model:` for any other provider the engine supports.

## Stages, resuming, forcing

`run_all` runs the profile's `stages` in order and leaves `state/<stage>.done` markers in the run
directory; re-invocation skips done stages, `--stages power,report` runs a subset, `--force`
ignores the markers. Each stage is also a module of its own with `--run <run>`:
`tasklevel.study`, `tasklevel.power`, `tasklevel.validate`, `tasklevel.report`.

## What determines the numbers

- Synthesis and timing: Yosys/ABC + OpenROAD/OpenSTA on ASAP7 at a 500 ps target, as pinned by the
  container image and the `rtlscout` commit in `pyproject.toml`. Every evaluation is a fresh tool
  process; no in-process state carries over between designs.
- Cycles, reads, writes: counted by the benchmark testbench under Verilator.
- The agent: the model named in the profile, the engine's prompt at the pinned commit, the
  benchmark description. LLM sampling makes individual runs differ; the paper reports three
  repetitions per cell and the eligible best.
