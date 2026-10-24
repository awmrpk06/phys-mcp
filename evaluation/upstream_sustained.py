"""Duration-based extension using only the pinned upstream task and runtime."""

from __future__ import annotations

import argparse
import csv
import json
import math
import resource
import subprocess
import sys
import time
from pathlib import Path


def scheduled_slots(duration_s: float, rate_hz: float, clock=time.monotonic_ns, wait=time.sleep):
    started = clock()
    period_ns = round(1e9 / rate_hz)
    planned = math.ceil(round(duration_s * 1e9) / period_ns)
    deadline = started + round(duration_s * 1e9)
    slot = 0
    while slot < planned:
        now = clock()
        if now >= deadline:
            break
        slot = max(slot, (now - started) // period_ns)
        release = started + slot * period_ns
        if release >= deadline:
            break
        if release > now:
            wait((release - now) / 1e9)
        now = clock()
        if now >= deadline:
            break
        slot = max(slot, (now - started) // period_ns)
        release = started + slot * period_ns
        yield slot, release, started, deadline, planned
        slot += 1
    remaining = (deadline - clock()) / 1e9
    if remaining > 0:
        wait(remaining)


def run(upstream: Path, output_dir: Path, duration_s: float, rate_hz: float) -> dict:
    if not math.isfinite(duration_s) or duration_s <= 0 or not math.isfinite(rate_hz) or not 0 < rate_hz <= 1000:
        raise ValueError("Duration must be positive and rate must be in (0, 1000] Hz")
    revision = subprocess.run(["git", "-C", str(upstream), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if revision != "36fbae002b0a9e066416ea9cafbb966a33fe53bb":
        raise ValueError("Unexpected upstream revision")
    sys.path.insert(0, str(upstream.resolve()))
    from demos.common import build_default_orchestrator, make_edge_task

    orchestrator = build_default_orchestrator()
    task = make_edge_task(task_id="embedded-sustained-original-edge")
    adapter = orchestrator.registry.get_adapter("edge-backend")
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "task.json").write_text(task.model_dump_json(indent=2), encoding="utf-8")
    ready_ns = time.monotonic_ns()
    time.sleep(3)
    completed = 0
    successes = 0
    maintenance_failures = 0
    unexpected_backends = 0
    replays = 0
    total_latency = 0.0
    planned = math.ceil(duration_s * rate_hz)
    started_ns = time.monotonic_ns()
    deadline_ns = started_ns + round(duration_s * 1e9)
    with (output_dir / "requests.csv").open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["slot", "release_ns", "started_ns", "finished_ns", "invocation_ms", "maintenance_ms", "success", "selected_backend", "error_code", "failure_reason", "maintenance_ok", "idempotent_replay"])
        writer.writeheader()
        for slot, release_ns, started_ns, deadline_ns, planned in scheduled_slots(duration_s, rate_hz):
            request_started = time.monotonic_ns()
            result = orchestrator.execute_task(task)
            request_finished = time.monotonic_ns()
            reset_ok = adapter.reset()
            recalibrate_ok = adapter.recalibrate()
            maintenance_finished = time.monotonic_ns()
            latency = (request_finished - request_started) / 1e6
            writer.writerow({
                "slot": slot, "release_ns": release_ns, "started_ns": request_started,
                "finished_ns": request_finished, "invocation_ms": latency,
                "maintenance_ms": (maintenance_finished - request_finished) / 1e6,
                "success": result.success, "selected_backend": result.decision.selected_backend_id,
                "error_code": str(result.error.code) if result.error else None,
                "failure_reason": result.failure_reason, "maintenance_ok": reset_ok and recalibrate_ok,
                "idempotent_replay": result.idempotent_replay,
            })
            completed += 1
            successes += int(result.success)
            maintenance_failures += int(not reset_ok or not recalibrate_ok)
            unexpected_backends += int(result.decision.selected_backend_id != "edge-backend")
            replays += int(result.idempotent_replay)
            total_latency += latency
        handle.flush()
    finished_ns = time.monotonic_ns()
    summary = {
        "upstream_commit": revision, "duration_s": duration_s, "rate_hz": rate_hz,
        "started_ns": started_ns, "finished_ns": finished_ns, "ready_ns": ready_ns,
        "admission_deadline_ns": deadline_ns, "planned_releases": planned,
        "completed_requests": completed, "successful_requests": successes,
        "missed_releases": planned - completed, "maintenance_failures": maintenance_failures,
        "unexpected_backends": unexpected_backends, "idempotent_replays": replays,
        "latency_mean_ms": total_latency / completed if completed else None,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "scope": "Sustained extension, not the original 25-run overhead protocol. Same original task factory and explicit maintenance; no injected faults or hosted model.",
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-s", type=float, default=300)
    parser.add_argument("--rate-hz", type=float, default=10)
    args = parser.parse_args()
    summary = run(args.upstream, args.output_dir, args.duration_s, args.rate_hz)
    print(json.dumps(summary, indent=2))
    if not summary["completed_requests"] or summary["successful_requests"] != summary["completed_requests"] or summary["maintenance_failures"] or summary["unexpected_backends"] or summary["idempotent_replays"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()