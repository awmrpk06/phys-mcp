"""Run the original paper's pinned release without modifying its evaluators."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

PINNED_COMMIT = "36fbae002b0a9e066416ea9cafbb966a33fe53bb"
ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def git_fact(upstream: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-C", str(upstream), *arguments], capture_output=True, text=True, check=True).stdout.strip()


def source_hashes(upstream: Path) -> dict:
    paths = subprocess.run(["git", "-C", str(upstream), "ls-files", "-z"], capture_output=True, check=True).stdout.split(b"\0")
    return {name.decode(): digest(upstream / name.decode()) for name in paths if name and (upstream / name.decode()).is_file()}


def stage_command(stage: str, output_dir: Path, *, upstream: Path | None = None, duration_s: float = 300, rate_hz: float = 10) -> list[str]:
    commands = {
        "tests": ["-m", "pytest", "-q"],
        "core": ["-m", "evaluation.run_all_evaluations"],
        "overhead": ["-m", "evaluation.evaluate_overhead"],
        "http": ["-m", "evaluation.evaluate_externalized_backend"],
        "distributed": ["-m", "evaluation.evaluate_distributed_testbed"],
        "e3-control": ["-m", "evaluation.agent_to_pnn_campaign", "--planner", "reference", "--execute-e3", "--output-dir", str(output_dir / "control-trace")],
        "e3-provider": ["-m", "cl.app.run", "cl-apps/cp2n2-biopattern-gate", "cl-apps/cp2n2-biopattern-gate/default.json", str(output_dir / "provider-trace")],
        "sustained": [str(ROOT / "evaluation/upstream_sustained.py"), "--upstream", str(upstream or ROOT / "_temp/cp2n2-v5.1.1"), "--output-dir", str(output_dir / "workload"), "--duration-s", str(duration_s), "--rate-hz", str(rate_hz)],
    }
    if stage not in commands:
        raise ValueError("Only non-hosted, software-only reproduction stages are permitted")
    return [sys.executable, *commands[stage]]


def reproduction_environment(output_dir: Path, stage: str) -> dict:
    environment = {
        name: value for name, value in os.environ.items()
        if not re.search(r"TOKEN|SECRET|PASSWORD|API_KEY", name, re.IGNORECASE)
        and not name.startswith(("CL_", "CP2N2_", "PHYSMCP_"))
        and name not in {"PYTHONPATH", "PYTHONHOME"}
    }
    environment.update(PYTHON_DOTENV_DISABLED="1", CP2N2_RESULTS_DIR=str(output_dir / "evaluations"), MPLBACKEND="Agg")
    if stage == "e3-provider":
        environment.update(PYTHONUTF8="1", CL_SDK_VISUALISATION="0", CL_SDK_ACCELERATED_TIME="1")
    return environment


def run_stage(upstream: Path, output_dir: Path, stage: str, timeout_s: float = 1800, *, profile: bool = False, interval_ms: int = 250, baseline_s: float = 3, duration_s: float = 300, rate_hz: float = 10) -> dict:
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout_s must be finite and positive")
    if type(interval_ms) is not int or interval_ms <= 0 or not math.isfinite(baseline_s) or baseline_s < 0:
        raise ValueError("Invalid profiling interval or baseline duration")
    if profile and shutil.which("tegrastats") is None:
        raise RuntimeError("Resource profiling requires tegrastats")
    if stage == "sustained" and (not profile or not math.isfinite(duration_s) or duration_s <= 0 or not math.isfinite(rate_hz) or not 0 < rate_hz <= 1000):
        raise ValueError("Sustained trials require profiling and valid duration/rate settings")
    upstream = upstream.resolve()
    output_dir = output_dir.resolve()
    if git_fact(upstream, "rev-parse", "HEAD") != PINNED_COMMIT:
        raise ValueError("Upstream checkout does not match the paper's v5.1.1 commit")
    if git_fact(upstream, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Pinned upstream has modified tracked files")
    command = stage_command(stage, output_dir, upstream=upstream, duration_s=duration_s, rate_hz=rate_hz)
    output_dir.mkdir(parents=True, exist_ok=False)
    before = source_hashes(upstream)
    metadata = {
        "stage": stage, "upstream_url": "https://github.com/fischesn/cp2n2",
        "upstream_commit": PINNED_COMMIT, "command": command, "cwd": str(upstream),
        "started_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version,
        "platform": platform.platform(), "timeout_s": timeout_s,
        "packages": sorted([{"name": item.metadata.get("Name", "unknown"), "version": item.version} for item in importlib.metadata.distributions()], key=lambda item: item["name"].lower()),
        "source_sha256": before,
        "environment_policy": "Dotenv disabled; secrets and preexisting provider settings removed. No thread, seed, count or warmup overrides.",
        "scope": "Reference planner is an oracle, not the original hosted Minimax experiment. E3 is software-only.",
        "profile": profile, "interval_ms": interval_ms if profile else None,
    }
    (output_dir / "provenance.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    started = time.monotonic()
    timed_out = False
    guard_reason = None
    windows = {}
    with ExitStack() as stack:
        stdout = stack.enter_context((output_dir / "stdout.log").open("x", encoding="utf-8"))
        stderr = stack.enter_context((output_dir / "stderr.log").open("x", encoding="utf-8"))
        sampler = None
        if profile:
            from evaluation.evaluate_tegra_resources import (
                TegraStatsSampler,
                stop_worker,
            )
            from evaluation.resource_monitor import (
                ProcessTreeSampler,
                thermal_guard_limit,
            )

            limit, trips = thermal_guard_limit()
            settings = {"interval_ms": interval_ms, "baseline_s": baseline_s, "temperature_stop_c": limit, "thermal_trip_points": trips, "min_free_disk_mib": 512, "observer_sources": {str(path.relative_to(ROOT)): digest(path) for path in (Path(__file__), ROOT / "evaluation/resource_monitor.py", ROOT / "evaluation/evaluate_tegra_resources.py", ROOT / "evaluation/upstream_sustained.py")}}
            (output_dir / "profiling.json").write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            sampler = stack.enter_context(TegraStatsSampler(interval_ms, output_dir / "tegrastats.jsonl"))
            tree_output = stack.enter_context((output_dir / "process_tree.jsonl").open("x", encoding="utf-8"))
            idle_started = time.monotonic_ns()
            time.sleep(baseline_s)
            windows["platform_before"] = [idle_started, time.monotonic_ns()]
            if shutil.disk_usage(output_dir).free < 512 * 1024 ** 2:
                raise RuntimeError("Insufficient free disk for profiled run")
        command_started = time.monotonic_ns()
        process = subprocess.Popen(command, cwd=upstream, env=reproduction_environment(output_dir, stage), stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            if profile:
                if sampler.pid is None:
                    raise RuntimeError("Platform sampler did not start")
                observer_pids = (os.getpid(), sampler.pid)
                tree_sampler = ProcessTreeSampler(process.pid, observer_pids)
                while process.poll() is None:
                    tree_output.write(json.dumps(tree_sampler.sample()) + "\n")
                    now = time.monotonic_ns()
                    sample = sampler.latest_sample
                    if (now - command_started) / 1e9 > timeout_s:
                        timed_out = True
                        guard_reason = "timeout"
                    elif shutil.disk_usage(output_dir).free < 512 * 1024 ** 2:
                        guard_reason = "disk_reserve"
                    elif sample and sample.get("max_temperature_c", -math.inf) >= limit:
                        guard_reason = "temperature_limit"
                    elif now - command_started > 2_000_000_000 and (sample is None or sample.get("max_temperature_c") is None or now - sample["monotonic_ns"] > max(2_000_000_000, interval_ms * 4_000_000)):
                        guard_reason = "temperature_telemetry_unavailable"
                    if guard_reason:
                        stop_worker(process)
                        break
                    time.sleep(interval_ms / 1000)
                returncode = process.wait()
            else:
                returncode = process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, 15)
            try:
                returncode = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, 9)
                returncode = process.wait(timeout=5)
        except BaseException:
            os.killpg(process.pid, 15)
            process.wait(timeout=5)
            raise
        command_finished = time.monotonic_ns()
        windows["command"] = [command_started, command_finished]
        if profile:
            idle_started = time.monotonic_ns()
            time.sleep(baseline_s)
            windows["platform_after"] = [idle_started, time.monotonic_ns()]
    profile_summary = None
    if profile:
        from evaluation.resource_monitor import summarize_profile

        workload_path = output_dir / "workload" / "summary.json"
        if workload_path.exists():
            workload = json.loads(workload_path.read_text())
            windows["steady_workload"] = [workload["started_ns"], workload["finished_ns"]]
            windows["ready_worker_idle"] = [workload["ready_ns"], workload["started_ns"]]
        profile_summary = summarize_profile(output_dir, windows, interval_ms)
        (output_dir / "resource-summary.json").write_text(json.dumps(profile_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = {
        "stage": stage, "returncode": returncode, "timed_out": timed_out,
        "elapsed_s": time.monotonic() - started,
        "upstream_sources_unchanged": before == source_hashes(upstream),
        "guard_reason": guard_reason, "profile": profile,
        "windows_ns": windows,
        "profile_complete": (
            profile_summary["windows"]["command"]["sensor_samples"] >= 2
            and profile_summary["windows"]["command"]["process_tree_samples"] >= 2
            and profile_summary["windows"]["command"]["max_temperature_c"] is not None
            and profile_summary["windows"]["command"]["power"]["coverage_fraction"] >= 0.8
        ) if profile else None,
    }
    (output_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {str(path.relative_to(output_dir)): digest(path) for path in sorted(output_dir.rglob("*")) if path.is_file()}
    (output_dir / "reproduction-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def summarize_reproduction(root: Path) -> dict:
    stages = {}
    verified_files = 0
    for folder in sorted(root.iterdir()):
        manifest_path = folder / "reproduction-manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text())
        for relative, expected in manifest.items():
            path = (folder / relative).resolve()
            if not path.is_relative_to(folder.resolve()) or digest(path) != expected:
                raise ValueError(f"Reproduction archive mismatch: {folder.name}/{relative}")
        verified_files += len(manifest)
        stages[folder.name] = json.loads((folder / "result.json").read_text())

    def load(relative):
        return json.loads((root / relative).read_text(encoding="utf-8"))

    reported = {"chemical": 0.361, "wetware": 0.194, "edge": 0.189}
    aggregate = {row["backend"]: row for row in load("core/evaluations/overhead_results.json")["results"]}
    standalone = {row["backend"]: row for row in load("overhead-standalone/evaluations/overhead_results.json")["results"]}
    comparisons = [{
        "backend": name, "published_added_ms": value,
        "jetson_aggregate_added_ms": aggregate[name]["added_overhead_ms"],
        "jetson_standalone_added_ms": standalone[name]["added_overhead_ms"],
        "iterations_per_path": aggregate[name]["iterations"],
    } for name, value in reported.items()]
    selectors = {}
    for case in load("core/evaluations/matching_baselines_results.json")["cases"]:
        counts = selectors.setdefault(case["selector"], {"correct": 0, "total": 0})
        counts["correct"] += int(case["correct"])
        counts["total"] += 1
    provider = load("e3-provider/provider-trace/result.json")
    e3_control = load("e3-control/control-trace/summary.json")
    report = {
        "upstream_commit": PINNED_COMMIT, "verified_files": verified_files, "stages": stages,
        "reproduction_complete": False,
        "comparison_source": "https://arxiv.org/html/2605.04256v2#S6.SS4",
        "overhead_comparison": comparisons,
        "http": load("core/evaluations/externalized_backend_results.json")["results"][0],
        "selectors": selectors,
        "matching_accuracy": load("core/evaluations/matching_results.json")["accuracy"],
        "fault_expectation_rate": load("core/evaluations/failure_campaign_results.json")["success_rate"],
        "holdout": load("core/evaluations/selection_robustness_results.json")["holdout"],
        "provider": {key: provider[key] for key in ("runtime_kind", "evidence_ceiling", "trial_count", "scored_trial_count", "sham_trial_count", "recording_artifact", "biological_claim")},
        "e3_control": {key: value for key, value in e3_control.items() if not isinstance(value, (list, dict))},
        "gaps": {
            "distributed": "Both unchanged full-default attempts aborted with connection-reset/disconnect exceptions; no complete 945-request matrix or final upstream manifest.",
            "hosted_model": "Not run: original AI-Lab credential is unavailable. Reference planner is not a substitute for the 160-call Minimax campaign.",
            "environment": "Python 3.12.15 and CL SDK 1.0.0 needed for cl.sim; original authors' exact interpreter/dependency/hardware configuration not established.",
            "timing": "Released code yields materially higher overhead here than the publication. Hardware-only attribution is unsupported; author reconciliation required.",
        },
    }
    (root / "comparison.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Original-paper protocol reproduction on Jetson", "",
        "Status: partial reproduction, with failures retained. Not a replacement for the original evidence or a completed confirmation.", "",
        f"Pinned unmodified upstream: `{PINNED_COMMIT}` (CP2N2 v5.1.1).", "",
        "## Original versus observed local overhead", "",
        "All numbers are mean added wall-clock milliseconds, using the original 25 iterations per path. The standalone experiment was run after the aggregate campaign failed; both observations are retained, not pooled or selectively substituted.", "",
        "| Backend | Original paper | Jetson aggregate run | Jetson standalone run |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in comparisons:
        lines.append(f"| {row['backend']} | {row['published_added_ms']:.3f} | {row['jetson_aggregate_added_ms']:.3f} | {row['jetson_standalone_added_ms']:.3f} |")
    lines.extend(["", "These observations do not reproduce the publication's sub-millisecond values. They are not a hardware-only slowdown estimate: the paper's exact measured-code lineage, interpreter and dependency environment need to be reconciled with the author.", "", "## Executed stages", ""])
    for name, result in stages.items():
        lines.append(f"- {name}: exit {result['returncode']}; unchanged upstream source: {result['upstream_sources_unchanged']}; logs and checksum manifest retained.")
    lines.extend(["", "## Conformance", ""])
    for name, counts in selectors.items():
        lines.append(f"- {name}: {counts['correct']}/{counts['total']} expected selections.")
    lines.extend([
        f"- Matching accuracy: {report['matching_accuracy']}; fault expectation rate: {report['fault_expectation_rate']}.",
        f"- CL SDK Simulator: {provider['trial_count']} trials, {provider['recording_artifact']['stim_count']} stimulations, {provider['recording_artifact']['spike_count']} spikes; online/offline match: {provider['recording_artifact']['online_offline_match']}.",
        "- The initial Python 3.10 test run failed on missing cl.sim; the isolated Python 3.12 environment passed all 146 upstream tests.",
        "", "## Gaps", "",
    ])
    lines.extend(f"- {name}: {description}" for name, description in report["gaps"].items())
    lines.extend(["", f"Verified {verified_files} reproduction archive files. No original evaluator, task, seed, iteration count, client matrix, timeout profile, or recovery policy was patched.", ""])
    (root / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["tests", "core", "overhead", "http", "distributed", "e3-control", "e3-provider", "sustained", "summarize"])
    parser.add_argument("--upstream", type=Path, default=ROOT / "_temp" / "cp2n2-v5.1.1")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=1800)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--interval-ms", type=int, default=250)
    parser.add_argument("--baseline-s", type=float, default=3)
    parser.add_argument("--duration-s", type=float, default=300)
    parser.add_argument("--rate-hz", type=float, default=10)
    args = parser.parse_args()
    if args.stage == "summarize":
        report = summarize_reproduction(args.output_dir)
        print(json.dumps({"reproduction_complete": report["reproduction_complete"], "verified_files": report["verified_files"], "overhead_comparison": report["overhead_comparison"], "gaps": report["gaps"]}, indent=2))
        return
    result = run_stage(args.upstream, args.output_dir, args.stage, args.timeout_s, profile=args.profile, interval_ms=args.interval_ms, baseline_s=args.baseline_s, duration_s=args.duration_s, rate_hz=args.rate_hz)
    print(json.dumps(result, indent=2))
    if result["returncode"] != 0 or not result["upstream_sources_unchanged"] or (args.profile and not result["profile_complete"]):
        raise SystemExit(2)


if __name__ == "__main__":
    main()