"""Measure Jetson resource usage with tegrastats while running phys-MCP workloads."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import logging
import math
import os
import platform
import re
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from dataclasses import asdict, replace
from datetime import datetime, timezone
from itertools import pairwise, permutations
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any

from typing_extensions import Self

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


RAM_RE = re.compile(r"RAM\s+(?P<used>\d+)/(?P<total>\d+)MB")
SWAP_RE = re.compile(r"SWAP\s+(?P<used>\d+)/(?P<total>\d+)MB")
CPU_RE = re.compile(r"CPU\s+\[(?P<values>[^\]]+)\]")
GPU_RE = re.compile(r"GR3D_FREQ\s+(?P<percent>\d+)%")
TEMP_RE = re.compile(r"(?P<name>[A-Za-z0-9_]+)@(?P<temp>[0-9.]+)C")
POWER_RE = re.compile(r"(?P<rail>VDD_[A-Z0-9_]+)\s+(?P<current>\d+)mW/(?P<avg>\d+)mW")


def parse_tegrastats_line(line: str) -> dict[str, Any]:
    """Parse the parts of a tegrastats line that matter for edge reporting."""
    sample: dict[str, Any] = {"raw": line.strip()}

    ram = RAM_RE.search(line)
    if ram:
        sample["ram_used_mb"] = int(ram.group("used"))
        sample["ram_total_mb"] = int(ram.group("total"))

    swap = SWAP_RE.search(line)
    if swap:
        sample["swap_used_mb"] = int(swap.group("used"))
        sample["swap_total_mb"] = int(swap.group("total"))

    cpu = CPU_RE.search(line)
    if cpu:
        cpu_percentages = []
        for entry in cpu.group("values").split(","):
            percent_match = re.search(r"(\d+)%@", entry)
            if percent_match:
                cpu_percentages.append(int(percent_match.group(1)))
        if cpu_percentages:
            sample["cpu_mean_percent"] = mean(cpu_percentages)
            sample["cpu_max_percent"] = max(cpu_percentages)

    gpu = GPU_RE.search(line)
    if gpu:
        sample["gr3d_percent"] = int(gpu.group("percent"))

    temps = {match.group("name"): float(match.group("temp")) for match in TEMP_RE.finditer(line)}
    if temps:
        sample["temperatures_c"] = temps
        sample["max_temperature_c"] = max(temps.values())

    rails: dict[str, dict[str, int]] = {}
    for match in POWER_RE.finditer(line):
        rails[match.group("rail")] = {
            "current_mw": int(match.group("current")),
            "avg_mw": int(match.group("avg")),
        }
    if rails:
        sample["power_rails"] = rails
        if "VDD_IN" in rails:
            sample["vdd_in_mw"] = rails["VDD_IN"]["current_mw"]

    return sample


class TegraStatsSampler:
    """Collect tegrastats output in a background thread."""

    def __init__(self, interval_ms: int, output_path: Path | None = None) -> None:
        if interval_ms <= 0:
            raise ValueError("interval_ms must be positive")
        tegrastats = shutil.which("tegrastats")
        if tegrastats is None:
            raise RuntimeError("tegrastats was not found on PATH.")
        self._cmd = [tegrastats, "--interval", str(interval_ms)]
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self.lines: list[str] = []
        self._output_path = output_path
        self._output = None

    def __enter__(self) -> Self:
        if self._output_path is not None:
            self._output = self._output_path.open("x", encoding="utf-8")
        try:
            self._start()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def _start(self) -> None:
        self._process = subprocess.Popen(
            self._cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        self._thread = threading.Thread(target=self._read_lines, daemon=True)
        self._thread.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._process is not None and self._process.poll() is None:
            try:
                os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
                self._process.wait(timeout=3)
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._output is not None:
            self._output.close()

    def _read_lines(self) -> None:
        if self._process is None or self._process.stdout is None:
            return
        for line in self._process.stdout:
            clean = line.strip()
            if clean:
                if self._output is None:
                    self.lines.append(clean)
                else:
                    sample = parse_tegrastats_line(clean)
                    sample["monotonic_ns"] = time.monotonic_ns()
                    self._output.write(json.dumps(sample) + "\n")


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[index]


def run_edge_control(iterations: int) -> dict[str, Any]:
    """Run the low-latency local edge path repeatedly."""
    from demos.common import build_default_orchestrator, make_edge_task

    orchestrator = build_default_orchestrator()
    task = make_edge_task(task_id="tegra-edge-control")
    adapter = orchestrator.registry.get_adapter("edge-backend")

    latencies_ms: list[float] = []
    successful_runs = 0
    for _ in range(iterations):
        start = time.perf_counter()
        result = orchestrator.execute_task(task)
        latencies_ms.append((time.perf_counter() - start) * 1000.0)
        if result.success:
            successful_runs += 1
        adapter.reset()
        adapter.recalibrate()

    return {
        "iterations": iterations,
        "successful_runs": successful_runs,
        "latency_mean_ms": mean(latencies_ms),
        "latency_median_ms": median(latencies_ms),
        "latency_p95_ms": percentile(latencies_ms, 95),
        "latency_max_ms": max(latencies_ms),
    }


def run_workload(name: str, iterations: int) -> dict[str, Any]:
    if name == "edge_control":
        return run_edge_control(iterations)
    if name == "overhead":
        from evaluation.evaluate_overhead import evaluate

        return evaluate()
    if name == "externalized_backend":
        from evaluation.evaluate_externalized_backend import evaluate

        return evaluate()
    raise ValueError(f"Unknown workload: {name}")


def summarize_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    def values_for(key: str) -> list[float]:
        return [float(sample[key]) for sample in samples if key in sample]

    ram_values = values_for("ram_used_mb")
    swap_values = values_for("swap_used_mb")
    cpu_mean_values = values_for("cpu_mean_percent")
    cpu_max_values = values_for("cpu_max_percent")
    gr3d_values = values_for("gr3d_percent")
    temp_values = values_for("max_temperature_c")
    vdd_in_values = values_for("vdd_in_mw")

    summary: dict[str, Any] = {"sample_count": len(samples)}
    if ram_values:
        summary["ram_used_mean_mb"] = mean(ram_values)
        summary["ram_used_max_mb"] = max(ram_values)
        summary["ram_total_mb"] = samples[-1].get("ram_total_mb")
    if swap_values:
        summary["swap_used_max_mb"] = max(swap_values)
    if cpu_mean_values:
        summary["cpu_mean_percent_avg"] = mean(cpu_mean_values)
    if cpu_max_values:
        summary["cpu_max_percent_peak"] = max(cpu_max_values)
    if gr3d_values:
        summary["gr3d_percent_avg"] = mean(gr3d_values)
        summary["gr3d_percent_peak"] = max(gr3d_values)
    if temp_values:
        summary["temperature_max_c"] = max(temp_values)
    if vdd_in_values:
        summary["vdd_in_mean_mw"] = mean(vdd_in_values)
        summary["vdd_in_max_mw"] = max(vdd_in_values)
        summary["vdd_in_min_mw"] = min(vdd_in_values)
    return summary


def save_latency_chart(payload: dict[str, Any], output_path: Path) -> bool:
    import matplotlib.pyplot as plt

    workload_result = payload["workload_result"]
    labels: list[str]
    values: list[float]

    if "latency_mean_ms" in workload_result:
        labels = ["mean", "median", "p95", "max"]
        values = [
            float(workload_result["latency_mean_ms"]),
            float(workload_result["latency_median_ms"]),
            float(workload_result["latency_p95_ms"]),
            float(workload_result["latency_max_ms"]),
        ]
        title = f"{payload['workload']} latency"
        ylabel = "Latency (ms)"
    elif workload_result.get("results"):
        first_row = workload_result["results"][0]
        if "mean_roundtrip_ms" not in first_row:
            return False
        labels = ["backend", "roundtrip", "transport"]
        values = [
            float(first_row["mean_backend_latency_ms"]),
            float(first_row["mean_roundtrip_ms"]),
            float(first_row["transport_overhead_ms"]),
        ]
        title = f"{payload['workload']} round-trip latency"
        ylabel = "Latency (ms)"
    else:
        return False

    plt.figure(figsize=(7.5, 4.5))
    plt.bar(labels, values, color=["#3867d6", "#20bf6b", "#fa8231", "#eb3b5a"][: len(values)])
    plt.title(title)
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.savefig(output_path, dpi=200)
    plt.close()
    return True


def save_resource_chart(payload: dict[str, Any], output_path: Path) -> bool:
    import matplotlib.pyplot as plt

    summary = payload["tegrastats_summary"]
    if not summary or summary.get("sample_count", 0) == 0:
        return False

    fig, axes = plt.subplots(2, 2, figsize=(10, 7))
    fig.suptitle(f"{payload['workload']} Jetson resource profile")

    axes[0][0].bar(
        ["mean", "max"],
        [
            float(summary.get("vdd_in_mean_mw", 0.0)) / 1000.0,
            float(summary.get("vdd_in_max_mw", 0.0)) / 1000.0,
        ],
        color=["#20bf6b", "#eb3b5a"],
    )
    axes[0][0].set_title("VDD_IN power")
    axes[0][0].set_ylabel("Power (W)")

    axes[0][1].bar(
        ["mean used", "max used", "total"],
        [
            float(summary.get("ram_used_mean_mb", 0.0)) / 1024.0,
            float(summary.get("ram_used_max_mb", 0.0)) / 1024.0,
            float(summary.get("ram_total_mb", 0.0)) / 1024.0,
        ],
        color=["#45aaf2", "#3867d6", "#a5b1c2"],
    )
    axes[0][1].set_title("RAM")
    axes[0][1].set_ylabel("Memory (GB)")

    axes[1][0].bar(
        ["CPU avg", "CPU peak", "GPU avg", "GPU peak"],
        [
            float(summary.get("cpu_mean_percent_avg", 0.0)),
            float(summary.get("cpu_max_percent_peak", 0.0)),
            float(summary.get("gr3d_percent_avg", 0.0)),
            float(summary.get("gr3d_percent_peak", 0.0)),
        ],
        color=["#f7b731", "#fa8231", "#8854d0", "#4b6584"],
    )
    axes[1][0].set_title("Utilization")
    axes[1][0].set_ylabel("Percent")
    axes[1][0].set_ylim(0, 105)

    axes[1][1].bar(
        ["max"],
        [float(summary.get("temperature_max_c", 0.0))],
        color=["#eb3b5a"],
    )
    axes[1][1].set_title("Thermal")
    axes[1][1].set_ylabel("Temperature (C)")

    for axis in axes.flat:
        axis.tick_params(axis="x", labelrotation=15)

    fig.tight_layout()
    fig.savefig(output_path, dpi=200)
    plt.close(fig)
    return True


def write_outputs(payload: dict[str, Any], samples: list[dict[str, Any]], workload: str) -> tuple[Path, Path, Path, dict[str, Path]]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = RESULTS_DIR / f"tegra_resources_{workload}_{timestamp}.json"
    csv_path = RESULTS_DIR / f"tegra_resources_{workload}_{timestamp}.csv"
    log_path = RESULTS_DIR / f"tegra_resources_{workload}_{timestamp}.log"
    latency_chart_path = RESULTS_DIR / f"tegra_resources_{workload}_{timestamp}_latency_bar_chart.png"
    resource_chart_path = RESULTS_DIR / f"tegra_resources_{workload}_{timestamp}_resource_bar_chart.png"

    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log_path.write_text("\n".join(sample["raw"] for sample in samples) + "\n", encoding="utf-8")

    fieldnames = [
        "ram_used_mb",
        "ram_total_mb",
        "swap_used_mb",
        "swap_total_mb",
        "cpu_mean_percent",
        "cpu_max_percent",
        "gr3d_percent",
        "max_temperature_c",
        "vdd_in_mw",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for sample in samples:
            writer.writerow({field: sample.get(field) for field in fieldnames})

    chart_paths: dict[str, Path] = {}
    if save_latency_chart(payload, latency_chart_path):
        chart_paths["latency_bar_chart"] = latency_chart_path
    if save_resource_chart(payload, resource_chart_path):
        chart_paths["resource_bar_chart"] = resource_chart_path

    return json_path, csv_path, log_path, chart_paths


def evaluate(workload: str, iterations: int, interval_ms: int) -> dict[str, Any]:
    ru_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    start = time.perf_counter()
    with TegraStatsSampler(interval_ms=interval_ms) as sampler:
        time.sleep(interval_ms / 1000.0)
        workload_result = run_workload(workload, iterations)
        time.sleep(interval_ms / 1000.0)
    elapsed_s = time.perf_counter() - start
    ru_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    samples = [parse_tegrastats_line(line) for line in sampler.lines]
    payload = {
        "workload": workload,
        "iterations": iterations,
        "interval_ms": interval_ms,
        "elapsed_s": elapsed_s,
        "platform": {
            "machine": platform.machine(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
        },
        "process": {
            "maxrss_before_kb": ru_before,
            "maxrss_after_kb": ru_after,
            "maxrss_delta_kb": ru_after - ru_before,
        },
        "workload_result": workload_result,
        "tegrastats_summary": summarize_samples(samples),
        "samples": samples,
    }
    json_path, csv_path, log_path, chart_paths = write_outputs(payload, samples, workload)
    payload["result_files"] = {
        "json": str(json_path),
        "csv": str(csv_path),
        "raw_log": str(log_path),
        **{name: str(path) for name, path in chart_paths.items()},
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def legacy_manifest(root: Path = PROJECT_ROOT) -> dict[str, str]:
    paths = list((root / "evaluation" / "results").glob("*")) + list(root.glob("*.h5"))
    return {str(path.relative_to(root)): sha256_file(path) for path in sorted(paths) if path.is_file()}


def source_manifest(root: Path = PROJECT_ROOT) -> dict[str, str]:
    paths = list(root.glob("*.py")) + [root / "requirements.txt"]
    for directory in ("adapters", "agent", "backends", "core", "demos", "descriptors", "evaluation", "remote", "scripts", "tests", "twins"):
        paths.extend((root / directory).glob("**/*.py"))
    return {str(path.relative_to(root)): sha256_file(path) for path in sorted(paths) if path.is_file()}


def read_optional(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip().strip("\0")
    except (OSError, UnicodeError):
        return None


def command_fact(command: list[str]) -> dict:
    try:
        result = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10, check=False)
        return {"returncode": result.returncode, "value": result.stdout.strip() if result.returncode == 0 else None}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": None, "value": None, "unavailable": type(exc).__name__}


THREAD_SETTINGS = {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1", "PYTHONHASHSEED": "0"}


def provenance() -> dict:
    return {
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "device_model": read_optional(Path("/proc/device-tree/model")),
        "l4t_release": read_optional(Path("/etc/nv_tegra_release")),
        "memory": read_optional(Path("/proc/meminfo")),
        "online_cpus": os.cpu_count(),
        "affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "cpu_governors": {
            str(path): read_optional(path)
            for path in sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_governor"))
        },
        "cooling_configuration": None,
        "ambient_temperature_c": None,
        "background_workload_controlled": False,
        "power_mode": command_fact(["nvpmodel", "-q"]),
        "git_revision": command_fact(["git", "rev-parse", "HEAD"]),
        "git_status": command_fact(["git", "status", "--porcelain"]),
        "packages": sorted([
            {"name": distribution.metadata.get("Name", "unknown"), "version": distribution.version}
            for distribution in importlib.metadata.distributions()
        ], key=lambda item: item["name"].lower()),
        "worker_environment_overrides": THREAD_SETTINGS,
        "source_sha256": source_manifest(),
        "notes": ["Current environment only; not historical June settings.", "No .env or unfiltered environment captured."],
    }


def sample_process(pid: int, role: str, proc_root: Path = Path("/proc")) -> dict | None:
    try:
        status = (proc_root / str(pid) / "status").read_text()
        stat = (proc_root / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    return {
        "monotonic_ns": time.monotonic_ns(), "pid": pid, "role": role,
        "rss_kib": int(fields["VmRSS"].split()[0]) if "VmRSS" in fields else None,
        "peak_rss_kib": int(fields["VmHWM"].split()[0]) if "VmHWM" in fields else None,
        "cpu_time_s": (int(stat[11]) + int(stat[12])) / os.sysconf("SC_CLK_TCK"),
    }


def power_integral(samples: list[dict], started_ns: int, finished_ns: int) -> dict:
    points = [(row["monotonic_ns"], row.get("vdd_in_mw")) for row in samples]
    energy = 0.0
    coverage_ns = 0
    for (first_time, first_power), (last_time, last_power) in pairwise(points):
        if last_time <= first_time:
            raise ValueError("Power timestamps must increase")
        if first_power is None or last_power is None:
            continue
        lower = max(started_ns, first_time)
        upper = min(finished_ns, last_time)
        if upper <= lower:
            continue
        lower_power = first_power + (last_power - first_power) * (lower - first_time) / (last_time - first_time)
        upper_power = first_power + (last_power - first_power) * (upper - first_time) / (last_time - first_time)
        energy += (lower_power + upper_power) / 2 * (upper - lower) / 1e12
        coverage_ns += upper - lower
    return {
        "platform_energy_j_over_observed_interval": energy if coverage_ns else None,
        "observed_interval_s": coverage_ns / 1e9,
        "coverage_fraction": coverage_ns / (finished_ns - started_ns) if finished_ns > started_ns else 0,
        "platform_mean_power_w": energy / (coverage_ns / 1e9) if coverage_ns else None,
        "idle_subtracted": False,
    }


def phase_resources(samples: list[dict], started_ns: int, finished_ns: int) -> dict:
    result = {}
    for role in sorted({sample["role"] for sample in samples}):
        rows = [sample for sample in samples if sample["role"] == role and started_ns <= sample["monotonic_ns"] <= finished_ns]
        memory = [sample["rss_kib"] for sample in rows if sample["rss_kib"] is not None]
        cpu_percent = None
        if len(rows) >= 2 and rows[-1]["monotonic_ns"] > rows[0]["monotonic_ns"]:
            elapsed = (rows[-1]["monotonic_ns"] - rows[0]["monotonic_ns"]) / 1e9
            cpu_percent = 100 * (rows[-1]["cpu_time_s"] - rows[0]["cpu_time_s"]) / elapsed
        result[role] = {
            "sample_count": len(rows),
            "rss_mean_kib": mean(memory) if memory else None,
            "rss_max_kib": max(memory) if memory else None,
            "cpu_percent_one_core": cpu_percent,
            "cpu_observed_interval_s": (rows[-1]["monotonic_ns"] - rows[0]["monotonic_ns"]) / 1e9 if len(rows) >= 2 else 0,
        }
    return result


def baseline_adjusted_power(phases: dict) -> dict:
    required = [phases[name]["power"] for name in ("idle_before", "measurement", "idle_after")]
    usable = all(item["coverage_fraction"] >= 0.9 and item["platform_mean_power_w"] is not None for item in required)
    return {
        "minimum_coverage_fraction": 0.9,
        "usable": usable,
        "active_minus_bracketing_idle_w": required[1]["platform_mean_power_w"] - mean([
            required[0]["platform_mean_power_w"], required[2]["platform_mean_power_w"],
        ]) if usable else None,
        "interpretation": "Whole-platform active minus ready-worker idle; includes backend and instrumentation, not isolated control-plane power.",
    }


def summarize_trial(trial_dir: Path) -> dict:
    worker = json.loads((trial_dir / "worker_summary.json").read_text())
    with (trial_dir / "requests.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != worker["completed_requests"] or (worker["config"]["iterations"] is not None and len(rows) != worker["config"]["iterations"]):
        raise ValueError("Request count does not match worker summary/config")
    if sum(row["success"] == "True" for row in rows) != worker["successful_requests"]:
        raise ValueError("Success count does not match worker summary")
    if sum(int(row["skipped_releases_before"]) for row in rows) + worker.get("terminal_skipped_releases", 0) != worker["skipped_releases"]:
        raise ValueError("Skipped release count does not match worker summary")
    if worker.get("planned_releases") is not None and len(rows) + worker["skipped_releases"] != worker["planned_releases"]:
        raise ValueError("Duration release accounting does not reconcile")
    latencies = sorted(float(row["invocation_ms"]) for row in rows)
    if not all(math.isfinite(value) and value >= 0 for value in latencies):
        raise ValueError("Invalid wall-clock latency")
    power_path = trial_dir / "tegrastats.jsonl"
    samples = [json.loads(line) for line in power_path.read_text().splitlines()] if power_path.exists() else []
    result = {
        "request_count": len(rows),
        "successful_requests": worker["successful_requests"],
        "skipped_releases": worker["skipped_releases"],
        "latency_mean_ms": mean(latencies) if latencies else None,
        "latency_median_ms": median(latencies) if latencies else None,
        "latency_p95_ms": latencies[math.ceil(0.95 * len(rows)) - 1] if len(rows) >= 20 else None,
        "latency_p99_ms": latencies[math.ceil(0.99 * len(rows)) - 1] if len(rows) >= 100 else None,
        "quantile_method": "nearest rank; p95 requires 20 samples, p99 requires 100",
        "process_peak_rss_kib": worker["process_peak_rss_kib"],
        "sensor_sample_count": len(samples),
        "power_sample_count": sum("vdd_in_mw" in row for row in samples),
        "power": power_integral(samples, worker["measurement_started_ns"], worker["measurement_finished_ns"]),
    }
    if worker.get("schema_version", 1) >= 2:
        process_path = trial_dir / "process_samples.jsonl"
        process_samples = [json.loads(line) for line in process_path.read_text().splitlines()] if process_path.exists() else []
        phases = {
            name: {"power": power_integral(samples, *bounds), "processes": phase_resources(process_samples, *bounds)}
            for name, bounds in worker["phases_ns"].items()
        }
        adjusted = baseline_adjusted_power(phases)
        if worker.get("schema_version", 1) >= 3 and not worker["config"].get("baseline_s"):
            adjusted.update(usable=False, active_minus_bracketing_idle_w=None)
        result.update(phases=phases, baseline_adjusted_power=adjusted)
    return result


def stop_worker(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=3)


def run_isolated_trial(config, trial_dir: Path, *, interval_ms: int, sensors: bool, timeout_s: float, process_sampling: bool = True) -> dict:
    from evaluation.embedded_worker import write_json

    trial_dir.mkdir(parents=True, exist_ok=False)
    with ExitStack() as stack:
        service = None
        if config.backend == "http":
            from remote.service_controller import start_remote_edge_service

            service = start_remote_edge_service(PROJECT_ROOT)
            stack.callback(service.stop)
            config = replace(config, base_url=service.base_url)
        config_path = trial_dir / "config.json"
        write_json(config_path, asdict(config))
        write_json(trial_dir / "sampling.json", {
            "interval_ms": interval_ms, "tegrastats": sensors, "process": process_sampling,
            "timeout_s": timeout_s,
        })
        events = stack.enter_context((trial_dir / "events.jsonl").open("x", encoding="utf-8"))
        errors = stack.enter_context((trial_dir / "stderr.txt").open("x", encoding="utf-8"))
        resources = stack.enter_context((trial_dir / "process_samples.jsonl").open("x", encoding="utf-8"))
        if sensors:
            stack.enter_context(TegraStatsSampler(interval_ms, trial_dir / "tegrastats.jsonl"))
        launched = time.monotonic_ns()
        process = subprocess.Popen(
            [sys.executable, "-m", "evaluation.embedded_worker", "--config", str(config_path), "--output-dir", str(trial_dir)],
            cwd=PROJECT_ROOT, stdout=events, stderr=errors, start_new_session=True,
            env={**os.environ, **THREAD_SETTINGS},
        )
        stack.callback(stop_worker, process)
        timed_out = False
        waiter = threading.Event()
        next_sample_ns = launched
        while process.poll() is None:
            now = time.monotonic_ns()
            targets = [(process.pid, "worker"), (os.getpid(), "controller")] + ([(service.process.pid, "service")] if service else [])
            for pid, role in targets if process_sampling and now >= next_sample_ns else []:
                sample = sample_process(pid, role)
                if sample is not None:
                    resources.write(json.dumps(sample) + "\n")
            if now >= next_sample_ns:
                next_sample_ns = now + interval_ms * 1_000_000
            if (time.monotonic_ns() - launched) / 1e9 > timeout_s:
                timed_out = True
                stop_worker(process)
                break
            waiter.wait(min(0.05, interval_ms / 1000))
        returncode = process.wait()
    events_data = [json.loads(line) for line in (trial_dir / "events.jsonl").read_text().splitlines()]
    ready = next((event["monotonic_ns"] for event in events_data if event["event"] == "ready"), None)
    result = {
        "returncode": returncode, "timed_out": timed_out, "sensors_enabled": sensors, "process_sampling": process_sampling,
        "launch_to_ready_ms": (ready - launched) / 1e6 if ready is not None else None,
        "summary": summarize_trial(trial_dir) if (trial_dir / "worker_summary.json").exists() else None,
    }
    write_json(trial_dir / "summary.json", result)
    return result


def sampling_settings(entry: dict, interval_ms: int, sensors: bool) -> dict:
    settings = {"interval_ms": interval_ms, "tegrastats": sensors, "process": True}
    overrides = entry.get("sampling", {})
    if not isinstance(overrides, dict) or set(overrides) - set(settings):
        raise ValueError("Unknown sampling settings")
    settings.update(overrides)
    if type(settings["interval_ms"]) is not int or settings["interval_ms"] < 1:
        raise ValueError("Sampling interval must be a positive integer")
    if any(type(settings[key]) is not bool for key in ("tegrastats", "process")):
        raise ValueError("Sampling switches must be booleans")
    return settings


def make_calibration_campaign(repetitions: int = 6, duration_s: float = 3.0) -> dict:
    from evaluation.embedded_worker import TrialConfig

    if type(repetitions) is not int or repetitions < 2:
        raise ValueError("Calibration requires at least two paired blocks")
    TrialConfig(iterations=None, duration_s=duration_s)
    conditions = {
        "off": {"tegrastats": False, "process": False, "interval_ms": 250},
        "250ms": {"tegrastats": True, "process": True, "interval_ms": 250},
        "1000ms": {"tegrastats": True, "process": True, "interval_ms": 1000},
    }
    orders = list(permutations(conditions))
    trials = []
    for block in range(repetitions):
        for condition in orders[block % len(orders)]:
            trials.append({
                "pair_id": f"block-{block:03d}", "condition": condition,
                "sampling": conditions[condition], "iterations": None, "duration_s": duration_s,
                "seed": 19 + block, "warmup": 200, "profile": "standard", "mode": "orchestrated",
            })
    return {
        "schema_version": 1, "label": "exploratory-sampler-calibration",
        "purpose": "sampler-calibration", "trials": trials,
    }


def paired_estimate(values: list[float]) -> dict:
    interval = None
    estimate = mean(values) if values else None
    if len(values) >= 2:
        from scipy.stats import t

        margin = float(t.ppf(0.975, len(values) - 1)) * stdev(values) / math.sqrt(len(values))
        interval = [estimate - margin, estimate + margin]
    return {"paired_blocks": len(values), "mean": estimate, "ci95": interval}


def validate_calibration(campaign: dict) -> None:
    expected_sampling = {
        "off": {"tegrastats": False, "process": False, "interval_ms": 250},
        "250ms": {"tegrastats": True, "process": True, "interval_ms": 250},
        "1000ms": {"tegrastats": True, "process": True, "interval_ms": 1000},
    }
    blocks = {}
    for entry in campaign["trials"]:
        pair_id = entry.get("pair_id")
        condition = entry.get("condition")
        if not isinstance(pair_id, str) or not pair_id or condition not in expected_sampling:
            raise ValueError("Calibration requires named paired blocks and known conditions")
        if entry.get("sampling") != expected_sampling[condition]:
            raise ValueError("Calibration condition does not match its sampler settings")
        block = blocks.setdefault(pair_id, {})
        if condition in block:
            raise ValueError("Duplicate calibration condition")
        block[condition] = {key: value for key, value in entry.items() if key not in {"sampling", "pair_id", "condition"}}
    if len(blocks) < 2:
        raise ValueError("Calibration requires two or more paired blocks")
    for block in blocks.values():
        if set(block) != set(expected_sampling) or any(value != block["off"] for value in block.values()):
            raise ValueError("Calibration conditions must have matched workloads in every complete block")


def calibration_report(campaign: dict, results: list[dict]) -> dict:
    blocks = {}
    for entry, result in zip(campaign["trials"], results, strict=True):
        block = blocks.setdefault(entry["pair_id"], {})
        if entry["condition"] in block:
            raise ValueError("Duplicate calibration condition in block")
        block[entry["condition"]] = result
    comparisons = {}
    for condition in ("250ms", "1000ms"):
        pairs = []
        excluded = []
        for block_id, block in blocks.items():
            baseline = block.get("off")
            measured = block.get(condition)
            if any(item is None or item["returncode"] != 0 or item.get("summary") is None for item in (baseline, measured)):
                excluded.append({"pair_id": block_id, "reason": "Missing or failed trial; raw outcome retained"})
                continue
            before = baseline["summary"]["latency_mean_ms"]
            after = measured["summary"]["latency_mean_ms"]
            pairs.append({"pair_id": block_id, "delta_mean_ms": after - before, "delta_mean_percent": 100 * (after / before - 1)})
        comparisons[condition] = {
            "pairs": pairs, "excluded_pairs": excluded,
            "mean_latency_difference_ms": paired_estimate([row["delta_mean_ms"] for row in pairs]),
            "mean_latency_difference_percent": paired_estimate([row["delta_mean_percent"] for row in pairs]),
        }
    return {
        "comparisons": comparisons,
        "method": "Paired block differences; two-sided Student t intervals assuming independent blocks. Exploratory, not equivalence proof.",
        "scope": "Standard edge orchestrator, unpaced duration trials, fixed maintenance. Both platform and process sampling vary together; timeout polling and per-request logs remain enabled.",
    }


def run_campaign(campaign: dict, output_dir: Path, *, interval_ms: int = 250, sensors: bool = True, timeout_s: float = 300) -> dict:
    from evaluation.embedded_worker import TrialConfig, write_json

    if not math.isfinite(timeout_s) or timeout_s <= 0 or interval_ms <= 0:
        raise ValueError("Timeout and sample interval must be positive")
    if set(campaign) - {"schema_version", "label", "trials", "purpose"} or campaign.get("schema_version") != 1:
        raise ValueError("Expected campaign schema_version 1 and only label/trials fields")
    if not isinstance(campaign.get("trials"), list) or not campaign["trials"]:
        raise ValueError("Campaign must contain trials")
    if campaign.get("purpose") == "sampler-calibration":
        validate_calibration(campaign)
    configs = []
    sampling = []
    for entry in campaign["trials"]:
        if "base_url" in entry:
            raise ValueError("Campaign HTTP endpoints must be managed localhost services")
        worker_entry = {key: value for key, value in entry.items() if key not in {"sampling", "pair_id", "condition"}}
        configs.append(TrialConfig(**({**worker_entry, "base_url": "http://127.0.0.1"} if entry.get("backend") == "http" else worker_entry)))
        sampling.append(sampling_settings(entry, interval_ms, sensors))
    if any(settings["tegrastats"] for settings in sampling) and shutil.which("tegrastats") is None:
        raise RuntimeError("tegrastats unavailable; explicitly use --no-tegrastats for portable checks")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    originals = legacy_manifest()
    write_json(output_dir / "legacy_sha256.json", originals)
    write_json(output_dir / "campaign.json", campaign)
    write_json(output_dir / "run_settings.json", {
        "interval_ms": interval_ms, "sensors_enabled": sensors, "timeout_s_per_trial": timeout_s,
        "timestamp_source": "time.monotonic_ns, sampler receipt time",
        "service_thread_environment": {key: os.environ.get(key) for key in THREAD_SETTINGS},
    })
    write_json(output_dir / "provenance.json", provenance())
    results = []
    for index, config in enumerate(configs):
        trial_dir = output_dir / f"trial-{index:03d}"
        try:
            settings = sampling[index]
            result = run_isolated_trial(
                config, trial_dir, interval_ms=settings["interval_ms"], sensors=settings["tegrastats"],
                timeout_s=timeout_s, process_sampling=settings["process"],
            )
        except Exception as exc:
            logger.exception("Embedded trial setup or validation failed")
            result = {"returncode": None, "timed_out": False, "summary": None, "error": f"{type(exc).__name__}: {exc}"}
            trial_dir.mkdir(exist_ok=True)
            if not (trial_dir / "summary.json").exists():
                write_json(trial_dir / "summary.json", result)
        results.append(result)
    unchanged = originals == legacy_manifest()
    result = {
        "trials": results,
        "legacy_artifacts_unchanged": unchanged,
        "all_trials_passed": unchanged and all(row["returncode"] == 0 and not row["timed_out"] for row in results),
        "interpretation": "Exploratory unless separately frozen and approved as a confirmatory campaign.",
    }
    if campaign.get("purpose") == "sampler-calibration":
        result["calibration"] = calibration_report(campaign, results)
    write_json(output_dir / "summary.json", result)
    write_json(output_dir / "artifacts_sha256.json", {
        str(path.relative_to(output_dir)): sha256_file(path) for path in sorted(output_dir.rglob("*")) if path.is_file()
    })
    return result


def verify_campaign(output_dir: Path) -> dict:
    manifest = json.loads((output_dir / "artifacts_sha256.json").read_text())
    actual_paths = {str(path.relative_to(output_dir)) for path in output_dir.rglob("*") if path.is_file() and path.name != "artifacts_sha256.json"}
    if set(manifest) != actual_paths:
        raise ValueError("Artifact manifest does not match the archived file set")
    for relative, expected in manifest.items():
        path = (output_dir / relative).resolve()
        if not path.is_relative_to(output_dir.resolve()) or sha256_file(path) != expected:
            raise ValueError(f"Artifact checksum mismatch: {relative}")
    for trial_dir in sorted(output_dir.glob("trial-*")):
        stored = json.loads((trial_dir / "summary.json").read_text())
        if stored["summary"] is not None and summarize_trial(trial_dir) != stored["summary"]:
            raise ValueError(f"Summary does not reproduce: {trial_dir.name}")
    campaign = json.loads((output_dir / "campaign.json").read_text())
    if campaign.get("purpose") == "sampler-calibration":
        summary = json.loads((output_dir / "summary.json").read_text())
        if calibration_report(campaign, summary["trials"]) != summary["calibration"]:
            raise ValueError("Calibration summary does not reproduce")
    return {"verified_files": len(manifest), "trial_count": len(list(output_dir.glob("trial-*")))}


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile phys-MCP workloads with Jetson tegrastats.")
    parser.add_argument(
        "--workload",
        choices=["edge_control", "overhead", "externalized_backend"],
        default="edge_control",
    )
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--interval-ms", type=int, default=250)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--campaign", type=Path)
    action.add_argument("--calibrate", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    action.add_argument("--verify", type=Path)
    parser.add_argument("--no-tegrastats", action="store_true")
    parser.add_argument("--timeout-s", type=float, default=300)
    parser.add_argument("--calibration-repeats", type=int, default=6)
    parser.add_argument("--calibration-duration-s", type=float, default=3)
    args = parser.parse_args()

    if args.verify is not None:
        print(json.dumps(verify_campaign(args.verify), indent=2))
        return
    if args.campaign is not None or args.calibrate:
        if args.output_dir is None:
            parser.error("--campaign requires a new --output-dir")
        result = run_campaign(
            make_calibration_campaign(args.calibration_repeats, args.calibration_duration_s) if args.calibrate else json.loads(args.campaign.read_text()), args.output_dir,
            interval_ms=args.interval_ms, sensors=not args.no_tegrastats, timeout_s=args.timeout_s,
        )
        print(json.dumps({"output_dir": str(args.output_dir), "all_trials_passed": result["all_trials_passed"]}, indent=2))
        if not result["all_trials_passed"]:
            raise SystemExit(2)
        return

    payload = evaluate(
        workload=args.workload,
        iterations=args.iterations,
        interval_ms=args.interval_ms,
    )
    summary = payload["tegrastats_summary"]
    workload_result = payload["workload_result"]
    print("Tegra resource evaluation complete.")
    print(f"Workload: {payload['workload']}")
    if "latency_mean_ms" in workload_result:
        print(
            "Latency: "
            f"mean={workload_result['latency_mean_ms']:.3f} ms, "
            f"p95={workload_result['latency_p95_ms']:.3f} ms, "
            f"max={workload_result['latency_max_ms']:.3f} ms"
        )
    elif workload_result.get("results"):
        first_row = workload_result["results"][0]
        if "mean_roundtrip_ms" in first_row:
            print(
                "Round-trip: "
                f"mean={first_row['mean_roundtrip_ms']:.3f} ms, "
                f"transport_overhead={first_row.get('transport_overhead_ms', 0.0):.3f} ms"
            )
    if "vdd_in_mean_mw" in summary:
        print(
            "Power VDD_IN: "
            f"mean={summary['vdd_in_mean_mw']:.1f} mW, "
            f"max={summary['vdd_in_max_mw']:.1f} mW"
        )
    if "ram_used_max_mb" in summary:
        print(f"RAM max: {summary['ram_used_max_mb']:.1f} MB / {summary.get('ram_total_mb')} MB")
    print(f"Samples: {summary['sample_count']}")
    print(f"JSON: {payload['result_files']['json']}")
    print(f"CSV:  {payload['result_files']['csv']}")
    print(f"Log:  {payload['result_files']['raw_log']}")
    if "latency_bar_chart" in payload["result_files"]:
        print(f"Latency chart:  {payload['result_files']['latency_bar_chart']}")
    if "resource_bar_chart" in payload["result_files"]:
        print(f"Resource chart: {payload['result_files']['resource_bar_chart']}")


if __name__ == "__main__":
    main()
