# Pinned-Upstream Resource Experiment

## Purpose

Measure embedded feasibility of CP2N2 v5.1.1 through functionality, absolute
resource demand and observed operating limits. This is not a Jetson speed contest.
The upstream checkout remains pinned at
`36fbae002b0a9e066416ea9cafbb966a33fe53bb` and unmodified.

This campaign adds external monitoring to the original workload entry points.
Its timing values must be labeled **profiled**, rather than pooled with the
earlier uninstrumented reproduction. A sustained workload is a separate extension
using the original edge-task factory and explicit reset/recalibration sequence.

## Collected signals

- Per-process PID and creation time, parent PID, name, thread count, RSS, virtual
  memory and cumulative user/system CPU time.
- Interval CPU utilization normalized to one logical CPU. First observations
  have no utilization value rather than an invented zero.
- Proportional set size (PSS) and unique set size (USS), at most once per second;
  unsupported/permission-denied data remains unavailable.
- A discovered process tree, including known children after reparenting; observer
  and tegrastats processes are identified separately and excluded from workload
  memory sums. PID identity uses creation time to avoid attaching to reused PIDs.
- Raw tegrastats lines and parsed platform RAM/swap, CPU utilization, GPU activity,
  CPU/GPU/SoC/junction temperatures and available power rails. CPU/GPU frequencies
  remain available in raw lines even where no normalized field is emitted.
- Monotonic timestamps on all observations, phase windows, task outcomes and
  pre/post upstream-source SHA-256 checks.

Default process/platform cadence is 250 ms. Scanning and PSS reads take time,
so the recorded timestamps, not the configured cadence alone, define actual
sampling intervals. Very short-lived children can be missed. Access errors are
recorded; summed RSS can double-count shared pages. These limitations must remain
visible in any paper table.

## Workloads

The initial campaign profiles the original `overhead`, `http`, `e3-control`,
`e3-provider`, and aggregate `core` entry points. Original task definitions,
invocation counts, seeds and concurrency profiles are not changed. Three-second
platform idle windows bracket the command, outside the upstream process.

Whole-command resource summaries include imports, evaluator computation, output
and plotting. They are valid deployment costs for that command, not a claim that
the control-plane core alone consumes that memory or energy.

The sustained extension uses one persistent upstream default orchestrator and
its three-backend registry. It obtains its task from `make_edge_task`, calls
`execute_task`, and performs the same explicit reset/recalibration sequence as
the original overhead evaluator. A three-second ready-worker idle phase precedes
the five-minute run. It admits ten requests per second on a monotonic schedule,
counts skipped releases, avoids catch-up bursts, and records success, selected
backend, errors, maintenance and idempotent-replay status. The measured steady
window excludes startup and output finalization. It does not inject faults,
change upstream recovery, or enable a hosted model or physical hardware.

## Operational guards

The controller preserves device power mode, clock policy and factory thermal
protection. It derives a conservative stop temperature from the lower of 80 C
and five degrees below the readable passive/critical thermal trip points. The
observed trip points and chosen threshold are archived, not represented as a
universal device rating. It also stops the workload on missing/stale temperature
telemetry after startup, less than 512 MiB free archive disk space, or the
explicit command timeout. A guard abort is retained as a non-success outcome.

No experiment intentionally drives the board to throttling or thermal limits.
Five minutes at one request rate cannot establish long-term thermal equilibrium,
leak freedom, sustained availability or suitability for a smartwatch.

## Runbook

Use the isolated Python 3.12 environment prepared for the original release:

```bash
_temp/cp2n2-python312/bin/python -m evaluation.reproduce_original overhead \
  --profile --output-dir evaluation/results/my-upstream-profile/overhead --timeout-s 90
_temp/cp2n2-python312/bin/python -m evaluation.reproduce_original http \
  --profile --output-dir evaluation/results/my-upstream-profile/http --timeout-s 90
_temp/cp2n2-python312/bin/python -m evaluation.reproduce_original sustained \
  --profile --duration-s 300 --rate-hz 10 --timeout-s 360 \
  --output-dir evaluation/results/my-upstream-profile/sustained-300s-10hz
_temp/cp2n2-python312/bin/python -m evaluation.summarize_profiles \
  evaluation/results/my-upstream-profile
```

Every run directory must be new. The sustained run requires profiling so guards
cannot be silently omitted. The original entry-point commands do not consume
the extension's duration/rate options.

Each run archives `profiling.json`, `process_tree.jsonl`, `tegrastats.jsonl`,
`resource-summary.json`, upstream outputs, logs, provenance and a final checksum
manifest. The report command verifies those manifests before generating tables
and the sustained trace. Raw data is never overwritten by summarization.

Power integrates only observed adjacent intervals. Gaps longer than three nominal
intervals are excluded. Coverage is shown rather than extrapolating energy across
missing data. Platform-before/after windows provide context; they do not isolate
monitoring cost, background activity or control-plane-only energy.

The existing A6 failure remains a valid incomplete condition even if its resource
trace is complete. Passing local/E3 paths establish only their tested conditions.