# Embedded Feasibility Study

## Scope and implementation status

Evaluate CP2N2 deployment feasibility on embedded Linux. The control plane,
adapters and twins are unchanged. Smartwatches motivate the study but are not
evaluated. A real Jetson host does not make the behavioral twins or SDK simulator
physical-PNN evidence. Venue and author approval of final claims remain pending.

The implementation supplies isolated counted or duration-based trials, process
sampling, Jetson telemetry, bracketing ready-worker idle windows, sampler
calibration, provenance, raw request data and reproducible summaries. Bundled
campaigns are **exploratory checks**, not confirmatory experiments. Full contention,
memory-limit, sustained-operation safety guards and publication analysis remain
pending.

## Runbook

From the repository root, use the existing working virtual environment:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m evaluation.evaluate_tegra_resources \
  --campaign docs/embedded-feasibility/smoke.json \
  --output-dir evaluation/results/embedded/smoke-001 \
  --interval-ms 250 --timeout-s 60
.venv/bin/python -m evaluation.evaluate_tegra_resources \
  --verify evaluation/results/embedded/smoke-001
```

Every output directory must be new. Use a different suffix for a rerun, never
delete the preceding evidence to reuse its name. Exit code 2 means at least one
workload failed, timed out, used an unexpected backend, or failed maintenance;
partial data is retained. Interrupting the controller cleans up registered worker,
sampler and service processes but leaves an incomplete archive that must not be
used as a completed trial. `--verify` checks archive integrity and recomputes
summaries, not whether the study design supports a publication claim.

For portable harness testing without Jetson sensors, explicitly add
`--no-tegrastats`. Process telemetry still requires Linux `/proc`. Missing power
data is null, never zero. The legacy `--workload` interface remains available but
is not the new isolated protocol and may overwrite its historical aggregate files.
Do not use legacy evaluation commands for this study.

The smoke campaign exercises direct and orchestrated edge, chemical, wetware and
localhost HTTP paths, followed by a paced edge run using native recovery. Only
these synthetic paths are launched; it does not run the CL SDK or an LLM.

### Sampler calibration and duration validation

```bash
.venv/bin/python -m evaluation.evaluate_tegra_resources \
  --calibrate --calibration-repeats 6 --calibration-duration-s 3 \
  --output-dir evaluation/results/embedded/calibration-001 --timeout-s 60
.venv/bin/python -m evaluation.evaluate_tegra_resources \
  --verify evaluation/results/embedded/calibration-001
.venv/bin/python -m evaluation.evaluate_tegra_resources \
  --campaign docs/embedded-feasibility/duration-smoke.json \
  --output-dir evaluation/results/embedded/duration-001 --timeout-s 60
```

Calibration generates six matched blocks of three trials: periodic sampling off,
250 ms platform/process sampling, and 1 s platform/process sampling. Each block
uses one seed, workload, warmup and duration. All six condition orders appear
once by default. Only sampling varies within a block. Per-request logging and
controller timeout polling remain enabled even in the off condition.

`summary.json` reports paired block differences and two-sided Student t intervals
over block differences, assuming independent blocks. Individual requests are not
treated as independent replications. Failed/incomplete pairs are listed explicitly,
and their raw trials remain archived. These intervals describe exploratory
variability; they do not prove equivalence or isolate each sampler's individual
effect. Increasing `--calibration-repeats` repeats the six-order rotation.

The duration validation runs three six-second, 100 requests/s local edge windows:
direct, directed orchestration, and capability selection with native recovery.
Three-second ready-worker idle windows bracket each measurement. This validates
duration and baseline accounting, not battery life or sustained one-hour behavior.

### Observed calibration, 2026-10-19

The [calibration archive](../../evaluation/results/embedded/sampler-calibration-20261019/summary.json)
contains 18 successful trials and 109,626 successful measured requests. All 49
legacy artifacts remained unchanged, and 179 archived files passed verification.

| Condition versus sampling off | Paired mean latency change | 95% paired interval |
| --- | ---: | ---: |
| 250 ms sampling | +0.208% | -0.186% to +0.601% |
| 1 s sampling | -0.329% | -1.016% to +0.358% |

Use 250 ms provisionally for the next resource pilot because it provides more
samples while this narrow test observed a small mean change. Neither interval
establishes zero effect. This unpaced local-edge calibration does not establish
HTTP, paced-load, tail-latency or other-backend observer effects. Background load,
cooling and ambient conditions were not controlled as publication factors.

The [duration/baseline pilot](../../evaluation/results/embedded/duration-baseline-smoke-20261019/summary.json)
completed 1,800/1,800 requests with no missed releases and full sampled coverage
of all three active windows and their idle baselines. Near-zero positive and
negative power differences are retained; these short runs validate accounting,
not energy efficiency. This archive predates a trace-event correction: its
`measurement_end` event was emitted after the trailing idle window. Use the
correct `measurement_finished_ns` and `phases_ns` bounds in its worker summaries,
which are the bounds used by the reported metrics. New traces timestamp the event
at the actual measurement boundary. Historical files have not been edited.

Worker schema 3 also suppresses idle-subtracted estimates unless `baseline_s` was
explicitly positive. Schema 2 remains supported for archive reproduction but can
contain such estimates for incidental near-zero idle phases. Disregard those
estimates in the sampler-calibration archive, whose baseline setting was zero;
the reported paired latency comparisons are unaffected. The duration pilot used
explicit three-second baselines and is unaffected by this power-estimate correction.

## Trial configuration

Campaign JSON contains `schema_version: 1`, an optional `label`, and an ordered,
nonempty `trials` array. Optional `purpose: "sampler-calibration"` enables matched
block validation/reporting. Trial fields and defaults:

| Field | Values / default |
| --- | --- |
| `backend` | `edge` (default), `chemical`, `wetware`, `http` |
| `mode` | `orchestrated` (default), `direct` |
| `profile` | `standard` (three local backends, default), `edge-only` |
| `selection` | `directed` (default), `capability` (orchestrated only) |
| `iterations` | Positive integer, 100 measured attempts by default; null for duration mode |
| `duration_s` | Positive finite admission window; requires `iterations: null` |
| `warmup` | Nonnegative integer, 20 excluded attempts by default |
| `seed` | Nonnegative integer, 19 by default |
| `idle_s` | Ready-worker idle duration, 0 by default |
| `baseline_s` | Idle duration before and after the active window, after warmup; 0 by default |
| `rate_hz` | Positive finite rate, or null for sequential unpaced execution |
| `maintenance` | `reset_recalibrate` (default), `native` |

An optional per-trial `sampling` object overrides controller sampling defaults:
`{"tegrastats": false, "process": false, "interval_ms": 250}` disables periodic
sampling. Use both false for an off baseline; `--no-tegrastats` alone only changes
the default platform sampler. Effective settings are saved in each trial's
`sampling.json`. `pair_id` and `condition` label calibration trials and are not
passed into the measured worker. No arbitrary remote endpoints are accepted.

Chemical and wetware require `standard`. HTTP adds a localhost service and a
remote adapter to the chosen registry. Its current service has a fixed seed 19;
other HTTP seeds are rejected rather than falsely reported as applied. Campaigns
cannot supply arbitrary remote URLs. The controller starts and stops each service.

Each trial starts a fresh interpreter. Local twins receive the specified seed.
Warmup and explicit maintenance advance twin state and do not rewind its RNG.
Pair direct/orchestrated trials using the same seed, task, profile, maintenance,
warmup and environment; alternate AB/BA order across independent repetitions.
The direct path intentionally lacks orchestration checks and is not a semantically
equivalent safety baseline. Both paths construct the same registry for a profile.

An `edge-only` registry is not an import-pruned runtime: current package
initializers eagerly load other adapters. Preserve that behavior and report
`loaded_modules` alongside footprint results. Workers never import plotting,
`demos.common` or `evaluation.common`.

## Measurement boundaries

- Startup: parent process launch to the worker's ready event, after imports,
  registration, task serialization and descriptor discovery. HTTP service startup
  occurs before worker launch and is excluded; do not call this total HTTP startup.
- Invocation: wall-clock direct prepare/invoke/telemetry, or full `execute_task`,
  including native validation and recovery. Warmup is excluded.
- Maintenance: explicit reset and recalibration after the invocation timer.
- `execution_and_maintenance_ms`: these two phases combined; excludes row encoding,
  file output and pacing. Overall elapsed time and resource use include logging.
- Paced runs use monotonic planned releases, one active call and no catch-up
  bursts. Missed releases are counted. `iterations` counts actual attempts, not
  scheduled slots: overload extends a counted trial. Duration mode stops admitting
  work at its monotonic deadline and permits an in-flight call to finish; the
  controller timeout remains the hard process limit. Trailing missed releases are
  counted separately so completed attempts plus missed releases reconcile with
  planned releases. Report achieved throughput and deadline overrun as well as
  latency. Zero-completion trials fail visibly but retain their data.
- Dispatch lateness and release-to-return latency are retained. No tail-latency
  guarantee or enforced application deadline is implied.
- `backend_reported_latency_proxy_ms` is synthetic twin telemetry. Never subtract
  it from wall-clock RTT to claim transport overhead. No twin energy proxy is a
  measured energy value.
- RSS/peak RSS use Linux KiB; CPU time is cumulative process user + system seconds.
  Worker, controller and HTTP service samples are separate. CPU utilization must
  be derived from timestamped CPU-time differences, not from raw cumulative time.
  Phase summaries select samples inside each ready-idle, baseline and active
  window. CPU percentage uses the observed CPU-time delta divided by the elapsed
  sample span, normalized to one core (so it can exceed 100%). Fewer than two
  samples yields null CPU utilization. Summed RSS can double-count shared pages;
  PSS is not yet collected.
- `tegrastats` samples have monotonic receipt timestamps and retain their raw
  source line. They describe the platform, not isolated control-plane cost.
- Power is trapezoid-integrated only over observed adjacent sample intervals
  intersecting each phase window. No extrapolation is performed. Coverage is
  reported. When both idle phases and the active phase each have at least 90%
  time coverage, the summary also reports active mean platform power minus the
  mean of the two bracketing idle means. Negative estimates are retained, not
  clipped. This includes backend and monitoring costs and is not isolated
  control-plane power. Sampling coverage alone is not proof of measurement
  precision; gap detection and uncertainty across trials remain publication gates.
  Short smoke trials may have zero measurable coverage, not zero power.
- Summary p95/p99 use nearest-rank quantiles, withheld below 20/100 observations.
  These are within-trial statistics, not confidence intervals across trials.

The controller sets single-thread numeric-library variables and a fixed Python
hash seed for workers. HTTP services currently inherit the controller environment;
the relevant allowlisted settings are recorded separately. Align those settings
before confirmatory HTTP comparisons. GC, CPU governor, power mode, cooling and
other host settings are not changed automatically.

## Artifacts and provenance

`evaluation/results/embedded/<campaign>/` contains:

```text
campaign.json              requested trial sequence
run_settings.json          sampler, timeout and service thread settings
provenance.json            current environment, packages and source hashes
legacy_sha256.json         original top-level results and HDF5 hashes
summary.json               campaign outcomes, including failed trials
artifacts_sha256.json      checksums of the completed archive
trial-000/
  config.json              effective worker configuration
  sampling.json            effective per-trial sampler and timeout settings
  task.json                exact serialized TaskRequest
  events.jsonl             readiness and phase timestamps
  requests.csv             raw measured attempts and outcomes
  process_samples.jsonl    worker/controller/service resource samples
  tegrastats.jsonl         timestamped platform observations, when enabled
  stderr.txt               worker diagnostics
  worker_summary.json      workload counters, peak RSS, loaded modules
  summary.json             controller status and recomputed metrics
```

Never copy `.env`, tokens, or the complete process environment. Provenance captures
package names/versions, not authenticated package-index URLs. Git metadata can be
unavailable because of ownership configuration; source hashes remain authoritative
for the files actually measured. No global Git exceptions or ownership changes
are made. Paths in provenance may need anonymization in a separate release copy.

Current hardware settings cannot reconstruct the June setup. Original results are
hashed before and after a campaign; any change makes the campaign fail. No original
artifact is moved, chmod-ed, regenerated or overwritten. Keep `_temp/` for
throwaway probes only.

## Gates before publication measurements

1. Confirm cooling, ambient conditions, background workloads, exact hardware and
   evaluated source with the author. Freeze a protocol/config revision and a
   nonsecret environment snapshot. Baseline tests must pass.
2. Validate sampling observer effects with matched sensor-on/off trials at 250 ms
   and 1 s. Keep calibration data separate and choose a fixed cadence before the
  confirmatory campaign. The first local-edge calibration above is exploratory;
  assess applicability to other workload paths and rates before generalizing.
3. Phase-specific summaries, duration-bound pacing, bracketing idle windows and
  coverage fractions are implemented. Add gap-aware coverage checks, platform-idle
  baselines, paired direct/orchestrated analysis and documented thermal/disk guards
  before the long power or soak campaigns.
4. Add safely isolated CPU contention and delegated-cgroup memory-limit runs.
   Do not stress host memory or escalate privileges automatically. Lower-power
   configurations require explicit approval and restoration checks.
5. Run matching/fault conformance under normal and contended conditions. Existing
   tests are a regression check, not the complete five-scenario/seven-case study.
6. Execute ten paired local trials of 10,000 requests per backend; ten paired HTTP
   trials of 2,000; ten startup/footprint trials per profile; five paired 120 s
   power windows per rate/path; and three independent one-hour native-recovery
   runs. Freeze warmup, seeds, ordering and overload rules before starting.
7. Offline analysis must preserve failures, distinguish correct rejections, report
   trial-level uncertainty and reproduce all tables/figures from raw records.
   Review claims and publication overlap with the author before choosing a venue.

Do not optimize control-plane behavior during the baseline campaign. Any discovered
runtime defect needs an E2E reproduction and a recorded source/protocol revision.
Do not reinterpret a one-hour run as proof of long-term availability or leak freedom.