"""Reproducible measurements and document build for the embedded workshop draft."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from functools import partial
from pathlib import Path
from statistics import mean

from evaluation.embedded_worker import write_json
from evaluation.evaluate_tegra_resources import (
    paired_estimate,
    sha256_file,
    source_manifest,
    verify_campaign,
)

ROOT = Path(__file__).resolve().parents[1]
PAPER_DIR = ROOT / "docs" / "embedded-feasibility"
STUDY_DIR = ROOT / "evaluation" / "results" / "embedded" / "workshop-study-20261019"
CALIBRATION_DIR = STUDY_DIR.parent / "sampler-calibration-20261019"


def paper_campaign(repetitions: int = 6) -> dict:
    if type(repetitions) is not int or repetitions < 2:
        raise ValueError("At least two paired repetitions are required")
    trials = []
    for backend in ("edge", "chemical", "wetware", "http"):
        for block in range(repetitions):
            modes = ("direct", "orchestrated") if block % 2 == 0 else ("orchestrated", "direct")
            for mode in modes:
                trials.append({
                    "pair_id": f"latency-{backend}-{block:02d}", "condition": mode,
                    "mode": mode, "backend": backend, "profile": "standard",
                    "iterations": 300 if backend == "http" else 2000,
                    "warmup": 100 if backend == "http" else 200,
                    "seed": 19 if backend == "http" else 19 + block,
                    "sampling": {"tegrastats": False, "process": False, "interval_ms": 250},
                })
    for block in range(repetitions):
        modes = ("direct", "orchestrated") if block % 2 == 0 else ("orchestrated", "direct")
        for mode in modes:
            trials.append({
                "pair_id": f"resources-edge-{block:02d}", "condition": mode,
                "mode": mode, "backend": "edge", "profile": "standard",
                "iterations": None, "duration_s": 6, "rate_hz": 100,
                "baseline_s": 3, "warmup": 200, "seed": 19 + block,
                "sampling": {"tegrastats": True, "process": True, "interval_ms": 250},
            })
    return {
        "schema_version": 1,
        "label": "repeated-embedded-feasibility-working-paper",
        "purpose": "workshop-feasibility",
        "trials": trials,
    }


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def analyze(study_dir: Path, calibration_dir: Path) -> dict:
    verification = {"study": verify_campaign(study_dir), "calibration": verify_campaign(calibration_dir)}
    campaign = load_json(study_dir / "campaign.json")
    if campaign != paper_campaign():
        raise ValueError("Manuscript protocol and recorded campaign differ")
    outcome = load_json(study_dir / "summary.json")
    if not outcome["all_trials_passed"]:
        raise ValueError("Campaign failures require manuscript review; no automatic omission is permitted")
    entries = []
    for index, config in enumerate(campaign["trials"]):
        folder = study_dir / f"trial-{index:03d}"
        entries.append({
            "trial": folder.name, "config": config,
            "worker": load_json(folder / "worker_summary.json"),
            "result": load_json(folder / "summary.json"),
        })
    latency = [row for row in entries if row["config"]["pair_id"].startswith("latency-")]
    resources = [row for row in entries if row["config"]["pair_id"].startswith("resources-")]
    latency_rows = []
    for backend in ("edge", "chemical", "wetware", "http"):
        selected = [row for row in latency if row["config"]["backend"] == backend]
        pairs = {}
        for row in selected:
            pairs.setdefault(row["config"]["pair_id"], {})[row["config"]["mode"]] = row["result"]["summary"]["latency_mean_ms"]
        if len(pairs) != 6 or any(set(pair) != {"direct", "orchestrated"} for pair in pairs.values()):
            raise ValueError("Incomplete paired latency data")
        latency_rows.append({
            "backend": backend,
            "direct_mean_ms": mean(pair["direct"] for pair in pairs.values()),
            "orchestrated_mean_ms": mean(pair["orchestrated"] for pair in pairs.values()),
            "added_overhead_ms": paired_estimate([pair["orchestrated"] - pair["direct"] for pair in pairs.values()]),
            "pairs": pairs,
        })
    resource_rows = []
    power_pairs = {}
    for mode in ("direct", "orchestrated"):
        selected = [row for row in resources if row["config"]["mode"] == mode]
        rss = [row["worker"]["process_peak_rss_kib"] / 1024 for row in selected]
        cpu = [row["result"]["summary"]["phases"]["measurement"]["processes"]["worker"]["cpu_percent_one_core"] for row in selected]
        startup = [row["result"]["launch_to_ready_ms"] for row in selected]
        if any(value is None for value in cpu + startup):
            raise ValueError("Resource samples are incomplete")
        resource_rows.append({
            "mode": mode, "peak_rss_mib": rss, "peak_rss_min_mib": min(rss), "peak_rss_max_mib": max(rss),
            "active_cpu_percent_one_core": cpu, "cpu_mean_percent_one_core": mean(cpu),
            "startup_mean_ms": mean(startup),
        })
        for row in selected:
            adjustment = row["result"]["summary"]["baseline_adjusted_power"]
            power_pairs.setdefault(row["config"]["pair_id"], {})[mode] = adjustment["active_minus_bracketing_idle_w"] if adjustment["usable"] else None
    valid_power = {name: pair for name, pair in power_pairs.items() if set(pair) == {"direct", "orchestrated"} and all(value is not None for value in pair.values())}
    power = paired_estimate([pair["orchestrated"] - pair["direct"] for pair in valid_power.values()])
    power["pairs"] = power_pairs
    power["excluded_pairs"] = sorted(set(power_pairs) - set(valid_power))
    provenance = load_json(study_dir / "provenance.json")
    reference = load_json(STUDY_DIR.parent / "implementation-smoke-20261019" / "provenance.json")["source_sha256"]
    evaluated_sources = provenance["source_sha256"]
    current_sources = source_manifest()
    runtime_paths = [name for name in reference if name.startswith(("core/", "adapters/", "twins/", "remote/"))]
    if any(reference[name] != evaluated_sources.get(name) or reference[name] != current_sources.get(name) for name in runtime_paths):
        raise ValueError("Runtime source changed relative to the original isolated baseline")
    return {
        "verification": verification,
        "study_manifest_sha256": sha256_file(study_dir / "artifacts_sha256.json"),
        "calibration_manifest_sha256": sha256_file(calibration_dir / "artifacts_sha256.json"),
        "latency": latency_rows, "resources": resource_rows, "paired_incremental_platform_power_w": power,
        "latency_requests": sum(row["worker"]["completed_requests"] for row in latency),
        "latency_successes": sum(row["worker"]["successful_requests"] for row in latency),
        "resource_requests": sum(row["worker"]["completed_requests"] for row in resources),
        "resource_successes": sum(row["worker"]["successful_requests"] for row in resources),
        "missed_releases": sum(row["worker"]["skipped_releases"] for row in resources),
        "latency_failed_trials": sum(row["result"]["returncode"] != 0 for row in latency),
        "resource_failed_trials": sum(row["result"]["returncode"] != 0 for row in resources),
        "calibration": load_json(calibration_dir / "summary.json")["calibration"],
        "provenance": {key: provenance[key] for key in ("python_version", "platform", "l4t_release", "device_model", "online_cpus", "memory", "power_mode")},
        "runtime_source_unchanged": True,
        "evaluated_git_revision": provenance["git_revision"]["value"],
    }


def figure_outputs(data: dict, paper_dir: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    directory = paper_dir / "figures"
    directory.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    figure, axes = plt.subplots(1, 2, figsize=(7.2, 3.0), gridspec_kw={"width_ratios": [3, 1]})
    for axis, records in zip(axes, (data["latency"][:3], data["latency"][3:]), strict=True):
        for index, record in enumerate(records):
            for mode, offset, color in (("direct", -0.15, "#287271"), ("orchestrated", 0.15, "#b03a48")):
                values = [pair[mode] for pair in record["pairs"].values()]
                axis.scatter([index + offset] * len(values), values, s=18, color=color, alpha=0.7, label=mode if index == 0 else None)
                axis.plot([index + offset - 0.09, index + offset + 0.09], [mean(values)] * 2, color=color, linewidth=2)
        axis.set_xticks(range(len(records)), [row["backend"].capitalize() for row in records])
        axis.set_ylim(bottom=0)
        axis.grid(axis="y", alpha=0.2)
    axes[0].set_ylabel("Mean invocation latency (ms)")
    axes[0].legend(fontsize=8)
    figure.tight_layout()
    figure.savefig(directory / "latency.png", dpi=200)
    figure.savefig(directory / "latency.pdf")
    plt.close(figure)
    figure, axes = plt.subplots(1, 2, figsize=(7.2, 2.8))
    for index, row in enumerate(data["resources"]):
        axes[0].scatter([index] * len(row["peak_rss_mib"]), row["peak_rss_mib"], color="#287271")
        axes[1].scatter([index] * len(row["active_cpu_percent_one_core"]), row["active_cpu_percent_one_core"], color="#b03a48")
    for axis in axes:
        axis.set_xticks([0, 1], ["Direct", "Orchestrated"])
        axis.set_xlim(-0.5, 1.5)
        axis.set_ylim(bottom=0)
        axis.grid(axis="y", alpha=0.2)
    axes[0].set_ylim(0, max(value for row in data["resources"] for value in row["peak_rss_mib"]) * 1.1)
    axes[0].set_ylabel("Worker lifetime peak RSS (MiB)")
    axes[1].set_ylabel("Active CPU (% of one core)")
    figure.tight_layout()
    figure.savefig(directory / "resources.png", dpi=200)
    figure.savefig(directory / "resources.pdf")
    plt.close(figure)
    figure, axis = plt.subplots(figsize=(6, 2.5))
    for index, condition in enumerate(("250ms", "1000ms")):
        estimate = data["calibration"]["comparisons"][condition]["mean_latency_difference_percent"]
        axis.errorbar(estimate["mean"], index, xerr=[[estimate["mean"] - estimate["ci95"][0]], [estimate["ci95"][1] - estimate["mean"]]], fmt="o", color="#287271", capsize=4)
    axis.axvline(0, color="#777777", linestyle="--", linewidth=1)
    axis.set_yticks([0, 1], ["250 ms sampling", "1 s sampling"])
    axis.set_ylim(-0.5, 1.5)
    axis.set_xlabel("Mean latency change versus sampling off (%)")
    axis.grid(axis="x", alpha=0.2)
    figure.tight_layout()
    figure.savefig(directory / "calibration.png", dpi=200)
    figure.savefig(directory / "calibration.pdf")
    plt.close(figure)


def markdown_values(data: dict) -> dict[str, str]:
    latency_lines = ["| Path | Direct mean (ms) | Orchestrated mean (ms) | Added cost, mean [95% CI] (ms) |", "| --- | ---: | ---: | ---: |"]
    for row in data["latency"]:
        effect = row["added_overhead_ms"]
        latency_lines.append(f"| {row['backend'].capitalize()} | {row['direct_mean_ms']:.3f} | {row['orchestrated_mean_ms']:.3f} | {effect['mean']:.3f} [{effect['ci95'][0]:.3f}, {effect['ci95'][1]:.3f}] |")
    resource_lines = ["| Path | Peak RSS range (MiB) | Mean active CPU (%) | Mean startup (ms) |", "| --- | ---: | ---: | ---: |"]
    for row in data["resources"]:
        resource_lines.append(f"| {row['mode'].capitalize()} | {row['peak_rss_min_mib']:.1f}-{row['peak_rss_max_mib']:.1f} | {row['cpu_mean_percent_one_core']:.2f} | {row['startup_mean_ms']:.1f} |")
    overheads = [row["added_overhead_ms"]["mean"] for row in data["latency"][:3]]
    peaks = [peak for row in data["resources"] for peak in row["peak_rss_mib"]]
    power = data["paired_incremental_platform_power_w"]
    if power["mean"] is not None and power["ci95"] is not None:
        power_text = (
            f"Across {power['paired_blocks']} usable paired resource blocks, the difference between orchestrated and direct active-minus-idle platform-power estimates is "
            f"{1000 * power['mean']:+.1f} mW, with a paired 95% interval [{1000 * power['ci95'][0]:+.1f}, {1000 * power['ci95'][1]:+.1f}] mW. "
            f"{len(power['excluded_pairs'])} pairs lacked usable phase coverage. This estimate is not an energy-efficiency claim."
        )
    else:
        power_text = "The available baseline-adjusted power pairs are insufficient for a paired interval; no isolated control-plane energy claim is made."
    memory = dict(line.split(":", 1) for line in data["provenance"]["memory"].splitlines() if ":" in line)
    values = {
        "evaluated_commit": data["evaluated_git_revision"] or "unavailable (use source manifest)",
        "latency_table": "\n".join(latency_lines), "resource_table": "\n".join(resource_lines),
        "local_overhead_range": f"{min(overheads):.3f} to {max(overheads):.3f} ms",
        "edge_overhead": f"{data['latency'][0]['added_overhead_ms']['mean']:.3f} ms",
        "peak_rss_range": f"{min(peaks):.1f}-{max(peaks):.1f}",
        "platform_release": "Jetson Linux (L4T) R36.5.0, Linux 5.15.185-tegra",
        "python_version": data["provenance"]["python_version"], "cpu_count": str(data["provenance"]["online_cpus"]),
        "physical_ram_gib": f"{int(memory['MemTotal'].split()[0]) / 1024 ** 2:.2f}",
        "power_result": power_text,
    }
    for key in ("latency_requests", "latency_successes", "resource_requests", "resource_successes", "missed_releases", "latency_failed_trials", "resource_failed_trials"):
        values[key] = f"{data[key]:,}"
    return values


def render_manuscript(source: str, values: dict[str, str]) -> str:
    def replacement(match):
        key = match.group(1)
        if key not in values:
            raise ValueError(f"Unknown manuscript placeholder: {key}")
        return values[key]

    return re.sub(r"\{\{([a-z_]+)\}\}", replacement, source)


def inline_markup(tokens: list[dict]) -> str:
    rendered = []
    for token in tokens:
        kind = token["type"]
        if kind == "text":
            rendered.append(html.escape(token["raw"]))
        elif kind == "codespan":
            rendered.append(f"<font name='Courier' size='8'>{html.escape(token['raw'])}</font>")
        elif kind in {"strong", "emphasis"}:
            tag = "b" if kind == "strong" else "i"
            rendered.append(f"<{tag}>{inline_markup(token['children'])}</{tag}>")
        elif kind in {"softbreak", "linebreak"}:
            rendered.append(" " if kind == "softbreak" else "<br/>")
        elif kind == "link":
            rendered.append(f"<link href='{html.escape(token['attrs']['url'], quote=True)}'>{inline_markup(token['children'])}</link>")
        else:
            raise ValueError(f"Unsupported inline Markdown node: {kind}")
    return "".join(rendered)


def build_pdf(markdown: str, paper_dir: Path) -> int:
    import mistune
    from pypdf import PdfReader
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        FrameBreak,
        Image,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    page_width, page_height = A4
    margin = 42
    gutter = 18
    column = (page_width - 2 * margin - gutter) / 2
    styles = {
        "body": ParagraphStyle("body", fontName="Times-Roman", fontSize=9.5, leading=11.5, spaceAfter=6, alignment=TA_JUSTIFY),
        "h2": ParagraphStyle("h2", fontName="Times-Bold", fontSize=11, leading=13, spaceBefore=8, spaceAfter=5, keepWithNext=True),
        "h3": ParagraphStyle("h3", fontName="Times-Bold", fontSize=10, leading=12, spaceBefore=6, spaceAfter=4, keepWithNext=True),
        "title": ParagraphStyle("title", fontName="Times-Bold", fontSize=18, leading=20, spaceAfter=7),
        "small": ParagraphStyle("small", fontName="Times-Roman", fontSize=8, leading=10, spaceAfter=5),
        "cell": ParagraphStyle("cell", fontName="Times-Roman", fontSize=7, leading=8.5),
    }

    def footer(canvas, document):
        canvas.setFont("Times-Italic", 8)
        canvas.drawString(margin, page_height - 27, "CP2N2 embedded feasibility - workshop working draft")
        canvas.drawRightString(page_width - margin, 25, str(document.page))

    def columns(top_gap=0):
        return [Frame(margin + index * (column + gutter), margin, column, page_height - 2 * margin - top_gap, id=f"column-{index}", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0) for index in range(2)]

    title_frame = Frame(margin, page_height - margin - 112, page_width - 2 * margin, 112, id="title", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    pdf_path = paper_dir / "paper.pdf"
    document = BaseDocTemplate(str(pdf_path), pagesize=A4, title="CP2N2 on Embedded Linux: A Feasibility Study of the phys-MCP Baseline", author="Author list pending approval", pageCompression=1, invariant=1)
    document.addPageTemplates([
        PageTemplate(id="first", frames=[title_frame, *columns(122)], onPage=footer, autoNextPageTemplate="body"),
        PageTemplate(id="body", frames=columns(), onPage=footer),
    ])
    tokens = mistune.create_markdown(renderer="ast", plugins=["table"])(markdown)
    flowables = []
    in_title = True
    for token in tokens:
        kind = token["type"]
        if kind == "blank_line":
            continue
        if kind == "heading":
            level = token["attrs"]["level"]
            if level == 2 and in_title:
                flowables.append(FrameBreak())
                in_title = False
            flowables.append(Paragraph(inline_markup(token["children"]), styles["title" if level == 1 else f"h{level}"]))
        elif kind == "paragraph":
            children = token["children"]
            if len(children) == 1 and children[0]["type"] == "image":
                image_token = children[0]
                image = Image(str(paper_dir / image_token["attrs"]["url"]))
                image.drawHeight *= column / image.drawWidth
                image.drawWidth = column
                flowables.extend([Spacer(1, 5), image, Paragraph(inline_markup(image_token["children"]), styles["small"])])
            else:
                flowables.append(Paragraph(inline_markup(children), styles["small" if in_title else "body"]))
        elif kind == "table":
            header, body = token["children"]
            rows = [header["children"], *[row["children"] for row in body["children"]]]
            cells = [[Paragraph(inline_markup(cell["children"]), styles["cell"]) for cell in row] for row in rows]
            widths = [column * fraction for fraction in (0.19, 0.23, 0.23, 0.35)]
            table = Table(cells, colWidths=widths, repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("LINEABOVE", (0, 0), (-1, 0), 0.6, colors.black),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
                ("LINEBELOW", (0, -1), (-1, -1), 0.6, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ]))
            flowables.extend([table, Spacer(1, 5)])
        else:
            raise ValueError(f"Unsupported block Markdown node: {kind}")
    document.build(flowables, canvasmaker=partial(Canvas, invariant=1))
    reader = PdfReader(pdf_path)
    text = "\n".join(page.extract_text() for page in reader.pages)
    if "{{" in text or "7. Conclusion" not in text or "References" not in text:
        raise ValueError("PDF completeness verification failed")
    return len(reader.pages)


def build(study_dir: Path, calibration_dir: Path, paper_dir: Path) -> dict:
    data = analyze(study_dir, calibration_dir)
    paper_dir.mkdir(parents=True, exist_ok=True)
    (paper_dir / "paper-results.json").write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    with (paper_dir / "latency-table.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["backend", "direct_mean_ms", "orchestrated_mean_ms", "added_mean_ms", "ci95_low_ms", "ci95_high_ms", "paired_blocks"])
        for row in data["latency"]:
            effect = row["added_overhead_ms"]
            writer.writerow([row["backend"], row["direct_mean_ms"], row["orchestrated_mean_ms"], effect["mean"], *effect["ci95"], effect["paired_blocks"]])
    figure_outputs(data, paper_dir)
    source = (paper_dir / "paper-source.md").read_text(encoding="utf-8")
    manuscript = render_manuscript(source, markdown_values(data))
    (paper_dir / "paper.md").write_text(manuscript, encoding="utf-8")
    pages = build_pdf(manuscript, paper_dir)
    outputs = [paper_dir / name for name in ("paper-source.md", "paper.md", "paper.pdf", "paper-results.json", "latency-table.csv", "paper-campaign.json")]
    outputs.extend(sorted((paper_dir / "figures").glob("*")))
    manifest = {str(path.relative_to(paper_dir)): sha256_file(path) for path in outputs}
    (paper_dir / "paper-manifest.json").write_text(json.dumps({"sha256": manifest, "pdf_pages": pages, "source_archives": data["verification"]}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"pdf_pages": pages, "latency": data["latency"], "resources": data["resources"], "power": data["paired_incremental_platform_power_w"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "build"])
    parser.add_argument("--output", type=Path, default=PAPER_DIR / "paper-campaign.json")
    parser.add_argument("--study-dir", type=Path, default=STUDY_DIR)
    parser.add_argument("--calibration-dir", type=Path, default=CALIBRATION_DIR)
    parser.add_argument("--paper-dir", type=Path, default=PAPER_DIR)
    args = parser.parse_args()
    if args.command == "prepare":
        write_json(args.output, paper_campaign())
        print(f"Prepared {args.output}")
    else:
        print(json.dumps(build(args.study_dir, args.calibration_dir, args.paper_dir), indent=2))


if __name__ == "__main__":
    main()