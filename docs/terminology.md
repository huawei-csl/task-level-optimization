# Terminology

The words below are used the same way in the code, the data files and the paper.

**Track (arm).** The verification contract the agent optimizes under.
*Equivalence track*: the testbench replays the starting design's cycle-accurate I/O trace at the
memory port (or the per-operation latency for the ALU), so any design that passes is sequentially
equivalent to the start at that interface; the schedule and the access counters are frozen.
*Task-level track*: the testbench checks only the final data (results written to memory, or the
ALU's results per operation) against goldens; schedule, access pattern and micro-architecture are
free. Job ids carry `equiv` / `task`.

**Benchmark.** One design task with a start, a testbench and vectors; six are released
(`docs/benchmarks.md`). Each exists with a Spire and a Verilog starting design (`_spire`,
`_verilog`) and each of those has a generated equivalence variant (`_equiv`).

**Cell.** One benchmark under one track (and, where it varies, one HDL and one objective).
The arp experiment has 12 cells: six benchmarks x two tracks.

**Objective.** The cost the agent minimizes in the loop.
`arp` = area x cycles x period (area-runtime product).
`marpN` = movements^N x arp, movements = counted memory-port reads + writes (alpha = N).
`earpN` = energy^N x arp with the energy measured by gate-level simulation in the loop.
`energy` = measured circuit energy alone.

**Job.** One agent run on one benchmark variant under one objective:
`<bench>_<hdl>_<arm>_<objective>`, e.g. `qr8_spire_task_arp`. `python -m tasklevel.study --list-jobs`.

**Profile.** A yaml under `profiles/` naming the jobs, the model, the step budget and the stages.

**Run.** One invocation of a profile: `data/<tag>_<datetime>/` holding every job's agent run,
the rescores, logs, validation, power and the per-run report. `--new-run` mints one; without it
the pinned run of the profile is resumed.

**Repetition.** Each run of the same profile is one repetition of its cells. The paper used three.

**Rescore.** Every job's best design is re-measured under one uniform metric at the target delay
the agent used, so area, period, cycles, reads and writes are comparable across tracks and
objectives regardless of what the agent optimized.

**Per-run best.** The rescored best design of one job in one run.

**Eligible.** A per-run best counts if its rescore succeeded, it passed the independent testbench
(`validation.json`, when the run was validated) and, on the equivalence track, its counters are
frozen (cycles, reads and writes equal the start's; `counters_frozen` in the rescore).

**Winner.** The eligible per-run best with the lowest objective across a cell's repetitions. Tables
report winners relative to the start; the statistics tables report mean +- std over the per-run
bests.

**Validation (tb_v2).** A second, independently written testbench per benchmark with more than one
million stimuli (directed corner cases plus random), goldens from the upstream RTL or the bit-exact
start. It is not visible to the agent; it gates eligibility post hoc.

**Upstream comparison.** The external designs measured at their native interfaces through the same
flow (`tasklevel.upstream`), the reference point for the task-level winners' absolute ARP.

**Start parity.** The Spire and Verilog starting designs of a benchmark are at parity when their
measured area, period and cycles agree within the flow's noise; the fanout artifacts that broke
parity were fixed with marked register replications in the Verilog starts.
