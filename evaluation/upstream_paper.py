"""Build the current embedded-feasibility paper from pinned v5.1.1 evidence."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from pathlib import Path

from evaluation.reproduce_original import PINNED_COMMIT, digest
from evaluation.summarize_profiles import sustained_windows, verify_profile
from evaluation.workshop_paper import build_pdf, render_manuscript

ROOT = Path(__file__).resolve().parents[1]
PAPER_DIR = ROOT / "docs/embedded-feasibility"
REPRODUCTION_DIR = ROOT / "evaluation/results/original-paper-20261024"
PROFILE_DIR = ROOT / "evaluation/results/upstream-profile-20261024"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def latency_rows(published: dict, aggregate: dict, standalone: dict) -> list[dict]:
    result = []
    for backend in ("chemical", "wetware", "edge"):
        first = next(row for row in aggregate["results"] if row["backend"] == backend)
        second = next(row for row in standalone["results"] if row["backend"] == backend)
        if first["iterations"] != 25 or second["iterations"] != 25:
            raise ValueError("Original-protocol latency table requires 25 iterations per path")
        result.append({
            "backend": backend, "published_ms": published[backend],
            "aggregate_ms": first["added_overhead_ms"], "standalone_ms": second["added_overhead_ms"],
            "iterations_per_path": 25,
        })
    return result


def collect_data(reproduction_dir: Path, profile_dir: Path) -> dict:
    manifests = {}
    for root in (reproduction_dir, profile_dir):
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or not (folder / "reproduction-manifest.json").exists():
                continue
            verify_profile(folder)
            provenance = load_json(folder / "provenance.json")
            if provenance["upstream_commit"] != PINNED_COMMIT:
                raise ValueError("Paper inputs mix different upstream revisions")
            if not load_json(folder / "result.json")["upstream_sources_unchanged"]:
                raise ValueError("Upstream source changed during a recorded experiment")
            manifests[f"{root.name}/{folder.name}"] = digest(folder / "reproduction-manifest.json")
    published = {"chemical": 0.361, "wetware": 0.194, "edge": 0.189}
    latency = latency_rows(published, load_json(reproduction_dir / "core/evaluations/overhead_results.json"), load_json(reproduction_dir / "overhead-standalone/evaluations/overhead_results.json"))
    sustained = profile_dir / "sustained-300s-10hz"
    worker = load_json(sustained / "workload/summary.json")
    if worker["upstream_commit"] != PINNED_COMMIT or worker["duration_s"] != 300 or worker["rate_hz"] != 10:
        raise ValueError("Sustained experiment does not match the manuscript protocol")
    profile = load_json(sustained / "resource-summary.json")["windows"]["steady_workload"]
    roots = [row for row in profile["processes"] if row["role"] == "root"]
    if len(roots) != 1:
        raise ValueError("Expected one persistent sustained workload root")
    cases = load_json(reproduction_dir / "core/evaluations/matching_baselines_results.json")["cases"]
    selectors = {}
    for case in cases:
        counts = selectors.setdefault(case["selector"], {"correct": 0, "total": 0})
        counts["correct"] += int(case["correct"])
        counts["total"] += 1
    provider = load_json(reproduction_dir / "e3-provider/provider-trace/result.json")
    tests_log = (reproduction_dir / "tests-python312/stdout.log").read_text()
    tests_match = re.search(r"(\d+) passed", tests_log)
    if tests_match is None:
        raise ValueError("Cannot establish the recorded upstream test count")
    faults = load_json(reproduction_dir / "core/evaluations/failure_campaign_results.json")["scenarios"]
    return {
        "upstream_commit": PINNED_COMMIT, "source_manifests": manifests,
        "published_latency_source": "https://arxiv.org/html/2605.04256v2#S6.SS4",
        "latency": latency, "workload": worker, "steady_profile": profile, "root_process": roots[0],
        "trend": sustained_windows(sustained), "selectors": selectors,
        "upstream_tests_passed": int(tests_match.group(1)),
        "fault_cases": len(faults), "fault_cases_passed": sum(row["expectation_met"] for row in faults),
        "provider": {key: provider[key] for key in ("trial_count", "recording_artifact", "runtime_kind", "biological_claim")},
        "stage_outcomes": {f"{root.name}/{folder.name}": load_json(folder / "result.json") for root in (reproduction_dir, profile_dir) for folder in sorted(root.iterdir()) if folder.is_dir() and (folder / "result.json").exists()},
    }


def preserve_legacy(paper_dir: Path) -> None:
    target = paper_dir / "legacy-20261019"
    if target.exists():
        return
    source = paper_dir / "paper-source.md"
    if source.exists() and "phys-MCP Baseline" in source.read_text():
        target.mkdir()
        for name in ("paper-source.md", "paper.md", "paper.pdf", "paper-results.json", "paper-manifest.json", "latency-table.csv", "paper-campaign.json"):
            if (paper_dir / name).exists():
                shutil.copy2(paper_dir / name, target / name)
        shutil.copytree(paper_dir / "figures", target / "figures")


def write_figures(data: dict, profile_dir: Path, paper_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    directory = paper_dir / "figures"
    directory.mkdir(exist_ok=True)
    outputs = []
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.labelsize": 8, "legend.fontsize": 7})

    def save(figure, name):
        for extension in ("png", "pdf"):
            path = directory / f"{name}.{extension}"
            figure.savefig(path, dpi=220)
            outputs.append(path)
        plt.close(figure)

    figure, axis = plt.subplots(figsize=(3.4, 2.8), constrained_layout=True)
    for offset, key, label, color, hatch in (
        (-0.23, "published_ms", "Original publication", "#4477AA", ""),
        (0, "aggregate_ms", "Jetson: aggregate run", "#EE7733", "//"),
        (0.23, "standalone_ms", "Jetson: standalone run", "#228833", ".."),
    ):
        axis.barh([index + offset for index in range(3)], [row[key] for row in data["latency"]], height=0.21, label=label, color=color, hatch=hatch)
    axis.set_yticks(range(3), [row["backend"].capitalize() for row in data["latency"]])
    axis.set_xlim(left=0)
    axis.set_xlabel("Mean added orchestration cost (ms)")
    axis.legend(loc="lower left", bbox_to_anchor=(0, 1.01), frameon=False)
    axis.grid(axis="x", alpha=0.2)
    save(figure, "v511-latency-comparison")

    folder = profile_dir / "sustained-300s-10hz"
    start, end = data["workload"]["started_ns"], data["workload"]["finished_ns"]
    snapshots = [json.loads(line) for line in (folder / "process_tree.jsonl").read_text().splitlines()]
    roots = [row for snapshot in snapshots for row in snapshot["processes"] if row["role"] == "root" and start <= row["monotonic_ns"] <= end]
    sensors = [json.loads(line) for line in (folder / "tegrastats.jsonl").read_text().splitlines()]
    sensors = [row for row in sensors if start <= row["monotonic_ns"] <= end]
    with (folder / "workload/requests.csv").open(newline="") as handle:
        requests = list(csv.DictReader(handle))
    if len(requests) != data["workload"]["completed_requests"]:
        raise ValueError("Request count does not match the sustained result")
    figure, axes = plt.subplots(3, 1, figsize=(3.4, 4.5), sharex=True, constrained_layout=True)
    cpu = [row for row in roots if row["cpu_percent_one_core"] is not None]
    axes[0].plot([(row["monotonic_ns"] - start) / 1e9 for row in cpu], [row["cpu_percent_one_core"] for row in cpu], linewidth=0.65, color="#4477AA")
    axes[0].set_ylabel("CPU (% one core)")
    axes[0].set_ylim(bottom=0)
    axes[1].plot([(row["monotonic_ns"] - start) / 1e9 for row in roots], [row["rss_kib"] / 1024 for row in roots], label="RSS", color="#4477AA")
    pss = [row for row in roots if row["pss_kib"] is not None]
    axes[1].plot([(row["monotonic_ns"] - start) / 1e9 for row in pss], [row["pss_kib"] / 1024 for row in pss], label="PSS", color="#EE7733", linestyle="--")
    axes[1].set_ylabel("Memory (MiB)")
    axes[1].set_ylim(0, max(row["rss_kib"] / 1024 for row in roots) * 1.12)
    axes[1].legend(loc="lower right")
    axes[2].plot([(int(row["started_ns"]) - start) / 1e9 for row in requests], [float(row["invocation_ms"]) for row in requests], linewidth=0.55, color="#228833")
    axes[2].set_ylabel("Invocation (ms)")
    axes[2].set_ylim(bottom=0)
    for axis in axes:
        axis.set_xlim(0, 300)
        axis.grid(alpha=0.2)
    axes[-1].set_xlabel("Sustained workload elapsed time (s)")
    save(figure, "v511-sustained-cost")

    figure, axes = plt.subplots(3, 1, figsize=(3.4, 4.1), sharex=True, constrained_layout=True)
    for name, color, linestyle in (("cpu", "#4477AA", "-"), ("gpu", "#EE7733", "--"), ("soc0", "#228833", ":")):
        rows = [row for row in sensors if name in row.get("temperatures_c", {})]
        axes[0].plot([(row["monotonic_ns"] - start) / 1e9 for row in rows], [row["temperatures_c"][name] for row in rows], label=name, color=color, linestyle=linestyle)
    axes[0].set_ylabel("Temperature (C)")
    axes[0].legend(ncol=3, loc="lower left", fontsize=6.5)
    power = [row for row in sensors if row.get("vdd_in_mw") is not None]
    axes[1].plot([(row["monotonic_ns"] - start) / 1e9 for row in power], [row["vdd_in_mw"] / 1000 for row in power], linewidth=0.7, color="#4477AA")
    axes[1].set_ylabel("Platform power (W)")
    axes[1].set_ylim(bottom=0)
    gpu = [row for row in sensors if row.get("gr3d_percent") is not None]
    axes[2].plot([(row["monotonic_ns"] - start) / 1e9 for row in gpu], [row["gr3d_percent"] for row in gpu], linewidth=0.7, color="#EE7733")
    axes[2].set_ylabel("Platform GPU (%)")
    axes[2].set_ylim(bottom=0)
    for axis in axes:
        axis.set_xlim(0, 300)
        axis.grid(alpha=0.2)
    axes[-1].set_xlabel("Sustained workload elapsed time (s)")
    save(figure, "v511-platform-traces")
    return outputs


def tables_and_values(data: dict) -> tuple[dict, dict]:
    workload, profile, process, trend = data["workload"], data["steady_profile"], data["root_process"], data["trend"]
    tables = {
        "outcome_table": [
            ["Experiment", "Protocol", "Observed result"],
            ["Upstream tests", "Unchanged v5.1.1 suite", f"{data['upstream_tests_passed']} passed"],
            ["Selection", "Original seven cases", "7/7 principal; 6/7 simpler selectors"],
            ["Fault handling", "Original five scenarios", f"{data['fault_cases_passed']}/{data['fault_cases']} expected outcomes"],
            ["E3 provider", "Official SDK Simulator", f"{data['provider']['trial_count']} trials; reconstruction matched"],
            ["Distributed A6", "Full original matrix", "Incomplete: connection errors"],
            ["Sustained edge", "300 s, 10 requests/s", f"{workload['successful_requests']}/{workload['completed_requests']} succeeded"],
        ],
        "comparison_table": [["Backend", "Published", "Jetson aggregate", "Jetson standalone"], *[[row["backend"].capitalize(), f"{row['published_ms']:.3f}", f"{row['aggregate_ms']:.3f}", f"{row['standalone_ms']:.3f}"] for row in data["latency"]]],
        "resource_table": [
            ["Metric", "Observed value"],
            ["Successful / planned requests", f"{workload['successful_requests']} / {workload['planned_releases']}"],
            ["Missed releases / replays", f"{workload['missed_releases']} / {workload['idempotent_replays']}"],
            ["Mean invocation latency", f"{workload['latency_mean_ms']:.2f} ms"],
            ["Mean root CPU (one-core basis)", f"{process['cpu_mean_percent_one_core']:.1f}%"],
            ["Sampled peak root RSS", f"{process['rss_max_mib']:.1f} MiB"],
            ["Sampled peak tree PSS", f"{profile['observed_tree_pss_sum_max_mib']:.1f} MiB"],
            ["Maximum platform temperature", f"{profile['max_temperature_c']:.1f} C"],
            ["Mean platform input power", f"{profile['power']['platform_mean_power_w']:.2f} W"],
            ["Power coverage / long gaps", f"{100 * profile['power']['coverage_fraction']:.1f}% / {len(profile['power_gaps_s'])}"],
        ],
        "growth_table": [["Metric (window mean)", "First minute", "Last minute"], *[[label, f"{trend['first'][key]:.1f}", f"{trend['last'][key]:.1f}"] for key, label in (("rss_mean_mib", "RSS (MiB)"), ("pss_mean_mib", "PSS (MiB)"), ("cpu_mean_percent_one_core", "CPU (% one core)"), ("invocation_mean_ms", "Invocation (ms)"))]],
    }
    values = {name: "\n".join(["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * len(rows[0])) + " |", *["| " + " | ".join(row) + " |" for row in rows[1:]]]) for name, rows in tables.items()}
    values.update(
        successes=str(workload["successful_requests"]), requests=str(workload["completed_requests"]),
        mean_latency=f"{workload['latency_mean_ms']:.2f}", cpu_mean=f"{process['cpu_mean_percent_one_core']:.1f}",
        peak_rss=f"{process['rss_max_mib']:.1f}", max_temp=f"{profile['max_temperature_c']:.1f}",
        platform_power=f"{profile['power']['platform_mean_power_w']:.2f}",
        rss_first=f"{trend['first']['rss_mean_mib']:.1f}", rss_last=f"{trend['last']['rss_mean_mib']:.1f}",
        upstream_commit=PINNED_COMMIT,
    )
    return tables, values


def export_results(reproduction_dir: Path, profile_dir: Path, paper_dir: Path) -> tuple[dict, list[Path], dict]:
    data = collect_data(reproduction_dir, profile_dir)
    paper_dir.mkdir(parents=True, exist_ok=True)
    outputs = write_figures(data, profile_dir, paper_dir)
    tables, values = tables_and_values(data)
    for name, rows in tables.items():
        path = paper_dir / f"v511-{name.replace('_', '-')}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle, lineterminator="\n").writerows(rows)
        outputs.append(path)
    result_path = paper_dir / "v511-results.json"
    result_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs.append(result_path)
    manifest = {str(path.relative_to(paper_dir)): digest(path) for path in outputs}
    (paper_dir / "results-manifest.json").write_text(json.dumps({"sha256": manifest, "upstream_commit": PINNED_COMMIT, "evidence_manifests": data["source_manifests"]}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data, outputs, values


def build(reproduction_dir: Path, profile_dir: Path, paper_dir: Path) -> dict:
    data, outputs, values = export_results(reproduction_dir, profile_dir, paper_dir)
    manuscript = render_manuscript((paper_dir / "paper-source.md").read_text(encoding="utf-8"), values)
    (paper_dir / "paper.md").write_text(manuscript, encoding="utf-8")
    (paper_dir / "paper-results.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pages = build_pdf(manuscript, paper_dir)
    outputs.extend(paper_dir / name for name in ("paper-source.md", "paper.md", "paper.pdf", "paper-results.json"))
    manifest = {str(path.relative_to(paper_dir)): digest(path) for path in outputs}
    (paper_dir / "paper-manifest.json").write_text(json.dumps({"sha256": manifest, "pdf_pages": pages, "upstream_commit": PINNED_COMMIT, "evidence_manifests": data["source_manifests"]}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"pdf_pages": pages, "figures": 3, "tables": 4, "verified_stages": len(data["source_manifests"])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["archive-legacy", "build", "export-results"])
    parser.add_argument("--paper-dir", type=Path, default=PAPER_DIR)
    parser.add_argument("--reproduction-dir", type=Path, default=REPRODUCTION_DIR)
    parser.add_argument("--profile-dir", type=Path, default=PROFILE_DIR)
    args = parser.parse_args()
    if args.command == "archive-legacy":
        preserve_legacy(args.paper_dir)
        print("Legacy manuscript preserved")
    elif args.command == "export-results":
        data, outputs, _ = export_results(args.reproduction_dir, args.profile_dir, args.paper_dir)
        print(json.dumps({"exported_results": len(outputs), "verified_stages": len(data["source_manifests"]), "manuscript_written": False}, indent=2))
    else:
        print(json.dumps(build(args.reproduction_dir, args.profile_dir, args.paper_dir), indent=2))


if __name__ == "__main__":
    main()