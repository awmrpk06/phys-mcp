"""Summarize and plot a Cortical Labs HDF5 recording artifact.

The CL SDK simulator stores recording data as HDF5 arrays/tables. This helper
turns the raw values into a short human-readable summary plus a PNG overview.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import tables


def latest_recording(project_root: Path) -> Path:
    candidates = sorted(
        project_root.glob("*_recording.h5"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No *_recording.h5 files found under {project_root}")
    return candidates[0]


def attr(root_attrs: Any, name: str, default: Any = None) -> Any:
    return getattr(root_attrs, name, default)


def int_attr(root_attrs: Any, name: str, default: int | None = None) -> int | None:
    value = attr(root_attrs, name, default)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def float_attr(root_attrs: Any, name: str, default: float | None = None) -> float | None:
    value = attr(root_attrs, name, default)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def active_window(root: tables.Group, max_frames: int) -> tuple[int, int]:
    sample_count = root.samples.shape[0]
    start = int_attr(root._v_attrs, "start_timestamp", 0) or 0
    end = int_attr(root._v_attrs, "end_timestamp", None)

    if end is None or end > sample_count or end <= start:
        end = sample_count
        start = max(0, end - max_frames)

    if end - start > max_frames:
        start = end - max_frames

    return max(start, 0), min(end, sample_count)


def select_channels(root: tables.Group, max_channels: int) -> list[int]:
    counts = Counter(int(row["channel"]) for row in root.spikes[:])
    channels = [channel for channel, _ in counts.most_common(max_channels)]
    stim_channels = [int(row["channel"]) for row in root.stims[:]]

    for channel in stim_channels:
        if channel not in channels:
            channels.append(channel)

    if not channels:
        channels = list(range(min(max_channels, root.samples.shape[1])))

    return channels[:max_channels]


def make_plot(path: Path, output_path: Path, max_frames: int, max_channels: int) -> None:
    with tables.open_file(path) as h5:
        root = h5.root
        fps = float_attr(root._v_attrs, "sampling_frequency", 25_000.0) or 25_000.0
        uv_per_unit = float_attr(root._v_attrs, "uV_per_sample_unit", 1.0) or 1.0
        start, end = active_window(root, max_frames=max_frames)
        channels = select_channels(root, max_channels=max_channels)
        segment = np.asarray(root.samples[start:end, channels], dtype=np.float32) * uv_per_unit
        times_s = np.arange(start, end, dtype=np.float32) / fps
        times_s = times_s - times_s[0] if times_s.size else times_s

        spikes = root.spikes[:]
        stims = root.stims[:]

        spike_counts = np.zeros(root.samples.shape[1], dtype=np.int32)
        if len(spikes):
            for channel, count in Counter(int(row["channel"]) for row in spikes).items():
                if channel < spike_counts.size:
                    spike_counts[channel] = count

        fig, axes = plt.subplots(
            3,
            1,
            figsize=(12, 9),
            gridspec_kw={"height_ratios": [2.0, 1.2, 1.5]},
        )
        fig.suptitle(f"Recording overview: {path.name}", fontsize=14, fontweight="bold")

        ax = axes[0]
        if segment.size:
            offset = 0.0
            for index, channel in enumerate(channels):
                trace = segment[:, index]
                if index:
                    offset += max(20.0, float(np.nanstd(trace)) * 8.0)
                ax.plot(times_s, trace + offset, linewidth=0.8, label=f"ch {channel}")
        ax.set_title("Signal traces from selected channels")
        ax.set_xlabel("Seconds in plotted window")
        ax.set_ylabel("uV, offset per channel")
        ax.legend(loc="upper right", ncol=2, fontsize=8)
        ax.grid(True, alpha=0.25)

        ax = axes[1]
        ax.bar(np.arange(spike_counts.size), spike_counts, color="#2c7fb8")
        ax.set_title("Detected spike count per channel")
        ax.set_xlabel("Channel")
        ax.set_ylabel("Spikes")
        ax.grid(True, axis="y", alpha=0.25)

        ax = axes[2]
        if len(spikes):
            spike_channels = np.asarray([int(row["channel"]) for row in spikes])
            spike_times = np.asarray([int(row["timestamp"]) for row in spikes], dtype=np.float64)
            spike_times = (spike_times - spike_times.min()) / fps
            ax.scatter(spike_times, spike_channels, s=10, color="#222222", alpha=0.75, label="spike")
        if len(stims):
            stim_times = np.asarray([int(row["timestamp"]) for row in stims], dtype=np.float64)
            if len(spikes):
                stim_times = (stim_times - np.asarray([int(row["timestamp"]) for row in spikes]).min()) / fps
            else:
                stim_times = stim_times / fps
            for time_s in stim_times:
                ax.axvline(time_s, color="#d95f02", linewidth=1.5, label="stim")
        ax.set_title("Spike raster and stimulation marker")
        ax.set_xlabel("Seconds from first spike in table")
        ax.set_ylabel("Channel")
        ax.set_ylim(-1, root.samples.shape[1])
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            unique = dict(zip(labels, handles))
            ax.legend(unique.values(), unique.keys(), loc="upper right")
        ax.grid(True, alpha=0.25)

        fig.tight_layout(rect=(0, 0, 1, 0.96))
        fig.savefig(output_path, dpi=160)
        plt.close(fig)


def print_summary(path: Path, output_path: Path, max_frames: int) -> None:
    with tables.open_file(path) as h5:
        root = h5.root
        fps = float_attr(root._v_attrs, "sampling_frequency", 25_000.0) or 25_000.0
        uv_per_unit = float_attr(root._v_attrs, "uV_per_sample_unit", 1.0) or 1.0
        start, end = active_window(root, max_frames=max_frames)
        duration_s = (end - start) / fps
        spikes = root.spikes[:]
        stims = root.stims[:]
        counts = Counter(int(row["channel"]) for row in spikes)
        top_counts = ", ".join(f"ch {channel}: {count}" for channel, count in counts.most_common(8))

        print(f"Recording: {path}")
        print(f"Plot:      {output_path}")
        print()
        print("What the arrays mean:")
        print("- /samples: raw signal traces; rows are time frames, columns are electrode channels.")
        print("- /spikes: detected spike events; each row has timestamp, channel, and a waveform snippet.")
        print("- /stims: stimulation events; each row has timestamp and stimulated channel.")
        print()
        print("Human-readable summary:")
        print(f"- channels: {root.samples.shape[1]}")
        print(f"- sample rate: {fps:g} frames/second")
        print(f"- sample scale: 1 raw unit = {uv_per_unit:g} microvolts")
        print(f"- plotted window: frames {start}..{end} ({duration_s:.3f} seconds)")
        print(f"- detected spikes: {len(spikes)}")
        print(f"- stimulation events: {len(stims)}")
        if len(stims):
            stim_desc = ", ".join(
                f"ch {int(row['channel'])} at timestamp {int(row['timestamp'])}"
                for row in stims
            )
            print(f"- stim details: {stim_desc}")
        if top_counts:
            print(f"- most active channels: {top_counts}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "recording",
        nargs="?",
        type=Path,
        help="Path to *_recording.h5. Defaults to the newest recording in the project root.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="PNG output path. Defaults to <recording_stem>_overview.png.",
    )
    parser.add_argument("--max-frames", type=int, default=5000, help="Maximum signal frames to plot.")
    parser.add_argument("--max-channels", type=int, default=6, help="Maximum signal channels to plot.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = Path(__file__).resolve().parents[1]
    recording = args.recording or latest_recording(project_root)
    recording = recording.expanduser().resolve()
    output = args.output or recording.with_name(f"{recording.stem}_overview.png")
    output = output.expanduser().resolve()

    make_plot(recording, output, max_frames=args.max_frames, max_channels=args.max_channels)
    print_summary(recording, output, max_frames=args.max_frames)


if __name__ == "__main__":
    main()
