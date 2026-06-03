# phys-MCP Embedded Edge Summary

Generated on Jetson Orin / aarch64, Linux `5.15.185-tegra`, Python `3.10.12`.

## Executive Summary

The current phys-MCP prototype runs successfully on the Orin as a lightweight embedded control-plane workload. The strongest embedded evidence is the local edge control path profiled with `tegrastats`: 5000/5000 successful orchestrated runs, mean latency below 0.5 ms, p95 latency below 0.5 ms, and process max RSS growth of only 28 KB during the latest measurement window.

This validates the current platform as a low-latency CPU-side control plane for edge orchestration. It does not yet prove GPU/CUDA acceleration, because the measured phys-MCP workloads are Python control-plane and simulator workloads; `tegrastats` reported `GR3D_FREQ 0%`.

Visual report:

- [Embedded edge visual PDF](embedded_edge_visual_report.pdf)

## Key Embedded Resource Results

### Local Edge Control Workload

Command:

```bash
python -m evaluation.evaluate_tegra_resources --workload edge_control --iterations 5000 --interval-ms 250
```

Result summary:

- Successful runs: `5000/5000`
- Mean latency: `0.457 ms`
- Median latency: `0.455 ms`
- p95 latency: `0.477 ms`
- Max latency: `1.631 ms`
- Process max RSS delta: `28 KB`
- System RAM max during run: `3114 / 7607 MB`
- Swap max during run: `6 MB`
- Mean `VDD_IN`: `5922.9 mW`
- Max `VDD_IN`: `6002.0 mW`
- Max temperature: `52.75 C`
- GPU/GR3D: `0%`

Artifacts:

- [JSON](tegra_resources_edge_control_20260603T080412Z.json)
- [CSV](tegra_resources_edge_control_20260603T080412Z.csv)
- [Raw tegrastats log](tegra_resources_edge_control_20260603T080412Z.log)
- [Latency bar chart](tegra_resources_edge_control_20260603T080412Z_latency_bar_chart.png)
- [Resource bar chart](tegra_resources_edge_control_20260603T080412Z_resource_bar_chart.png)

### Externalized HTTP Edge Backend

Command:

```bash
python -m evaluation.evaluate_tegra_resources --workload externalized_backend --iterations 15 --interval-ms 250
```

Result summary:

- Backend: `remote-edge-backend`
- Endpoint kind: `http_remote`
- Mean round-trip latency: `6.351 ms`
- Mean backend latency: `3.947 ms`
- Transport/control boundary overhead: `2.405 ms`
- Process max RSS delta: `0 KB`
- System RAM max during run: `3143 / 7607 MB`
- Mean `VDD_IN`: `5889.8 mW`
- Max `VDD_IN`: `6120.0 mW`
- Max temperature: `52.781 C`
- GPU/GR3D: `0%`

Artifacts:

- [JSON](tegra_resources_externalized_backend_20260603T080526Z.json)
- [CSV](tegra_resources_externalized_backend_20260603T080526Z.csv)
- [Raw tegrastats log](tegra_resources_externalized_backend_20260603T080526Z.log)
- [Latency bar chart](tegra_resources_externalized_backend_20260603T080526Z_latency_bar_chart.png)
- [Resource bar chart](tegra_resources_externalized_backend_20260603T080526Z_resource_bar_chart.png)

## Comparison With Original Committed Results

The original committed results appear to come from a non-Orin development
machine. The Orin reproduces the same functional outcomes, with small
differences in timing.

### Local Control-Plane Overhead

| Backend | Original orchestrated mean | Orin orchestrated mean | Change | Original added overhead | Orin added overhead | Change |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Chemical | `2.488 ms` | `3.208 ms` | slower by `0.720 ms` | `0.361 ms` | `0.268 ms` | lower by `0.092 ms` |
| Wetware | `0.267 ms` | `0.460 ms` | slower by `0.192 ms` | `0.194 ms` | `0.322 ms` | higher by `0.127 ms` |
| Edge | `0.321 ms` | `0.463 ms` | slower by `0.143 ms` | `0.189 ms` | `0.301 ms` | higher by `0.112 ms` |

Interpretation: local Orin control-plane execution is somewhat slower than the
original committed desktop-style baseline for the fastest synthetic paths, but
the absolute overhead remains sub-millisecond. The edge backend remains below
`0.5 ms` mean orchestrated latency.

### Externalized Backend

| Metric | Original | Orin | Change |
| --- | ---: | ---: | ---: |
| Mean backend latency | `3.947 ms` | `3.947 ms` | unchanged |
| Mean round-trip latency | `8.961 ms` | `6.351 ms` | faster by `2.610 ms` |
| Transport overhead | `5.015 ms` | `2.405 ms` | lower by `2.610 ms` |

Interpretation: the Orin run is slightly better than the original committed
result for the local HTTP service boundary.

### Cortical Labs Runtime Path

| Metric | Original committed run | Orin run |
| --- | ---: | ---: |
| Successful runs | `3/3` | `3/3` |
| Backend latency range | `6941-7730 ms` | `260-305 ms` |
| Observation latency range | `16-50 ms` | `239-284 ms` |
| Channel count | `64` | `64` |
| FPS | `25000` | `25000` |

Interpretation: the Orin run completes the same Cortical Labs simulator path
successfully and has much lower total backend/session latency. The observation
latency is higher in the Orin run, likely because the local CL SDK simulator
timing behavior differs after Python/SDK compatibility patches and mock replay
configuration. This should be treated as a simulator/runtime comparison, not a
real wetware performance claim.

### Functional Results

These results are unchanged in the important sense:

- Matching still reaches `100%` on the curated phys-MCP matcher cases.
- Baseline comparison still shows the full matcher at `7/7`, ahead of simpler
  modality-only and latency-only selectors.
- Failure campaign still reaches `5/5` expected behaviors.
- Portability still reports shared descriptor and invocation ratios of `1.0`.

## Existing Evaluation Evidence

### Control-Plane Overhead

The overhead evaluation compares direct adapter calls with orchestrated phys-MCP calls.

Current results:

- Chemical backend added overhead: `0.268 ms`
- Wetware backend added overhead: `0.322 ms`
- Edge backend added overhead: `0.301 ms`
- Edge orchestrated mean latency: `0.463 ms`

Artifacts:

- [JSON](overhead_results.json)
- [CSV](overhead_results.csv)
- [Chart](overhead_bar_chart.png)

### Externalized Backend

This isolates the HTTP service boundary without the tegrastats wrapper.

Current results:

- Mean round-trip latency: `6.351 ms`
- Mean backend latency: `3.947 ms`
- Transport overhead: `2.405 ms`

Artifacts:

- [JSON](externalized_backend_results.json)
- [CSV](externalized_backend_results.csv)

### Matching Correctness

The matcher chooses the expected backend across the curated task suite.

Current results:

- Full phys-MCP matcher: `7/7`
- Modality-only baseline: `3/7`
- Latency-only baseline: `3/7`
- Random admissible baseline: `4/7`

Artifacts:

- [Matching JSON](matching_results.json)
- [Matching CSV](matching_results.csv)
- [Baseline JSON](matching_baselines_results.json)
- [Baseline CSV](matching_baselines_results.csv)
- [Matching chart](matching_accuracy_bar_chart.png)

### Robustness / Fault Campaign

The fault campaign verifies fallback, rejection, and policy behavior under runtime faults.

Current results:

- Expected behavior match rate: `5/5`
- Drifted local edge backend avoided
- Local preparation failure recovered by fallback
- Wetware request without supervision rejected
- Stale chemical twin rejected
- Missing required telemetry recovered by fallback

Artifacts:

- [JSON](failure_campaign_results.json)
- [CSV](failure_campaign_results.csv)

### Portability

The portability evaluation shows a shared descriptor and invocation contract across local, remote, and API-backed paths.

Current results:

- Descriptor shared-key ratio: `1.0`
- Invocation shared-key ratio: `1.0`
- Successful executable backend runs: `4/4`
- Registered descriptor backends include chemical, wetware, edge, remote edge, and Cortical Labs.

Artifacts:

- [Portability JSON](portability_results.json)
- [Portability runs CSV](portability_runs.csv)
- [Existing API adapter CSV](existing_api_adapter_results.csv)
- [Portability chart](portability_metadata_bar_chart.png)

### Cortical Labs Runtime Path

The Cortical Labs simulator/API-backed path was executed end to end through phys-MCP.

Current results:

- Successful runs: `3/3`
- Backend latencies: approximately `260-305 ms`
- Observation latencies: approximately `239-284 ms`
- Channel count: `64`
- FPS: `25000`

Artifacts:

- [JSON](cortical_runtime_results_20260603T072212Z.json)
- [CSV](cortical_runtime_results_20260603T072212Z.csv)

## Verification

The repository test suite passed after the embedded evaluator was added:

```text
pytest -q
6 passed
```

## Interpretation

The current evidence supports this claim:

> phys-MCP can run on a Jetson Orin as a lightweight embedded control plane with sub-millisecond local edge orchestration latency and modest memory growth, while preserving runtime-aware matching, telemetry, fallback, and API-backed integration paths.

The current evidence does not yet support this stronger claim:

> phys-MCP uses CUDA/GPU acceleration for its core workload.

To support that stronger claim, the next backend should execute a real GPU workload, such as a TensorRT, PyTorch CUDA, CUDA kernel, or Ollama-backed inference adapter, and then repeat the same `tegrastats` evaluation with nonzero GR3D/GPU activity.
