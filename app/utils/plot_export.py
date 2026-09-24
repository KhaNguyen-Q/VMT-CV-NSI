"""
Matplotlib export helpers for Displacement vs Time figures.

Edit labels, figsize, pad_frac, and line style here — AppWindow only calls
export_displacement_vs_time / default_plot_path / export_peak_to_peak_plot.
"""
from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def series_to_mm(times_s, positions_px, mm_per_pixel):
    """Convert tracked (t, x_px) arrays to displacement in mm."""
    t = np.asarray(times_s, dtype=float)
    y_px = np.asarray(positions_px, dtype=float)
    mpp = float(mm_per_pixel)
    if mpp <= 0:
        raise ValueError("mm_per_pixel must be > 0 to export displacement in mm")
    return t, y_px * mpp


def padded_limits(values, pad_frac=0.12, min_span=1e-6):
    """
    Axis limits with padding so peaks are not clipped.
    Near-flat series get a small floor span so the plot is readable.
    """
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return -1.0, 1.0
    lo = float(np.min(arr))
    hi = float(np.max(arr))
    span = hi - lo
    if span < min_span:
        mid = 0.5 * (lo + hi)
        half = max(abs(mid) * pad_frac, min_span)
        return mid - half, mid + half
    pad = span * float(pad_frac)
    return lo - pad, hi + pad


def default_plot_path(project_root, axis="x"):
    """data/plots/displacement_vs_time_{axis}.png"""
    axis = "y" if axis == "y" else "x"
    plots_dir = os.path.join(project_root, "data", "plots")
    os.makedirs(plots_dir, exist_ok=True)
    return os.path.join(plots_dir, f"displacement_vs_time_{axis}.png")


def default_peak_to_peak_plot_path(project_root, axis="x"):
    """data/plots/peak_to_peak_{axis}.png"""
    axis = "y" if axis == "y" else "x"
    plots_dir = os.path.join(project_root, "data", "plots")
    os.makedirs(plots_dir, exist_ok=True)
    return os.path.join(plots_dir, f"peak_to_peak_{axis}.png")


def export_displacement_vs_time(
    path,
    times_s,
    displacement_mm,
    *,
    title="Displacement vs Time",
    xlabel="Time (s)",
    ylabel="Displacement (mm)",
    figsize=(12, 5),
    pad_frac=0.12,
    dpi=150,
):
    """
    Save a Displacement vs Time PNG (Gemini-style: time_s vs x_mm).
    Returns the written path.
    """
    t = np.asarray(times_s, dtype=float)
    y = np.asarray(displacement_mm, dtype=float)
    if t.size == 0 or y.size == 0:
        raise ValueError("No data to plot")
    if t.shape != y.shape:
        raise ValueError("times_s and displacement_mm must have the same shape")

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

    fig, ax = plt.subplots(figsize=figsize)
    ax.plot(t, y, label=ylabel, linewidth=1.5)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True)

    t_lo, t_hi = padded_limits(t, pad_frac=pad_frac * 0.5, min_span=1e-3)
    y_lo, y_hi = padded_limits(y, pad_frac=pad_frac)
    ax.set_xlim(t_lo, t_hi)
    ax.set_ylim(y_lo, y_hi)
    ax.legend(loc="best")

    fig.tight_layout()
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path


def export_peak_to_peak_plot(
    path,
    times_s,
    positions_px,
    extrema_rows,
    *,
    axis="x",
    title=None,
    xlabel="Time (s)",
    ylabel=None,
    figsize=(12, 5),
    pad_frac=0.12,
    dpi=150,
):
    """
    Save Position vs Time PNG with top/bottom/plateau extrema markers.
    Returns the written path.
    """
    t = np.asarray(times_s, dtype=float)
    y = np.asarray(positions_px, dtype=float)
    if t.size == 0 or y.size == 0:
        raise ValueError("No data to plot")
    if t.shape != y.shape:
        raise ValueError("times_s and positions_px must have the same shape")

    axis = "y" if axis == "y" else "x"
    if title is None:
        title = f"Peak-to-Peak Extrema ({axis})"
    if ylabel is None:
        ylabel = f"Position {axis} (px)"

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

    fig, ax = plt.subplots(figsize=figsize)
    ax.plot(t, y, color="#1f77b4", linewidth=1.5, label="Position", zorder=1)

    by_label = {"top": [], "bottom": [], "plateau": []}
    for row in extrema_rows:
        label = row.get("label")
        if label in by_label:
            by_label[label].append((row["time_s"], row["position_px"]))

    style = {
        "top": {"c": "#ff7f0e", "marker": "^", "label": "Top"},
        "bottom": {"c": "#17becf", "marker": "v", "label": "Bottom"},
        "plateau": {"c": "#7f7f7f", "marker": "s", "label": "Plateau"},
    }
    for key, points in by_label.items():
        if not points:
            continue
        xs, ys = zip(*points)
        s = style[key]
        ax.scatter(
            xs,
            ys,
            c=s["c"],
            marker=s["marker"],
            s=40,
            label=s["label"],
            zorder=2,
            edgecolors="k",
            linewidths=0.4,
        )

    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True)

    t_lo, t_hi = padded_limits(t, pad_frac=pad_frac * 0.5, min_span=1e-3)
    y_lo, y_hi = padded_limits(y, pad_frac=pad_frac)
    ax.set_xlim(t_lo, t_hi)
    ax.set_ylim(y_lo, y_hi)
    ax.legend(loc="best")

    fig.tight_layout()
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path


def _downsample_xy(t, y, max_points=150_000):
    """Stride-subsample long traces so matplotlib export stays responsive."""
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    n = t.size
    if n <= max_points or max_points < 2:
        return t, y
    step = int(np.ceil(n / max_points))
    return t[::step], y[::step]


def export_voltage_vs_time(
    path,
    series_list,
    *,
    title="Voltage vs Time",
    xlabel="Time (s)",
    ylabel="Voltage (V)",
    figsize=(12, 5),
    pad_frac=0.12,
    dpi=150,
    max_points_per_series=150_000,
):
    """
    Save a Voltage vs Time PNG for one or more (name, times_s, volts) series.
    series_list items: dict with keys name, times_s, volts (arrays).
    Returns the written path.
    """
    if not series_list:
        raise ValueError("No series to plot")

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

    fig, ax = plt.subplots(figsize=figsize)
    all_t = []
    all_y = []
    for item in series_list:
        name = item.get("name") or "series"
        t, y = _downsample_xy(
            item["times_s"],
            item["volts"],
            max_points=max_points_per_series,
        )
        if t.size == 0:
            continue
        ax.plot(t, y, label=name, linewidth=1.2)
        all_t.append(t)
        all_y.append(y)

    if not all_t:
        plt.close(fig)
        raise ValueError("No data to plot")

    t_cat = np.concatenate(all_t)
    y_cat = np.concatenate(all_y)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True)
    t_lo, t_hi = padded_limits(t_cat, pad_frac=pad_frac * 0.5, min_span=1e-3)
    y_lo, y_hi = padded_limits(y_cat, pad_frac=pad_frac)
    ax.set_xlim(t_lo, t_hi)
    ax.set_ylim(y_lo, y_hi)
    ax.legend(loc="best")

    fig.tight_layout()
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path
