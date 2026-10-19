"""Isolated embedded workloads; no plotting or evaluation-helper imports."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import resource
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrialConfig:
    mode: str = "orchestrated"
    profile: str = "standard"
    backend: str = "edge"
    iterations: int | None = 100
    duration_s: float | None = None
    warmup: int = 20
    seed: int = 19
    rate_hz: float | None = None
    idle_s: float = 0.0
    baseline_s: float = 0.0
    maintenance: str = "reset_recalibrate"
    selection: str = "directed"
    base_url: str | None = None

    def __post_init__(self) -> None:
        for name, choices in {
            "mode": {"direct", "orchestrated"},
            "profile": {"edge-only", "standard"},
            "backend": {"edge", "chemical", "wetware", "http"},
            "maintenance": {"reset_recalibrate", "native"},
            "selection": {"directed", "capability"},
        }.items():
            if getattr(self, name) not in choices:
                raise ValueError(f"Invalid {name}: {getattr(self, name)!r}")
        for name, minimum in (("iterations", 1), ("warmup", 0), ("seed", 0)):
            value = getattr(self, name)
            if name == "iterations" and value is None and self.duration_s is not None:
                continue
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if self.duration_s is not None and self.iterations is not None:
            raise ValueError("Set iterations to null when using duration_s")
        for name in ("idle_s", "baseline_s", "rate_hz", "duration_s"):
            value = getattr(self, name)
            if value is None and name in {"rate_hz", "duration_s"}:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be numeric")
            if not math.isfinite(value) or value < 0 or (name in {"rate_hz", "duration_s"} and value == 0):
                raise ValueError(f"Invalid {name}")
        if self.duration_s is not None and self.duration_s < 1e-9:
            raise ValueError("duration_s is below the clock's nanosecond resolution")
        if self.backend == "http" and (not self.base_url or self.seed != 19):
            raise ValueError("HTTP requires base_url and the service's fixed seed 19")
        if self.profile == "edge-only" and self.backend in {"chemical", "wetware"}:
            raise ValueError("Chemical and wetware workloads require the standard profile")
        if self.rate_hz is not None and self.rate_hz > 1e9:
            raise ValueError("rate_hz exceeds the clock's nanosecond resolution")
        if self.mode == "direct" and self.selection != "directed":
            raise ValueError("Capability selection requires orchestration")


def write_json(path: Path, payload: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, "monotonic_ns": time.monotonic_ns(), **fields}), flush=True)


def build_workload(config: TrialConfig):
    from adapters.edge_adapter import EdgeAdapter
    from core.orchestrator import PhysMCPOrchestrator
    from core.task_model import TaskRequest
    from descriptors.capability_schema import Locality, SignalModality
    from twins.edge_twin import EdgeTwin

    orchestrator = PhysMCPOrchestrator()
    adapter = EdgeAdapter(twin=EdgeTwin(seed=config.seed))
    if config.profile == "standard":
        from adapters.chemical_adapter import ChemicalAdapter
        from adapters.wetware_adapter import WetwareAdapter
        from twins.chemical_twin import ChemicalTwin
        from twins.wetware_twin import WetwareTwin

        orchestrator.register_adapter(ChemicalAdapter(twin=ChemicalTwin(seed=config.seed)))
        orchestrator.register_adapter(WetwareAdapter(twin=WetwareTwin(seed=config.seed)))
    orchestrator.register_adapter(adapter)
    if config.backend == "http":
        from adapters.remote_edge_adapter import RemoteEdgeAdapter

        adapter = RemoteEdgeAdapter(config.base_url)
        orchestrator.register_adapter(adapter)
    elif config.backend != "edge":
        adapter = orchestrator.registry.get_adapter(f"{config.backend}-backend")
    task_fields = {
        "task_kind": "classification",
        "required_input_modalities": [SignalModality.DIGITAL_VECTOR],
        "preferred_output": "score",
        "latency_budget_ms": 20.0,
        "preferred_locality": Locality.FOG if config.backend == "http" else Locality.EDGE,
        "metadata": {"input_vector": [0.1, 0.3, 0.5, 0.7]},
    }
    if config.backend == "chemical":
        task_fields.update(
            task_kind="sensing", required_input_modalities=[SignalModality.CONCENTRATION],
            preferred_output="state_estimate", latency_budget_ms=15000.0, preferred_locality=None,
            metadata={"input_level": 1.4},
        )
    elif config.backend == "wetware":
        task_fields.update(
            task_kind="temporal_inference", required_input_modalities=[SignalModality.SPIKES],
            preferred_output="telemetry_aware_result", latency_budget_ms=120.0, preferred_locality=None,
            continuous_monitoring_required=True, human_supervision_available=True,
            required_telemetry_fields=["health_status", "drift_score"],
            metadata={"stimulation_strength": 0.65, "observation_window_ms": 140.0},
        )
    task = TaskRequest(
        task_id="embedded-trial",
        summary="Embedded control-plane measurement.",
        min_confidence=0.2,
        direct_backend_id=adapter.backend_id() if config.selection == "directed" else None,
        allow_fallback=False,
        **task_fields,
    )
    return orchestrator, adapter, task


def execute_once(config, orchestrator, adapter, task, clock=time.monotonic_ns) -> dict:
    success = False
    selected_backend = None
    invocation = None
    telemetry = {}
    fallback = False
    validation_failures = []
    recovery_actions = []
    error = None
    result = None
    started = clock()
    try:
        if config.mode == "orchestrated":
            result = orchestrator.execute_task(task)
        else:
            preparation = adapter.prepare(task)
            if preparation.prepared:
                invocation = adapter.invoke(task)
                telemetry = adapter.collect_telemetry()
                selected_backend = adapter.backend_id()
                success = True
            else:
                error = preparation.details
    except Exception as exc:
        logger.exception("Embedded invocation failed")
        error = f"{type(exc).__name__}: {exc}"
    finished = clock()
    maintenance_error = None
    if config.maintenance == "reset_recalibrate":
        try:
            reset_ok = adapter.reset()
            recalibrate_ok = adapter.recalibrate()
            if not reset_ok or not recalibrate_ok:
                maintenance_error = "Reset or recalibration returned false"
        except Exception as exc:
            logger.exception("Embedded maintenance failed")
            maintenance_error = f"{type(exc).__name__}: {exc}"
    maintained = clock()
    if result is not None:
        success = result.success
        selected_backend = result.decision.selected_backend_id
        invocation = result.invocation
        telemetry = result.telemetry_after
        fallback = result.decision.used_fallback
        validation_failures = result.validation_failures
        recovery_actions = result.recovery_actions
        error = result.failure_reason
    return {
        "started_ns": started,
        "finished_ns": finished,
        "invocation_ms": (finished - started) / 1e6,
        "maintenance_ms": (maintained - finished) / 1e6,
        "execution_and_maintenance_ms": (maintained - started) / 1e6,
        "success": success,
        "backend_matches_target": selected_backend == adapter.backend_id(),
        "selected_backend": selected_backend,
        "used_fallback": fallback,
        "error": error,
        "maintenance_error": maintenance_error,
        "validation_failures": json.dumps(validation_failures),
        "recovery_actions": json.dumps(recovery_actions),
        "telemetry": json.dumps(telemetry),
        "backend_reported_latency_proxy_ms": invocation.execution_latency_ms if invocation else None,
        "confidence": invocation.confidence if invocation else None,
    }


def scheduled_release(next_release: int, now: int, period_ns: int) -> tuple[int, int]:
    skipped = max(0, (now - next_release) // period_ns)
    return next_release + skipped * period_ns, skipped


def measurement_schedule(config: TrialConfig, started_ns: int, clock=time.monotonic_ns, wait=time.sleep):
    period_ns = round(1e9 / config.rate_hz) if config.rate_hz else None
    deadline = started_ns + round(config.duration_s * 1e9) if config.duration_s else None
    next_release = started_ns
    request_index = 0
    while config.iterations is None or request_index < config.iterations:
        now = clock()
        if deadline is not None and now >= deadline:
            break
        if period_ns and deadline is not None and next_release >= deadline:
            wait(max(0, (deadline - now) / 1e9))
            continue
        skipped_before = 0
        if period_ns:
            next_release, skipped_before = scheduled_release(next_release, now, period_ns)
            delay = (next_release - clock()) / 1e9
            if delay > 0:
                wait(delay)
            now = clock()
            if deadline is not None and now >= deadline:
                break
            next_release, additional_skips = scheduled_release(next_release, now, period_ns)
            skipped_before += additional_skips
        release = next_release if period_ns else now
        yield request_index, release, skipped_before
        request_index += 1
        if period_ns:
            next_release += period_ns


def run(config: TrialConfig, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "requests.csv").open("x", newline="", encoding="utf-8") as handle:
        orchestrator, adapter, task = build_workload(config)
        write_json(output_dir / "task.json", task.model_dump(mode="json"))
        emit("ready", pid=os.getpid(), registered_backends=[item["backend_id"] for item in orchestrator.discover_backends()])
        phases = {}
        idle_started = time.monotonic_ns()
        time.sleep(config.idle_s)
        phases["ready_idle"] = [idle_started, time.monotonic_ns()]
        emit("warmup_start")
        for _ in range(config.warmup):
            record = execute_once(config, orchestrator, adapter, task)
            if not record["success"] or not record["backend_matches_target"] or record["maintenance_error"]:
                raise RuntimeError(f"Warmup failed: {record}")
        baseline_started = time.monotonic_ns()
        time.sleep(config.baseline_s)
        phases["idle_before"] = [baseline_started, time.monotonic_ns()]
        emit("measurement_start")
        started = time.monotonic_ns()
        period_ns = round(1e9 / config.rate_hz) if config.rate_hz else None
        writer = None
        completed = 0
        successful = 0
        maintenance_failures = 0
        unexpected_backends = 0
        skipped = 0
        for request_index, release, skipped_before in measurement_schedule(config, started):
            record = execute_once(config, orchestrator, adapter, task)
            record.update(
                request_index=request_index,
                release_ns=release,
                skipped_releases_before=skipped_before,
                dispatch_lateness_ms=(record["started_ns"] - release) / 1e6,
                release_to_return_ms=(record["finished_ns"] - release) / 1e6,
            )
            if writer is None:
                writer = csv.DictWriter(handle, fieldnames=list(record))
                writer.writeheader()
            writer.writerow(record)
            completed += 1
            successful += int(record["success"])
            unexpected_backends += int(not record["backend_matches_target"])
            maintenance_failures += int(record["maintenance_error"] is not None)
            skipped += skipped_before
        handle.flush()
        finished = time.monotonic_ns()
        phases["measurement"] = [started, finished]
        emit("measurement_end", monotonic_ns=finished, completed_requests=completed)
        baseline_started = time.monotonic_ns()
        time.sleep(config.baseline_s)
        phases["idle_after"] = [baseline_started, time.monotonic_ns()]
    planned_releases = None
    terminal_skipped = 0
    if config.duration_s is not None and period_ns is not None:
        planned_releases = math.ceil(round(config.duration_s * 1e9) / period_ns)
        terminal_skipped = max(0, planned_releases - completed - skipped)
    summary = {
        "schema_version": 3,
        "config": asdict(config),
        "phases_ns": phases,
        "pid": os.getpid(),
        "measurement_started_ns": started,
        "measurement_finished_ns": finished,
        "measurement_elapsed_s": (finished - started) / 1e9,
        "completed_requests": completed,
        "successful_requests": successful,
        "maintenance_failures": maintenance_failures,
        "unexpected_backends": unexpected_backends,
        "skipped_releases": skipped + terminal_skipped,
        "terminal_skipped_releases": terminal_skipped,
        "planned_releases": planned_releases,
        "admission_deadline_ns": started + round(config.duration_s * 1e9) if config.duration_s else None,
        "achieved_requests_per_s": completed / ((finished - started) / 1e9),
        "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "loaded_modules": sorted(sys.modules),
        "evidence": "software twin or same-host service; no physical PNN execution",
    }
    write_json(output_dir / "worker_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    config = TrialConfig(**json.loads(args.config.read_text(encoding="utf-8")))
    summary = run(config, args.output_dir)
    if not summary["completed_requests"] or summary["successful_requests"] != summary["completed_requests"] or summary["maintenance_failures"] or summary["unexpected_backends"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()