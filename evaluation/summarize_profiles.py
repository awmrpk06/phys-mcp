"""Verify and report external resource profiles without modifying raw evidence."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean

from evaluation.reproduce_original import digest


def verify_profile(folder: Path) -> int:
    manifest = json.loads((folder / "reproduction-manifest.json").read_text())
    for relative, expected in manifest.items():
        path = (folder / relative).resolve()
        if not path.is_relative_to(folder.resolve()) or digest(path) != expected:
            raise ValueError(f"Profile checksum mismatch: {folder.name}/{relative}")
    return len(manifest)


def sustained_windows(folder: Path, window_s: float = 60) -> dict:
    summary = json.loads((folder / "workload" / "summary.json").read_text())
    start, finish = summary["started_ns"], summary["finished_ns"]
    trees = [json.loads(line) for line in (folder / "process_tree.jsonl").read_text().splitlines()]
    roots = [record for sample in trees for record in sample["processes"] if record["role"] == "root"]
    with (folder / "workload" / "requests.csv").open(newline="") as handle:
        requests = list(csv.DictReader(handle))
    result = {}
    for name, lower, upper in (("first", start, start + round(window_s * 1e9)), ("last", finish - round(window_s * 1e9), finish)):
        selected = [record for record in roots if lower <= record["monotonic_ns"] < upper]
        rss = [record["rss_kib"] / 1024 for record in selected]
        pss = [record["pss_kib"] / 1024 for record in selected if record["pss_kib"] is not None]
        cpu = [record["cpu_percent_one_core"] for record in selected if record["cpu_percent_one_core"] is not None]
        latencies = [float(row["invocation_ms"]) for row in requests if lower <= int(row["started_ns"]) < upper]
        result[name] = {
            "window_s": window_s, "rss_mean_mib": mean(rss) if rss else None,
            "pss_mean_mib": mean(pss) if pss else None,
            "cpu_mean_percent_one_core": mean(cpu) if cpu else None,
            "invocation_mean_ms": mean(latencies) if latencies else None,
        }
    return result


def report_profiles(root: Path, draw: bool = True) -> dict:
    records = []
    verified = 0
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or not (folder / "resource-summary.json").exists():
            continue
        verified += verify_profile(folder)
        result = json.loads((folder / "result.json").read_text())
        summary = json.loads((folder / "resource-summary.json").read_text())
        for name in ("command", "steady_workload"):
            if name not in summary["windows"]:
                continue
            window = summary["windows"][name]
            primary = [process for process in window["processes"] if process["role"] == "root"]
            records.append({
                "run": folder.name, "window": name, "returncode": result["returncode"],
                "guard_reason": result["guard_reason"], "profile_complete": result["profile_complete"],
                "seconds": window["duration_s"], "root_cpu_mean_percent_one_core": primary[0]["cpu_mean_percent_one_core"] if primary else None,
                "root_rss_max_mib": primary[0]["rss_max_mib"] if primary else None,
                "tree_rss_sum_max_mib": window["observed_tree_rss_sum_max_mib"],
                "tree_pss_sum_max_mib": window["observed_tree_pss_sum_max_mib"],
                "max_temperature_c": window["max_temperature_c"],
                "gpu_max_percent": window["gpu_max_percent"],
                "platform_power_mean_w": window["power"]["platform_mean_power_w"],
                "platform_energy_j_observed": window["power"]["platform_energy_j_over_observed_interval"],
                "power_coverage_fraction": window["power"]["coverage_fraction"],
                "sensor_samples": window["sensor_samples"], "tree_samples": window["process_tree_samples"],
                "power_gaps_s": window["power_gaps_s"],
            })
    result = {"verified_files": verified, "records": records}
    (root / "profile-results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def numeric(value):
        return "unavailable" if value is None else f"{value:.2f}"

    lines = [
        "# CP2N2 v5.1.1 Embedded Resource Experiment", "",
        "External 250 ms process-tree and Jetson telemetry; pinned upstream source unchanged. These are profiled experiments, separate from timing-only reproduction runs.", "",
        "| Run/window | Exit | Seconds | Root CPU (% one core) | Root RSS max (MiB) | Tree PSS max (MiB) | Max temperature (C) | Platform power (W) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in records:
        lines.append(f"| {row['run']}/{row['window']} | {row['returncode']} | {numeric(row['seconds'])} | {numeric(row['root_cpu_mean_percent_one_core'])} | {numeric(row['root_rss_max_mib'])} | {numeric(row['tree_pss_sum_max_mib'])} | {numeric(row['max_temperature_c'])} | {numeric(row['platform_power_mean_w'])} |")
    lines.extend([
        "", "## Interpretation", "",
        "- Command windows include imports, evaluator work, output and plotting. Do not label these as control-plane-only footprint or energy.",
        "- The steady_workload row excludes imports and initialization and uses the pinned upstream edge task factory. It is a sustained extension, not an original-paper measurement.",
        "- Root CPU is the mean of sampled interval percentages; 100% is one logical CPU. Root RSS is the maximum observed resident memory, not a lifetime allocation delta.",
        "- Tree PSS sums proportional memory when every observed workload process has a PSS sample; observers are excluded. Summed RSS double-counts shared pages and is retained only in JSON.",
        "- PSS/USS are sampled at most once per second. Children shorter than the sampling interval may not be captured; the sample tree is not an exhaustive lifetime process census.",
        "- CPU/GPU/SoC temperatures measure sensor temperatures, not heat flow. Power and thermal observations describe the platform and include unrelated system activity.",
        "- Missing or long-gap power intervals are not extrapolated. Coverage and gap lists are retained in profile-results.json.",
        "- A failed upstream command remains a failed condition even when telemetry is complete. The profiled full suite does not turn an aborted A6 matrix into a completed reproduction.",
        "- A five-minute run at one rate does not establish one-hour stability, leak freedom, hard real-time behavior, or smartwatch suitability.",
        "", f"Verified {verified} archived files before generating this report. Raw profiles and their per-run manifests are unchanged.", "",
    ])
    sustained = root / "sustained-300s-10hz"
    workload_path = sustained / "workload" / "summary.json"
    if workload_path.exists():
        workload = json.loads(workload_path.read_text())
        trend = sustained_windows(sustained)
        result["sustained_first_last_minute"] = trend
        (root / "profile-results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        lines.extend([
            "## Sustained outcome", "",
            f"- Duration: {workload['duration_s']} s; configured rate: {workload['rate_hz']} requests/s.",
            f"- Completed: {workload['completed_requests']}; successful: {workload['successful_requests']}; missed planned releases: {workload['missed_releases']}.",
            f"- Mean invocation latency: {workload['latency_mean_ms']:.3f} ms; idempotent replays: {workload['idempotent_replays']}; maintenance failures: {workload['maintenance_failures']}.",
            f"- First/last-minute mean RSS: {numeric(trend['first']['rss_mean_mib'])}/{numeric(trend['last']['rss_mean_mib'])} MiB; mean PSS: {numeric(trend['first']['pss_mean_mib'])}/{numeric(trend['last']['pss_mean_mib'])} MiB.",
            f"- First/last-minute mean CPU: {numeric(trend['first']['cpu_mean_percent_one_core'])}/{numeric(trend['last']['cpu_mean_percent_one_core'])}% of one core; invocation latency: {numeric(trend['first']['invocation_mean_ms'])}/{numeric(trend['last']['invocation_mean_ms'])} ms.",
            "- Memory and CPU rose during this observed run. Successful completion is not evidence of a stable memory plateau or indefinite operation; do not hide growth behind the peak-memory number.",
            "", "![Sustained resource trace](sustained-resources.png)", "",
        ])
        if draw:
            plot_sustained(sustained, root / "sustained-resources.png")
    (root / "profile-report.md").write_text("\n".join(lines), encoding="utf-8")
    return result


def plot_sustained(folder: Path, output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    worker = json.loads((folder / "workload" / "summary.json").read_text())
    started, finished = worker["started_ns"], worker["finished_ns"]
    trees = [json.loads(line) for line in (folder / "process_tree.jsonl").read_text().splitlines()]
    sensors = [json.loads(line) for line in (folder / "tegrastats.jsonl").read_text().splitlines()]
    roots = [process for snapshot in trees if started <= snapshot["monotonic_ns"] <= finished for process in snapshot["processes"] if process["role"] == "root"]
    selected = [sample for sample in sensors if started <= sample["monotonic_ns"] <= finished]
    figure, axes = plt.subplots(2, 2, figsize=(10, 6), constrained_layout=True)
    cpu = [process for process in roots if process["cpu_percent_one_core"] is not None]
    axes[0, 0].plot([(process["monotonic_ns"] - started) / 1e9 for process in cpu], [process["cpu_percent_one_core"] for process in cpu], linewidth=0.8)
    axes[0, 0].set_ylabel("Root CPU (% of one core)")
    axes[0, 1].plot([(process["monotonic_ns"] - started) / 1e9 for process in roots], [process["rss_kib"] / 1024 for process in roots], label="RSS")
    pss = [process for process in roots if process["pss_kib"] is not None]
    axes[0, 1].plot([(process["monotonic_ns"] - started) / 1e9 for process in pss], [process["pss_kib"] / 1024 for process in pss], label="PSS")
    axes[0, 1].set_ylabel("Root memory (MiB)")
    axes[0, 1].legend()
    for label in ("cpu", "gpu", "soc0"):
        values = [sample for sample in selected if label in sample.get("temperatures_c", {})]
        axes[1, 0].plot([(sample["monotonic_ns"] - started) / 1e9 for sample in values], [sample["temperatures_c"][label] for sample in values], label=label)
    axes[1, 0].set_ylabel("Temperature (C)")
    axes[1, 0].legend()
    values = [sample for sample in selected if sample.get("vdd_in_mw") is not None]
    axes[1, 1].plot([(sample["monotonic_ns"] - started) / 1e9 for sample in values], [sample["vdd_in_mw"] / 1000 for sample in values], linewidth=0.8)
    axes[1, 1].set_ylabel("Platform input power (W)")
    for axis in axes.flat:
        axis.set_xlabel("Steady workload elapsed time (s)")
        axis.grid(alpha=0.2)
    figure.suptitle("Pinned CP2N2 v5.1.1: 300 s at 10 requests/s")
    figure.savefig(output, dpi=180)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    report = report_profiles(args.root)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()