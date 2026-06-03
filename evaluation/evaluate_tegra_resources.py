"""Measure Jetson resource usage with tegrastats while running phys-MCP workloads."""

from __future__ import annotations

import argparse
import csv
import json
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
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

import matplotlib.pyplot as plt

try:
    from .common import PROJECT_ROOT, RESULTS_DIR
except ImportError:
    from common import PROJECT_ROOT, RESULTS_DIR

from demos.common import build_default_orchestrator, make_edge_task


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

    def __init__(self, interval_ms: int) -> None:
        tegrastats = shutil.which("tegrastats")
        if tegrastats is None:
            raise RuntimeError("tegrastats was not found on PATH.")
        self._cmd = [tegrastats, "--interval", str(interval_ms)]
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self.lines: list[str] = []

    def __enter__(self) -> "TegraStatsSampler":
        self._process = subprocess.Popen(
            self._cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            preexec_fn=os.setsid,
        )
        self._thread = threading.Thread(target=self._read_lines, daemon=True)
        self._thread.start()
        return self

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

    def _read_lines(self) -> None:
        if self._process is None or self._process.stdout is None:
            return
        for line in self._process.stdout:
            clean = line.strip()
            if clean:
                self.lines.append(clean)


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[index]


def run_edge_control(iterations: int) -> dict[str, Any]:
    """Run the low-latency local edge path repeatedly."""
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
    elif "results" in workload_result and workload_result["results"]:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile phys-MCP workloads with Jetson tegrastats.")
    parser.add_argument(
        "--workload",
        choices=["edge_control", "overhead", "externalized_backend"],
        default="edge_control",
    )
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--interval-ms", type=int, default=250)
    args = parser.parse_args()

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
    elif "results" in workload_result and workload_result["results"]:
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
