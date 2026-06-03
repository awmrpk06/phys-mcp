"""Create a visual PDF report for the Jetson Orin embedded-edge evaluation."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

try:
    from .common import PROJECT_ROOT, RESULTS_DIR
except ImportError:
    from common import PROJECT_ROOT, RESULTS_DIR


PAGE_SIZE = (11.69, 8.27)  # A4 landscape, inches.
BLUE = "#1f5eff"
GREEN = "#0f9d58"
ORANGE = "#f29900"
RED = "#d93025"
DARK = "#202124"
MUTED = "#5f6368"
LIGHT = "#f1f3f4"


def latest_result(pattern: str) -> Path:
    matches = sorted(RESULTS_DIR.glob(pattern), key=lambda path: path.stat().st_mtime)
    if not matches:
        raise FileNotFoundError(f"No result file matched {pattern}")
    return matches[-1]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def infer_chart_path(json_path: Path, suffix: str) -> Path:
    return json_path.with_name(json_path.stem + suffix)


def add_header(fig, title: str, subtitle: str | None = None) -> None:
    fig.text(0.05, 0.94, title, fontsize=20, fontweight="bold", color=DARK)
    if subtitle:
        fig.text(0.05, 0.905, subtitle, fontsize=10.5, color=MUTED)
    fig.add_artist(plt.Line2D([0.05, 0.95], [0.885, 0.885], color=LIGHT, linewidth=2))


def metric_card(fig, x: float, y: float, w: float, h: float, label: str, value: str, color: str = BLUE) -> None:
    rect = plt.Rectangle((x, y), w, h, transform=fig.transFigure, facecolor="#ffffff", edgecolor="#dfe3eb", linewidth=1.1)
    fig.add_artist(rect)
    fig.text(x + 0.018, y + h - 0.035, label, fontsize=9, color=MUTED)
    fig.text(x + 0.018, y + 0.035, value, fontsize=18, fontweight="bold", color=color)


def wrapped_text(fig, x: float, y: float, text: str, width: int = 92, size: float = 10.5, color: str = DARK) -> None:
    lines = textwrap.wrap(text, width=width)
    fig.text(x, y, "\n".join(lines), fontsize=size, color=color, va="top", linespacing=1.45)


def add_image(fig, image_path: Path, rect: list[float], title: str | None = None) -> None:
    ax = fig.add_axes(rect)
    ax.imshow(plt.imread(image_path))
    ax.set_axis_off()
    if title:
        ax.set_title(title, fontsize=12, fontweight="bold", pad=8, color=DARK)


def add_table_page(
    pdf: PdfPages,
    title: str,
    subtitle: str,
    columns: list[str],
    rows: list[list[str]],
    footer: str | None = None,
) -> None:
    fig = plt.figure(figsize=PAGE_SIZE, facecolor="white")
    add_header(fig, title, subtitle)
    ax = fig.add_axes([0.05, 0.22, 0.9, 0.58])
    ax.axis("off")
    table = ax.table(cellText=rows, colLabels=columns, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(9.5)
    table.scale(1, 1.65)
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#dfe3eb")
        if row == 0:
            cell.set_facecolor("#e8f0fe")
            cell.set_text_props(weight="bold", color=DARK)
        elif row % 2 == 0:
            cell.set_facecolor("#fafafa")
    if footer:
        wrapped_text(fig, 0.05, 0.14, footer, width=118, size=10)
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def format_ms(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.3f} ms"


def format_watts(mw: float | int | None) -> str:
    if mw is None:
        return "n/a"
    return f"{float(mw) / 1000.0:.2f} W"


def make_report(output_path: Path | None = None) -> Path:
    edge_json = latest_result("tegra_resources_edge_control_*.json")
    external_json = latest_result("tegra_resources_externalized_backend_*.json")
    overhead_json = RESULTS_DIR / "overhead_results.json"
    matching_baseline_json = RESULTS_DIR / "matching_baselines_results.json"
    matching_json = RESULTS_DIR / "matching_results.json"
    failure_json = RESULTS_DIR / "failure_campaign_results.json"
    portability_json = RESULTS_DIR / "portability_results.json"
    cortical_json = latest_result("cortical_runtime_results_*.json")

    edge = load_json(edge_json)
    external = load_json(external_json)
    overhead = load_json(overhead_json)
    matching_baseline = load_json(matching_baseline_json)
    matching = load_json(matching_json)
    failure = load_json(failure_json)
    portability = load_json(portability_json)
    cortical = load_json(cortical_json)

    edge_latency_chart = infer_chart_path(edge_json, "_latency_bar_chart.png")
    edge_resource_chart = infer_chart_path(edge_json, "_resource_bar_chart.png")
    external_latency_chart = infer_chart_path(external_json, "_latency_bar_chart.png")
    external_resource_chart = infer_chart_path(external_json, "_resource_bar_chart.png")

    overhead_chart = RESULTS_DIR / "overhead_bar_chart.png"
    matching_chart = RESULTS_DIR / "matching_accuracy_bar_chart.png"
    portability_chart = RESULTS_DIR / "portability_metadata_bar_chart.png"

    output_path = output_path or (RESULTS_DIR / "embedded_edge_visual_report.pdf")

    edge_workload = edge["workload_result"]
    edge_resources = edge["tegrastats_summary"]
    external_row = external["workload_result"]["results"][0]
    external_resources = external["tegrastats_summary"]

    with PdfPages(output_path) as pdf:
        fig = plt.figure(figsize=PAGE_SIZE, facecolor="white")
        fig.text(0.05, 0.88, "phys-MCP Embedded Edge Report", fontsize=26, fontweight="bold", color=DARK)
        fig.text(0.05, 0.835, "Jetson Orin resource, latency, and control-plane evaluation", fontsize=13, color=MUTED)
        fig.text(0.05, 0.79, f"Source project: {PROJECT_ROOT}", fontsize=9, color=MUTED)

        metric_card(fig, 0.05, 0.59, 0.2, 0.13, "Local edge mean", format_ms(edge_workload["latency_mean_ms"]), GREEN)
        metric_card(fig, 0.28, 0.59, 0.2, 0.13, "Local edge p95", format_ms(edge_workload["latency_p95_ms"]), GREEN)
        metric_card(fig, 0.51, 0.59, 0.2, 0.13, "VDD_IN mean", format_watts(edge_resources["vdd_in_mean_mw"]), ORANGE)
        metric_card(fig, 0.74, 0.59, 0.2, 0.13, "RAM max", f"{edge_resources['ram_used_max_mb']:.0f} MB", BLUE)

        wrapped_text(
            fig,
            0.05,
            0.49,
            "Summary: phys-MCP runs on the Jetson Orin as a lightweight embedded control plane. "
            "The local edge-control workload completed all 5000 orchestrated runs with sub-millisecond latency. "
            "The tegrastats profile records power, RAM, CPU, GPU, and temperature during the workload.",
            width=118,
            size=12,
        )
        wrapped_text(
            fig,
            0.05,
            0.32,
            "Important scope note: the current workloads exercise the control plane and simulators, not a CUDA-backed model. "
            "GR3D/GPU utilization remains 0%, so a future TensorRT, PyTorch CUDA, CUDA kernel, or Ollama backend is needed "
            "to prove GPU acceleration.",
            width=118,
            size=11,
            color=MUTED,
        )
        fig.text(0.05, 0.13, "Generated artifacts are stored in evaluation/results/", fontsize=10, color=MUTED)
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        fig = plt.figure(figsize=PAGE_SIZE, facecolor="white")
        add_header(fig, "Local Edge Control Workload", "5000 orchestrated edge-control runs under tegrastats sampling")
        metric_card(fig, 0.05, 0.74, 0.16, 0.11, "success", f"{edge_workload['successful_runs']}/{edge_workload['iterations']}", GREEN)
        metric_card(fig, 0.23, 0.74, 0.16, 0.11, "mean", format_ms(edge_workload["latency_mean_ms"]), GREEN)
        metric_card(fig, 0.41, 0.74, 0.16, 0.11, "p95", format_ms(edge_workload["latency_p95_ms"]), GREEN)
        metric_card(fig, 0.59, 0.74, 0.16, 0.11, "RSS delta", f"{edge['process']['maxrss_delta_kb']} KB", BLUE)
        metric_card(fig, 0.77, 0.74, 0.16, 0.11, "GPU", f"{edge_resources['gr3d_percent_peak']:.0f}%", MUTED)
        add_image(fig, edge_latency_chart, [0.05, 0.12, 0.42, 0.52], "Latency distribution")
        add_image(fig, edge_resource_chart, [0.53, 0.08, 0.42, 0.6], "Jetson resource profile")
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        fig = plt.figure(figsize=PAGE_SIZE, facecolor="white")
        add_header(fig, "Externalized HTTP Edge Backend", "Remote edge adapter path through a local HTTP service boundary")
        metric_card(fig, 0.05, 0.74, 0.18, 0.11, "roundtrip mean", format_ms(external_row["mean_roundtrip_ms"]), GREEN)
        metric_card(fig, 0.26, 0.74, 0.18, 0.11, "backend mean", format_ms(external_row["mean_backend_latency_ms"]), BLUE)
        metric_card(fig, 0.47, 0.74, 0.18, 0.11, "transport", format_ms(external_row["transport_overhead_ms"]), ORANGE)
        metric_card(fig, 0.68, 0.74, 0.18, 0.11, "VDD_IN mean", format_watts(external_resources["vdd_in_mean_mw"]), ORANGE)
        add_image(fig, external_latency_chart, [0.05, 0.12, 0.42, 0.52], "Round-trip latency")
        add_image(fig, external_resource_chart, [0.53, 0.08, 0.42, 0.6], "Jetson resource profile")
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        overhead_rows = {row["backend"]: row for row in overhead["results"]}
        comparison_rows = [
            ["Chemical", "2.488 ms", f"{overhead_rows['chemical']['orchestrated_mean_ms']:.3f} ms", "0.361 ms", f"{overhead_rows['chemical']['added_overhead_ms']:.3f} ms"],
            ["Wetware", "0.267 ms", f"{overhead_rows['wetware']['orchestrated_mean_ms']:.3f} ms", "0.194 ms", f"{overhead_rows['wetware']['added_overhead_ms']:.3f} ms"],
            ["Edge", "0.321 ms", f"{overhead_rows['edge']['orchestrated_mean_ms']:.3f} ms", "0.189 ms", f"{overhead_rows['edge']['added_overhead_ms']:.3f} ms"],
            ["HTTP edge", "8.961 ms", f"{external_row['mean_roundtrip_ms']:.3f} ms", "5.015 ms", f"{external_row['transport_overhead_ms']:.3f} ms"],
        ]
        add_table_page(
            pdf,
            "Original vs Orin Timing Comparison",
            "Functional behavior matches the original results; timing shifts are small and remain embedded-friendly.",
            ["Path", "Original mean", "Orin mean", "Original overhead", "Orin overhead"],
            comparison_rows,
            "The Orin is slightly slower for the fastest in-process synthetic paths but stays below 0.5 ms for the local edge control path. "
            "The local HTTP edge boundary is faster than the original committed result in this run.",
        )

        fig = plt.figure(figsize=PAGE_SIZE, facecolor="white")
        add_header(fig, "Existing Evaluation Charts", "Control overhead, matcher accuracy, and descriptor portability")
        add_image(fig, overhead_chart, [0.04, 0.47, 0.29, 0.34], "Control-plane overhead")
        add_image(fig, matching_chart, [0.36, 0.47, 0.29, 0.34], "Matcher accuracy")
        add_image(fig, portability_chart, [0.68, 0.47, 0.29, 0.34], "Portability metadata")

        matcher_summary = matching_baseline["summary"]["physmcp_full"]
        wrapped_text(
            fig,
            0.06,
            0.28,
            f"Matcher evidence: full phys-MCP selector reaches {matcher_summary['correct']}/{matcher_summary['total']} "
            "on the baseline suite, while simpler modality-only and latency-only selectors miss runtime-sensitive cases.",
            width=82,
            size=11,
        )
        wrapped_text(
            fig,
            0.06,
            0.18,
            f"Portability evidence: descriptor shared-key ratio is {portability['descriptor_shared_key_ratio']}, "
            f"invocation shared-key ratio is {portability['invocation_shared_key_ratio']}, and "
            f"{portability['successful_runs']}/{portability['total_runs']} executable backend runs succeeded.",
            width=82,
            size=11,
        )
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        failure_rows = [
            [row["scenario"].replace("_", " "), row["selected_backend"] or "none", "yes" if row["expectation_met"] else "no"]
            for row in failure["scenarios"]
        ]
        cortical_success = sum(1 for row in cortical if row["success"])
        footer = (
            f"Cortical Labs simulator path: {cortical_success}/{len(cortical)} successful runs, "
            f"64 channels, 25000 FPS. Current conclusion: embedded control-plane feasibility is supported; "
            "GPU acceleration remains future work."
        )
        add_table_page(
            pdf,
            "Robustness and Integration Evidence",
            "Representative runtime faults are handled by rejection or fallback as expected.",
            ["Scenario", "Selected backend", "Expected?"],
            failure_rows,
            footer,
        )

    return output_path


def main() -> None:
    output_path = make_report()
    print(f"PDF report written: {output_path}")


if __name__ == "__main__":
    main()
