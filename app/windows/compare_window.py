"""Experimental overlay of two exported tracking CSVs. Does not touch the live plot."""

import csv
from io import BytesIO
import os

import matplotlib

matplotlib.use("Agg")
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
import numpy as np
import pyqtgraph as pg
from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches
from scipy.ndimage import percentile_filter
from scipy.signal import savgol_filter
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from utils.utils import (
    dominant_fft_frequency,
    find_peak_to_peak_extrema,
    get_project_root,
)
from windows.graph_viewbox import GraphViewBox

_project_root = get_project_root(os.path.dirname(os.path.abspath(__file__)))


def _read_tracking_csv(path):
    """Read a tracking CSV or peak-to-peak extrema CSV for comparison."""
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("CSV has no data rows")
    fields = rows[0].keys()
    if "time_s" not in fields:
        raise ValueError("CSV needs a time_s column")

    px_col = next((name for name in ("x_px", "y_px") if name in fields), None)
    if px_col is None and "position_px" in fields:
        px_col = "position_px"
    mm_col = next((name for name in ("x_mm", "y_mm") if name in fields), None)
    if px_col is None and mm_col is None:
        raise ValueError(
            "CSV needs an x_px/y_px, x_mm/y_mm, or peak-to-peak position_px column"
        )

    times = []
    px = []
    mm = []
    for row in rows:
        t = _to_float(row.get("time_s"))
        if t is None:
            continue
        times.append(t)
        px.append(_to_float(row.get(px_col)) if px_col else None)
        mm.append(_to_float(row.get(mm_col)) if mm_col else None)
    if len(times) < 2:
        raise ValueError("CSV needs at least two numeric time samples")

    order = np.argsort(times)
    times = np.asarray(times, dtype=float)[order]
    px_arr = np.asarray(
        [np.nan if value is None else value for value in px], dtype=float
    )[order]
    mm_arr = np.asarray(
        [np.nan if value is None else value for value in mm], dtype=float
    )[order]
    return {
        "name": os.path.splitext(os.path.basename(path))[0],
        "path": path,
        "time_s": times,
        "px": px_arr,
        "mm": mm_arr,
    }


def _read_peak_to_peak_csv(path):
    """Read tagged extrema exported by the peak-to-peak analysis."""
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"time_s", "label", "peak_to_peak_px"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(
                "Peak-to-peak CSV needs time_s, label, and peak_to_peak_px columns"
            )
        rows = []
        for row in reader:
            time_s = _to_float(row.get("time_s"))
            label = (row.get("label") or "").strip().casefold()
            amplitude = _to_float(row.get("peak_to_peak_px"))
            if time_s is None or label not in ("top", "bottom", "plateau"):
                continue
            rows.append(
                {
                    "time_s": time_s,
                    "label": label,
                    "peak_to_peak_px": amplitude,
                }
            )
    if not rows:
        raise ValueError("Peak-to-peak CSV has no valid tagged extrema")
    rows.sort(key=lambda row: row["time_s"])
    return {
        "name": os.path.splitext(os.path.basename(path))[0],
        "path": path,
        "rows": rows,
    }


def _peak_to_peak_stats(extrema, start_s, end_s):
    rows = [
        row
        for row in extrema
        if start_s <= row["time_s"] <= end_s
        and row["label"] in ("top", "bottom")
    ]
    complete_amplitudes = []
    previous_extremum = None
    for row in extrema:
        if row["label"] not in ("top", "bottom"):
            continue
        if (
            row["peak_to_peak_px"] is not None
            and previous_extremum is not None
            and previous_extremum["label"] != row["label"]
            and start_s <= previous_extremum["time_s"] <= end_s
            and start_s <= row["time_s"] <= end_s
        ):
            complete_amplitudes.append(row["peak_to_peak_px"])
        previous_extremum = row

    amplitudes = np.asarray(complete_amplitudes, dtype=float)
    if amplitudes.size > 2:
        keep = np.asarray(
            [
                amplitude
                >= 0.5
                * np.median(np.delete(amplitudes, index))
                for index, amplitude in enumerate(amplitudes)
            ],
            dtype=bool,
        )
        amplitudes = amplitudes[keep]
    top_count = sum(row["label"] == "top" for row in rows)
    bottom_count = sum(row["label"] == "bottom" for row in rows)
    return {
        "rows": rows,
        "cycle_count": min(top_count, bottom_count),
        "sample_count": int(amplitudes.size),
        "mean": float(np.mean(amplitudes)) if amplitudes.size else None,
        "std": (
            float(np.std(amplitudes, ddof=1 if amplitudes.size > 1 else 0))
            if amplitudes.size
            else None
        ),
    }


def _cycle_period_stats(extrema, start_s, end_s):
    peak_times = np.asarray(
        [
            row["time_s"]
            for row in extrema
            if start_s <= row["time_s"] <= end_s and row["label"] == "top"
        ],
        dtype=float,
    )
    periods = np.diff(peak_times)
    return {
        "mean": float(np.mean(periods)) if periods.size else None,
        "std": (
            float(np.std(periods, ddof=1 if periods.size > 1 else 0))
            if periods.size
            else None
        ),
        "sample_count": int(periods.size),
    }


def _to_float(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not np.isfinite(number):
        return None
    return number


def _series_position(series, use_mm):
    values = series["mm"] if use_mm else series["px"]
    mask = np.isfinite(series["time_s"]) & np.isfinite(values)
    t = series["time_s"][mask]
    y = values[mask]
    if t.size < 2:
        raise ValueError(f"{series['name']} has no numeric position samples")
    mean = float(np.mean(y))
    return t, y - mean, mean


def _phase_weight(label):
    normalized = "".join(
        character for character in str(label).lower() if character.isalnum()
    )
    if normalized in ("phase1", "phasei", "phaseone"):
        return "phase_1", 3.0
    if normalized in ("phase2", "phaseii", "phasetwo"):
        return "phase_2", 2.0
    if normalized in ("phase3", "phaseiii", "phasethree"):
        return "phase_3", 0.0
    return None, 0.0


def _aligned_phase_samples(t_base, y_base, t_compare, y_compare, offset, start, end):
    """Pair comparison values interpolated onto baseline times within a range."""
    compare_mask = np.isfinite(t_compare) & np.isfinite(y_compare)
    compare_times = t_compare[compare_mask]
    compare_values = y_compare[compare_mask]
    if compare_times.size < 2:
        empty = np.array([], dtype=float)
        return empty, empty

    order = np.argsort(compare_times)
    compare_times = compare_times[order]
    compare_values = compare_values[order]
    compare_times, unique_indices = np.unique(compare_times, return_index=True)
    compare_values = compare_values[unique_indices]
    if compare_times.size < 2:
        empty = np.array([], dtype=float)
        return empty, empty

    baseline_mask = (
        np.isfinite(t_base)
        & np.isfinite(y_base)
        & (t_base >= start)
        & (t_base <= end)
        & (t_base >= compare_times[0] + offset)
        & (t_base <= compare_times[-1] + offset)
    )
    paired_times = t_base[baseline_mask]
    baseline_values = y_base[baseline_mask]
    comparison_values = np.interp(paired_times - offset, compare_times, compare_values)
    valid = np.isfinite(baseline_values) & np.isfinite(comparison_values)
    return baseline_values[valid], comparison_values[valid]


def _pearson_correlation(values_a, values_b):
    if values_a.size < 2 or values_a.size != values_b.size:
        return None
    if np.ptp(values_a) <= np.finfo(float).eps:
        return None
    if np.ptp(values_b) <= np.finfo(float).eps:
        return None
    correlation = float(np.corrcoef(values_a, values_b)[0, 1])
    return correlation if np.isfinite(correlation) else None


def _weighted_correlation(phase_samples):
    """Combine phase correlations in Fisher-z space using phase weights."""
    weighted_z_scores = []
    total_weight = 0.0
    for weight, (baseline_values, comparison_values) in phase_samples.values():
        correlation = _pearson_correlation(baseline_values, comparison_values)
        if correlation is None or weight <= 0:
            continue
        clipped = float(np.clip(correlation, -1.0 + 1e-12, 1.0 - 1e-12))
        weighted_z_scores.append(weight * np.arctanh(clipped))
        total_weight += weight
    if total_weight == 0:
        return None
    return float(np.tanh(sum(weighted_z_scores) / total_weight))


def _relative_difference_pct(difference, baseline):
    if difference is None or baseline is None:
        return None
    if abs(baseline) <= np.finfo(float).eps:
        return None
    return 100.0 * difference / abs(baseline)


def _phase_waveform_figure(baseline, compare, ranges, offset, use_mm):
    """Render each selected range against baseline-relative elapsed time."""
    if baseline is None:
        return None

    unit = "mm" if use_mm else "px"
    baseline_times, baseline_values, _ = _series_position(baseline, use_mm)
    compare_data = None
    if compare is not None:
        compare_times, compare_values, _ = _series_position(compare, use_mm)
        compare_data = (compare_times, compare_values)

    figure = Figure(figsize=(9, 6.6), constrained_layout=True)
    canvas = FigureCanvasAgg(figure)
    grid = figure.add_gridspec(2, 2, height_ratios=(1.25, 1))
    overlay_axis = figure.add_subplot(grid[0, :])
    baseline_axis = figure.add_subplot(grid[1, 0])
    compare_axis = figure.add_subplot(grid[1, 1])
    max_duration = max(
        (phase["end_s"] - phase["start_s"] for phase in ranges),
        default=1e-6,
    )

    for index, phase in enumerate(ranges, start=1):
        start_s = phase["start_s"]
        end_s = phase["end_s"]
        duration = end_s - start_s
        label = f"Range {index} ({start_s:.3f}–{end_s:.3f} s)"
        baseline_mask = (
            (baseline_times >= start_s) & (baseline_times <= end_s)
        )
        baseline_x = baseline_times[baseline_mask] - start_s
        baseline_y = baseline_values[baseline_mask]
        if baseline_x.size:
            overlay_axis.plot(
                baseline_x,
                baseline_y,
                color="#168c83",
                linewidth=1.25,
                label=f"Baseline — {label}",
            )
            baseline_axis.plot(
                baseline_x,
                baseline_y,
                color="#168c83",
                linewidth=1.25,
                label=label,
            )

        if compare_data is not None and offset is not None:
            compare_times, compare_values = compare_data
            compare_mask = (
                (compare_times + offset >= start_s)
                & (compare_times + offset <= end_s)
            )
            compare_x = compare_times[compare_mask] + offset - start_s
            compare_y = compare_values[compare_mask]
            if compare_x.size:
                overlay_axis.plot(
                    compare_x,
                    compare_y,
                    color="#e47735",
                    linewidth=1.15,
                    alpha=0.9,
                    label=f"Comparison — {label}",
                )
                compare_axis.plot(
                    compare_x,
                    compare_y,
                    color="#e47735",
                    linewidth=1.15,
                    label=label,
                )

    for axis in (overlay_axis, baseline_axis, compare_axis):
        axis.set_xlim(0, max(max_duration, 1e-6))

    overlay_axis.set_title("Waveform overlay")
    overlay_axis.set_ylabel(f"Displacement ({unit}, mean-subtracted)")
    overlay_axis.grid(True, alpha=0.25)
    overlay_axis.legend(loc="best", fontsize="small")

    baseline_axis.set_title("Baseline")
    baseline_axis.set_ylabel(f"Displacement ({unit}, mean-subtracted)")
    baseline_axis.set_xlabel("Elapsed time from range start (s)")
    baseline_axis.grid(True, alpha=0.25)
    if baseline_axis.lines:
        baseline_axis.legend(loc="best", fontsize="small")

    compare_axis.set_title("Comparison")
    compare_axis.set_ylabel(f"Displacement ({unit}, mean-subtracted)")
    compare_axis.set_xlabel("Elapsed time from range start (s)")
    compare_axis.grid(True, alpha=0.25)
    if compare_axis.lines:
        compare_axis.legend(loc="best", fontsize="small")
    else:
        compare_axis.text(
            0.5,
            0.5,
            "Comparison data unavailable",
            ha="center",
            va="center",
            transform=compare_axis.transAxes,
        )

    figure.suptitle("Selected-range waveform comparison")
    output = BytesIO()
    canvas.print_png(output)
    figure.clear()
    output.seek(0)
    return output


def _odd_window(length, fraction):
    window = max(5, int(round(length * fraction)))
    if window % 2 == 0:
        window += 1
    return min(window, length if length % 2 else length - 1)


def _smooth_phase_segment(
    times,
    values,
    phase,
    target_peak_to_peak=None,
    tagged_cycles=0,
):
    """Smooth a bounded phase without filtering neighboring phases."""
    if len(values) < 5:
        return values.copy()

    label = phase["label"].strip().casefold()
    duration = float(times[-1] - times[0])
    if duration <= 0:
        return values.copy()

    if label == "phase 1":
        cycles = phase["expected_cycles"] or tagged_cycles
        if not cycles:
            centered = values - np.median(values)
            crossings = np.count_nonzero(np.diff(np.signbit(centered)))
            cycles = max(1, crossings / 2)
        harmonics = min(3, max(1, (len(values) - 2) // 2))
        phase_angle = 2 * np.pi * cycles * (times - times[0]) / duration
        columns = [np.ones(len(values))]
        for harmonic in range(1, harmonics + 1):
            columns.extend(
                (np.cos(harmonic * phase_angle), np.sin(harmonic * phase_angle))
            )
        design = np.column_stack(columns)
        coefficients, _, _, _ = np.linalg.lstsq(design, values, rcond=None)
        smoothed = design @ coefficients
    else:
        if label == "phase 2":
            cycles = phase["expected_cycles"]
            if not cycles:
                centered = values - np.median(values)
                crossings = np.count_nonzero(np.diff(np.signbit(centered)))
                cycles = max(1.0, crossings / 2)
            window = int(round(len(values) / cycles * 0.4))
            if window % 2 == 0:
                window -= 1
            window = max(5, window)
        elif label == "phase 3":
            window = _odd_window(len(values), 0.15)
        else:
            window = _odd_window(len(values), 0.07)
        window = min(window, len(values) if len(values) % 2 else len(values) - 1)
        if window < 5:
            return values.copy()

        uniform_times = np.linspace(times[0], times[-1], len(times))
        uniform_values = np.interp(uniform_times, times, values)
        filtered = savgol_filter(uniform_values, window_length=window, polyorder=2)
        smoothed = np.interp(times, uniform_times, filtered)

    if label == "phase 1":
        target = target_peak_to_peak
        if target is not None and target > 0:
            smooth_stats = _peak_to_peak_stats(
                find_peak_to_peak_extrema(times, smoothed),
                float(times[0]),
                float(times[-1]),
            )
            current = smooth_stats["mean"]
        else:
            target = float(np.ptp(values))
            current = float(np.ptp(smoothed))
        if target > 0:
            if current is None or current <= np.finfo(float).eps:
                smoothed = values.copy()
                current = float(np.ptp(smoothed))
            if current > np.finfo(float).eps:
                center = float(np.mean(smoothed))
                smoothed = center + (smoothed - center) * (target / current)
    edge_width = min(max(1, len(values) // 10), 10, len(values) // 2)
    if edge_width:
        weights = np.ones(len(values))
        ramp = np.linspace(0.0, 1.0, edge_width + 1)[:-1]
        weights[:edge_width] = ramp
        weights[-edge_width:] = ramp[::-1]
        smoothed = values * (1.0 - weights) + smoothed * weights
    return smoothed


def _smooth_phase_ranges(times, values, phases, baseline_extrema=None):
    smoothed = values.copy()
    for phase in phases:
        if not phase["smooth"]:
            continue
        mask = (times >= phase["start_s"]) & (times <= phase["end_s"])
        indices = np.flatnonzero(mask)
        if indices.size:
            target_peak_to_peak = None
            tagged_cycles = 0
            if (
                baseline_extrema is not None
                and phase["label"].strip().casefold() == "phase 1"
            ):
                stats = _peak_to_peak_stats(
                    baseline_extrema,
                    phase["start_s"],
                    phase["end_s"],
                )
                target_peak_to_peak = stats["mean"]
                tagged_cycles = stats["cycle_count"]
            smoothed[indices] = _smooth_phase_segment(
                times[indices],
                values[indices],
                phase,
                target_peak_to_peak=target_peak_to_peak,
                tagged_cycles=tagged_cycles,
            )
    return smoothed


def estimate_phase_offset(t_base, y_base, t_cmp, y_cmp):
    """Seconds to add to compare time so it lines up with the baseline within one cycle."""
    freq = dominant_fft_frequency(t_base, y_base)
    if freq > 1e-6:
        max_lag = 1.0 / freq
    else:
        span = float(min(t_base[-1] - t_base[0], t_cmp[-1] - t_cmp[0]))
        max_lag = max(0.05, 0.25 * span)

    dt = float(np.median(np.diff(t_base)))
    if not np.isfinite(dt) or dt <= 0:
        return 0.0

    n_steps = int(max_lag / dt)
    if n_steps > 200:
        n_steps = 200
    if n_steps < 1:
        return 0.0

    best_lag = 0.0
    best_score = -np.inf
    for lag in np.linspace(-max_lag, max_lag, n_steps * 2 + 1):
        t_shifted = t_cmp + lag
        t0 = max(float(t_base[0]), float(t_shifted[0]))
        t1 = min(float(t_base[-1]), float(t_shifted[-1]))
        if t1 - t0 < 4 * dt:
            continue
        count = min(2000, max(32, int((t1 - t0) / dt)))
        grid = np.linspace(t0, t1, count)
        yb = np.interp(grid, t_base, y_base)
        yc = np.interp(grid, t_shifted, y_cmp)
        yb = yb - np.mean(yb)
        yc = yc - np.mean(yc)
        denom = np.linalg.norm(yb) * np.linalg.norm(yc)
        if denom == 0:
            continue
        score = float(np.dot(yb, yc) / denom)
        if score > best_score:
            best_score = score
            best_lag = float(lag)
    return best_lag


def comparison_outliers(t_base, y_base, t_cmp, y_cmp, offset):
    """Classify point residuals by local baseline scale and persistence."""
    t_plot = t_cmp + offset
    t0 = max(float(t_base[0]), float(t_plot[0]))
    t1 = min(float(t_base[-1]), float(t_plot[-1]))
    mask = (t_plot >= t0) & (t_plot <= t1)
    if not np.any(mask):
        empty = np.array([], dtype=float)
        empty_mask = np.array([], dtype=bool)
        return empty, empty, empty, empty, empty_mask, empty_mask

    t = t_plot[mask]
    y_c = y_cmp[mask]
    y_b = np.interp(t, t_base, y_base)
    residual = y_c - y_b
    sample_interval = float(np.median(np.diff(t)))
    if not np.isfinite(sample_interval) or sample_interval <= 0:
        sample_interval = float(np.median(np.diff(t_base)))
    window = max(5, int(round(1.0 / sample_interval)))
    if window % 2 == 0:
        window += 1
    local_span = (
        percentile_filter(y_b, 90, size=window, mode="nearest")
        - percentile_filter(y_b, 10, size=window, mode="nearest")
    )
    signal_span = float(np.percentile(y_b, 95) - np.percentile(y_b, 5))
    if signal_span <= np.finfo(float).eps:
        signal_span = float(np.ptp(y_b)) or 1.0

    absolute_residual = np.abs(residual)
    review_threshold = np.maximum(0.25 * local_span, 0.075 * signal_span)
    strong_threshold = np.maximum(0.40 * local_span, 0.15 * signal_span)
    strong_candidates = absolute_residual > strong_threshold
    candidate_indices = np.flatnonzero(strong_candidates)
    if candidate_indices.size:
        time_gaps = np.diff(t[candidate_indices])
        gap_limit = 1.5 * sample_interval
        runs = np.split(
            candidate_indices,
            np.flatnonzero(time_gaps > gap_limit) + 1,
        )
        sustained_duration = 0.05
        strong = np.zeros(t.size, dtype=bool)
        for run in runs:
            if (
                run.size >= 3
                and t[run[-1]] - t[run[0]] >= sustained_duration
            ):
                strong[run] = True
    else:
        strong = np.zeros(t.size, dtype=bool)

    review = (absolute_residual > review_threshold) & ~strong
    return t, y_c, y_b, residual, review, strong


def behavior_outliers(
    t_base,
    y_base,
    t_cmp,
    y_cmp,
    offset,
    phase_ranges,
    baseline_extrema=None,
    n_sigmas=3.0,
):
    """Compare each range against tagged baseline peak-to-peak statistics."""
    findings = []
    for phase in phase_ranges:
        start = phase["start_s"]
        end = phase["end_s"]
        compare_start = start - offset
        compare_end = end - offset
        compare_mask = (t_cmp >= compare_start) & (t_cmp <= compare_end)

        baseline_stats = (
            _peak_to_peak_stats(baseline_extrema, start, end)
            if baseline_extrema is not None
            else {"cycle_count": 0, "sample_count": 0, "mean": None, "std": None}
        )
        compare_extrema = find_peak_to_peak_extrema(
            t_cmp[compare_mask], y_cmp[compare_mask]
        )
        baseline_cycles = baseline_stats["cycle_count"]
        compare_cycles = min(
            sum(row["label"] == "top" for row in compare_extrema),
            sum(row["label"] == "bottom" for row in compare_extrema),
        )
        expected_cycles = phase["expected_cycles"] or baseline_cycles

        if phase["expected_cycles"] and baseline_cycles and (
            baseline_cycles != phase["expected_cycles"]
        ):
            difference = abs(baseline_cycles - phase["expected_cycles"])
            findings.append(
                {
                    "finding_type": "baseline_cycle_count",
                    "phase": phase["label"],
                    "time_s": start,
                    "expected": phase["expected_cycles"],
                    "observed": baseline_cycles,
                    "ratio": "",
                    "baseline_pos": "",
                    "compare_pos": "",
                    "residual": "",
                    "tag": "strong_outlier" if difference > 1 else "review",
                    "details": (
                        f"Selected baseline range contains {baseline_cycles} "
                        f"complete cycles; configured expectation is "
                        f"{phase['expected_cycles']}."
                    ),
                }
            )

        if expected_cycles and compare_cycles != expected_cycles:
            difference = abs(compare_cycles - expected_cycles)
            if compare_cycles < expected_cycles:
                description = (
                    f"Not enough cycles as expected for {phase['label']}"
                )
            else:
                description = f"Too many cycles for {phase['label']}"
            findings.append(
                {
                    "finding_type": "cycle_count",
                    "phase": phase["label"],
                    "time_s": start,
                    "expected": expected_cycles,
                    "observed": compare_cycles,
                    "ratio": (
                        compare_cycles / expected_cycles
                        if expected_cycles
                        else ""
                    ),
                    "baseline_pos": "",
                    "compare_pos": "",
                    "residual": "",
                    "tag": "strong_outlier" if difference > 1 else "review",
                    "details": description,
                }
            )

        amplitude_mean = baseline_stats["mean"]
        amplitude_std = baseline_stats["std"]
        if amplitude_mean is None or amplitude_std is None:
            continue

        review_threshold = max(
            2.0 * amplitude_std,
            abs(amplitude_mean) * 0.05,
            1e-9,
        )
        strong_threshold = max(
            n_sigmas * amplitude_std,
            abs(amplitude_mean) * 0.10,
            review_threshold,
            1e-9,
        )

        for row in compare_extrema:
            amplitude = row["peak_to_peak_px"]
            if amplitude is None:
                continue
            difference = abs(amplitude - amplitude_mean)
            if difference <= review_threshold:
                continue
            compare_time = row["time_s"] + offset
            baseline_position = (
                float(np.interp(compare_time, t_base, y_base))
                if t_base[0] <= compare_time <= t_base[-1]
                else ""
            )
            findings.append(
                {
                    "finding_type": "peak_to_peak_amplitude",
                    "phase": phase["label"],
                    "time_s": compare_time,
                    "expected": amplitude_mean,
                    "observed": float(amplitude),
                    "ratio": (
                        float(amplitude / amplitude_mean)
                        if amplitude_mean
                        else ""
                    ),
                    "baseline_pos": baseline_position,
                    "compare_pos": float(row["position_px"]),
                    "residual": "",
                    "tag": (
                        "strong_outlier"
                        if difference > strong_threshold
                        else "review"
                    ),
                    "details": (
                        f"Peak-to-peak amplitude differs from the baseline mean "
                        f"by more than {n_sigmas:g} standard deviations "
                        f"(mean={amplitude_mean:.4g}, std={amplitude_std:.4g}, "
                        f"n={baseline_stats['sample_count']})."
                    ),
                }
            )
    return findings


class CompareWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Compare tracking series")
        self.resize(1100, 720)
        self._baseline = None
        self._baseline_extrema = None
        self._compare = None
        self._outlier_rows = []
        self._phase_ranges = []
        self._setting_offset = False
        self._phase_region = None

        layout = QVBoxLayout(self)

        self.graph_layout = pg.GraphicsLayoutWidget()
        self.plot = self.graph_layout.addPlot(
            title="Baseline plot vs Compare plot",
            viewBox=GraphViewBox(),
        )
        self.graph_layout.setToolTip(
            "Wheel: zoom both axes | Ctrl+wheel/right-drag: X axis | "
            "Shift+wheel/right-drag: Y axis | Left-drag: pan"
        )
        self.plot.showGrid(x=True, y=True)
        self.plot.addLegend()
        self.plot.setLabel("bottom", "time (s)")
        self.plot.setLabel("left", "Position (zeroed)")

        self.baseline_curve = self.plot.plot(
            pen=pg.mkPen(color="#03fcc2", width=2),
            name="Baseline",
        )
        self.smoothed_baseline_curve = self.plot.plot(
            pen=pg.mkPen(color="#f2c14e", width=2, style=pg.QtCore.Qt.DashLine),
            name="Smoothed baseline",
        )
        self.compare_curve = self.plot.plot(
            pen=pg.mkPen(color="#ff7f50", width=2),
            name="Compare",
        )
        for curve in (self.baseline_curve, self.compare_curve):
            curve.setDownsampling(auto=True, method="subsample")
            curve.setClipToView(True)

        self.review_plot = self.plot.plot(
            pen=None,
            symbol="o",
            symbolSize=6,
            symbolPen=pg.mkPen(color=(255, 210, 60), width=2),
            symbolBrush=pg.mkBrush(255, 210, 60),
            name="Review",
        )
        self.strong_outlier_plot = self.plot.plot(
            pen=None,
            symbol="o",
            symbolSize=8,
            symbolPen=pg.mkPen(color=(255, 65, 65), width=2),
            symbolBrush=pg.mkBrush(255, 65, 65),
            name="Strong outlier",
        )
        layout.addWidget(self.graph_layout)

        visibility_controls = QHBoxLayout()
        visibility_controls.addWidget(QLabel("Show (display only):"))
        self.show_baseline_check = QCheckBox("Baseline")
        self.show_baseline_check.setChecked(True)
        self.show_baseline_check.toggled.connect(self._update_curve_visibility)
        visibility_controls.addWidget(self.show_baseline_check)
        self.show_smoothed_check = QCheckBox("Smoothed baseline")
        self.show_smoothed_check.setChecked(True)
        self.show_smoothed_check.toggled.connect(self._update_curve_visibility)
        visibility_controls.addWidget(self.show_smoothed_check)
        self.show_compare_check = QCheckBox("Compare")
        self.show_compare_check.setChecked(True)
        self.show_compare_check.toggled.connect(self._update_curve_visibility)
        visibility_controls.addWidget(self.show_compare_check)
        visibility_controls.addStretch(1)
        layout.addLayout(visibility_controls)

        controls = QHBoxLayout()
        baseline_btn = QPushButton("Load baseline CSV…")
        baseline_btn.clicked.connect(self._load_baseline)
        controls.addWidget(baseline_btn)

        compare_btn = QPushButton("Load compare CSV…")
        compare_btn.clicked.connect(self._load_compare)
        controls.addWidget(compare_btn)
        self.load_extrema_btn = QPushButton("Load baseline peak-to-peak CSV…")
        self.load_extrema_btn.setToolTip(
            "Load the tagged top/bottom extrema CSV exported by Peak to Peak."
        )
        self.load_extrema_btn.clicked.connect(self._load_baseline_extrema)

        controls.addWidget(QLabel("Phase offset (s):"))
        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(-30.0, 30.0)
        self.offset_spin.setDecimals(4)
        self.offset_spin.setSingleStep(0.001)
        self.offset_spin.setEnabled(False)
        self.offset_spin.valueChanged.connect(self._on_offset_changed)
        controls.addWidget(self.offset_spin)

        self.save_btn = QPushButton("Save outliers CSV…")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save_outliers)
        controls.addWidget(self.save_btn)
        self.save_smoothed_btn = QPushButton("Save smoothed baseline CSV…")
        self.save_smoothed_btn.setEnabled(False)
        self.save_smoothed_btn.clicked.connect(self._save_smoothed_baseline)
        controls.addWidget(self.save_smoothed_btn)
        controls.addWidget(QLabel("Amplitude sensitivity (σ):"))
        self.sensitivity_spin = QDoubleSpinBox()
        self.sensitivity_spin.setRange(0.1, 10.0)
        self.sensitivity_spin.setDecimals(1)
        self.sensitivity_spin.setSingleStep(0.5)
        self.sensitivity_spin.setValue(3.0)
        self.sensitivity_spin.valueChanged.connect(self._on_sensitivity_changed)
        controls.addWidget(self.sensitivity_spin)
        controls.addStretch(1)
        layout.addLayout(controls)

        extrema_controls = QHBoxLayout()
        extrema_controls.addWidget(self.load_extrema_btn)
        self.extrema_source_label = QLabel("No baseline peak-to-peak CSV loaded.")
        extrema_controls.addWidget(self.extrema_source_label)
        extrema_controls.addStretch(1)
        layout.addLayout(extrema_controls)

        phase_controls = QHBoxLayout()
        phase_controls.addWidget(QLabel("Baseline behavior:"))
        self.phase_combo = QComboBox()
        self.phase_combo.setEditable(True)
        self.phase_combo.addItems(["Phase 1", "Phase 2", "Phase 3", "Other"])
        self.phase_combo.currentTextChanged.connect(
            self._on_phase_label_changed
        )
        phase_controls.addWidget(self.phase_combo)
        self.smooth_range_check = QCheckBox("Smooth this range")
        self.smooth_range_check.setToolTip(
            "Apply phase-specific smoothing to this baseline range."
        )
        phase_controls.addWidget(self.smooth_range_check)
        phase_controls.addWidget(QLabel("Expected cycles (0 = tagged baseline count):"))
        self.expected_cycles_spin = QSpinBox()
        self.expected_cycles_spin.setRange(0, 10000)
        self.expected_cycles_spin.setValue(0)
        phase_controls.addWidget(self.expected_cycles_spin)
        self.add_phase_btn = QPushButton("Add selected baseline range")
        self.add_phase_btn.setToolTip(
            "Drag the blue region's edges on the plot to select a baseline "
            "behavior range, then add it here."
        )
        self.add_phase_btn.setEnabled(False)
        self.add_phase_btn.clicked.connect(self._add_phase_range)
        phase_controls.addWidget(self.add_phase_btn)
        self.remove_phase_btn = QPushButton("Remove selected range")
        self.remove_phase_btn.setEnabled(False)
        self.remove_phase_btn.clicked.connect(self._remove_phase_range)
        phase_controls.addWidget(self.remove_phase_btn)
        self.export_phase_ranges_btn = QPushButton("Export ranges CSV…")
        self.export_phase_ranges_btn.setEnabled(False)
        self.export_phase_ranges_btn.clicked.connect(self._save_phase_ranges)
        phase_controls.addWidget(self.export_phase_ranges_btn)
        self.export_report_btn = QPushButton("Export report table…")
        self.export_report_btn.setEnabled(False)
        self.export_report_btn.clicked.connect(self._save_report_table)
        phase_controls.addWidget(self.export_report_btn)
        phase_controls.addStretch(1)
        layout.addLayout(phase_controls)

        self.phase_table = QTableWidget(0, 9)
        self.phase_table.setHorizontalHeaderLabels(
            [
                "Behavior",
                "Start (s)",
                "End (s)",
                "Expected cycles",
                "Tagged cycles",
                "P2P mean (px)",
                "P2P std (px)",
                "Samples",
                "Smooth",
            ]
        )
        self.phase_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.phase_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.phase_table.setMaximumHeight(120)
        self.phase_table.itemSelectionChanged.connect(
            self._on_phase_selection_changed
        )
        layout.addWidget(self.phase_table)

        self.status = QLabel("Load a baseline CSV and a compare CSV.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def _csv_start_dir(self):
        folder = os.path.join(_project_root, "data", "csv") if _project_root else ""
        if folder and os.path.isdir(folder):
            return folder
        return ""

    def _update_curve_visibility(self, _checked=False):
        self.baseline_curve.setVisible(self.show_baseline_check.isChecked())
        has_smoothed_ranges = any(
            phase["smooth"] for phase in self._phase_ranges
        )
        self.smoothed_baseline_curve.setVisible(
            self.show_smoothed_check.isChecked() and has_smoothed_ranges
        )
        show_compare = self.show_compare_check.isChecked()
        self.compare_curve.setVisible(show_compare)
        self.review_plot.setVisible(show_compare)
        self.strong_outlier_plot.setVisible(show_compare)

    def _pick_csv(self, title):
        path, _ = QFileDialog.getOpenFileName(
            self,
            title,
            self._csv_start_dir(),
            "CSV files (*.csv);;All files (*)",
        )
        return path or None

    def _load_baseline(self):
        path = self._pick_csv("Load baseline CSV")
        if not path:
            return
        try:
            self._baseline = _read_tracking_csv(path)
        except Exception as exc:
            QMessageBox.warning(self, "Load baseline", f"Failed to load CSV:\n{exc}")
            return
        self._baseline_extrema = None
        self.extrema_source_label.setText("No baseline peak-to-peak CSV loaded.")
        self._phase_ranges = []
        self.phase_table.setRowCount(0)
        self.remove_phase_btn.setEnabled(False)
        self.export_phase_ranges_btn.setEnabled(False)
        self.export_report_btn.setEnabled(False)
        if self._phase_region is not None:
            self.plot.removeItem(self._phase_region)
        t = self._baseline["time_s"]
        self._phase_region = pg.LinearRegionItem(
            values=(float(t[0]), float(t[-1])),
            brush=pg.mkBrush(80, 160, 255, 45),
            pen=pg.mkPen(80, 160, 255, 180),
        )
        self._phase_region.setBounds((float(t[0]), float(t[-1])))
        self._phase_region.setZValue(-10)
        self.plot.addItem(self._phase_region)
        self.add_phase_btn.setEnabled(True)
        self._align_and_redraw(estimate_offset=True)

    def _load_baseline_extrema(self):
        path = self._pick_csv("Load baseline peak-to-peak CSV")
        if not path:
            return
        try:
            self._baseline_extrema = _read_peak_to_peak_csv(path)
        except Exception as exc:
            QMessageBox.warning(
                self, "Load baseline peak-to-peak CSV", f"Failed to load CSV:\n{exc}"
            )
            return
        self.extrema_source_label.setText(
            f"Loaded tagged extrema: {self._baseline_extrema['name']}"
        )
        self._refresh_phase_table()
        self._align_and_redraw(estimate_offset=False)

    def _load_compare(self):
        path = self._pick_csv("Load compare CSV")
        if not path:
            return
        try:
            self._compare = _read_tracking_csv(path)
        except Exception as exc:
            QMessageBox.warning(self, "Load compare", f"Failed to load CSV:\n{exc}")
            return
        self._align_and_redraw(estimate_offset=True)

    def _on_offset_changed(self, _value):
        if self._setting_offset:
            return
        self._align_and_redraw(estimate_offset=False)

    def _on_sensitivity_changed(self, _value):
        self._align_and_redraw(estimate_offset=False)

    def _on_phase_label_changed(self, label):
        self.expected_cycles_spin.setValue(0)

    def _add_phase_range(self):
        if self._phase_region is None:
            return
        start, end = sorted(float(value) for value in self._phase_region.getRegion())
        if end <= start:
            QMessageBox.warning(
                self, "Add behavior range", "Select a non-empty baseline time range."
            )
            return
        if any(
            start < phase["end_s"] and end > phase["start_s"]
            for phase in self._phase_ranges
        ):
            QMessageBox.warning(
                self,
                "Add behavior range",
                "Behavior ranges cannot overlap. Adjust the selected range and try again.",
            )
            return

        self._phase_ranges.append(
            {
                "label": self.phase_combo.currentText(),
                "start_s": start,
                "end_s": end,
                "expected_cycles": self.expected_cycles_spin.value(),
                "smooth": self.smooth_range_check.isChecked(),
            }
        )
        self._phase_ranges.sort(key=lambda phase: phase["start_s"])
        self._refresh_phase_table()
        self._align_and_redraw(estimate_offset=False)

    def _remove_phase_range(self):
        selected_rows = sorted(
            {index.row() for index in self.phase_table.selectionModel().selectedRows()},
            reverse=True,
        )
        for row in selected_rows:
            del self._phase_ranges[row]
        self._refresh_phase_table()
        self._align_and_redraw(estimate_offset=False)

    def _refresh_phase_table(self):
        self.phase_table.setRowCount(len(self._phase_ranges))
        for row, phase in enumerate(self._phase_ranges):
            stats = (
                _peak_to_peak_stats(
                    self._baseline_extrema["rows"],
                    phase["start_s"],
                    phase["end_s"],
                )
                if self._baseline_extrema is not None
                else None
            )
            values = (
                phase["label"],
                f"{phase['start_s']:.4f}",
                f"{phase['end_s']:.4f}",
                str(phase["expected_cycles"] or "tagged"),
                str(stats["cycle_count"]) if stats else "—",
                f"{stats['mean']:.4f}" if stats and stats["mean"] is not None else "—",
                f"{stats['std']:.4f}" if stats and stats["std"] is not None else "—",
                str(stats["sample_count"]) if stats else "—",
                "Yes" if phase["smooth"] else "No",
            )
            for column, value in enumerate(values):
                self.phase_table.setItem(row, column, QTableWidgetItem(value))
        self.remove_phase_btn.setEnabled(bool(self._phase_ranges))
        self.export_phase_ranges_btn.setEnabled(bool(self._phase_ranges))
        self.export_report_btn.setEnabled(bool(self._phase_ranges))

    def _on_phase_selection_changed(self):
        selected = self.phase_table.selectionModel().selectedRows()
        self.remove_phase_btn.setEnabled(bool(selected))

    def _save_phase_ranges(self):
        if not self._phase_ranges:
            QMessageBox.information(
                self, "Export ranges", "There are no baseline ranges to export."
            )
            return
        start = self._csv_start_dir()
        default_name = (
            "comparison_ranges_report.csv"
            if self._compare is not None
            else "baseline_ranges.csv"
        )
        suggested = (
            os.path.join(start, default_name)
            if start
            else default_name
        )
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export baseline ranges CSV",
            suggested,
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"

        fieldnames = [
            "baseline",
            "compare",
            "baseline_extrema",
            "behavior",
            "phase_offset_s",
            "baseline_start_s",
            "baseline_end_s",
            "compare_start_s",
            "compare_end_s",
            "compare_coverage_start_s",
            "compare_coverage_end_s",
            "compare_coverage_pct",
            "expected_cycles",
            "baseline_cycles",
            "compare_cycles",
            "cycles_difference",
            "baseline_p2p_mean_px",
            "baseline_p2p_std_px",
            "baseline_p2p_samples",
            "compare_p2p_mean_px",
            "compare_p2p_std_px",
            "compare_p2p_samples",
            "p2p_difference_px",
            "p2p_difference_pct",
            "p2p_absolute_error_px",
            "baseline_period_mean_s",
            "baseline_period_std_s",
            "compare_period_mean_s",
            "compare_period_std_s",
            "period_difference_s",
            "period_difference_pct",
            "period_absolute_error_s",
            "cycles_absolute_error",
            "cycles_difference_pct",
            "confirmed_outlier_count",
            "phase_correlation",
            "phase_weight",
            "overall_weighted_correlation",
            "phase_weighting",
            "correlation_method",
            "smooth",
        ]
        try:
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            offset = (
                float(self.offset_spin.value())
                if self._baseline is not None and self._compare is not None
                else None
            )
            compare_times = compare_positions = None
            if self._compare is not None and np.isfinite(self._compare["px"]).any():
                compare_mask = (
                    np.isfinite(self._compare["time_s"])
                    & np.isfinite(self._compare["px"])
                )
                compare_times = self._compare["time_s"][compare_mask]
                compare_positions = self._compare["px"][compare_mask]
                if compare_times.size < 2:
                    compare_times = compare_positions = None

            correlation_baseline_times = correlation_baseline_values = None
            correlation_compare_times = correlation_compare_values = None
            if self._baseline is not None and self._compare is not None:
                position_key = "mm" if self._use_mm() else "px"
                baseline_mask = (
                    np.isfinite(self._baseline["time_s"])
                    & np.isfinite(self._baseline[position_key])
                )
                compare_mask = (
                    np.isfinite(self._compare["time_s"])
                    & np.isfinite(self._compare[position_key])
                )
                if np.count_nonzero(baseline_mask) >= 2:
                    correlation_baseline_times = self._baseline["time_s"][
                        baseline_mask
                    ]
                    correlation_baseline_values = self._baseline[position_key][
                        baseline_mask
                    ]
                if np.count_nonzero(compare_mask) >= 2:
                    correlation_compare_times = self._compare["time_s"][
                        compare_mask
                    ]
                    correlation_compare_values = self._compare[position_key][
                        compare_mask
                    ]

            weighted_phase_samples = {}
            if (
                offset is not None
                and correlation_baseline_times is not None
                and correlation_compare_times is not None
            ):
                for phase in self._phase_ranges:
                    phase_key, weight = _phase_weight(phase["label"])
                    if phase_key is None or weight == 0:
                        continue
                    baseline_values, comparison_values = _aligned_phase_samples(
                        correlation_baseline_times,
                        correlation_baseline_values,
                        correlation_compare_times,
                        correlation_compare_values,
                        offset,
                        phase["start_s"],
                        phase["end_s"],
                    )
                    if baseline_values.size == 0:
                        continue
                    if phase_key in weighted_phase_samples:
                        old_weight, (old_baseline, old_comparison) = (
                            weighted_phase_samples[phase_key]
                        )
                        baseline_values = np.concatenate(
                            (old_baseline, baseline_values)
                        )
                        comparison_values = np.concatenate(
                            (old_comparison, comparison_values)
                        )
                        weight = old_weight
                    weighted_phase_samples[phase_key] = (
                        weight,
                        (baseline_values, comparison_values),
                    )
            overall_weighted_correlation = _weighted_correlation(
                weighted_phase_samples
            )

            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                for phase in self._phase_ranges:
                    baseline_stats = (
                        _peak_to_peak_stats(
                            self._baseline_extrema["rows"],
                            phase["start_s"],
                            phase["end_s"],
                        )
                        if self._baseline_extrema is not None
                        else None
                    )
                    baseline_period = (
                        _cycle_period_stats(
                            self._baseline_extrema["rows"],
                            phase["start_s"],
                            phase["end_s"],
                        )
                        if self._baseline_extrema is not None
                        else None
                    )
                    compare_start = (
                        phase["start_s"] - offset if offset is not None else None
                    )
                    compare_end = (
                        phase["end_s"] - offset if offset is not None else None
                    )
                    compare_stats = compare_period = None
                    coverage_start = coverage_end = coverage_pct = None
                    if (
                        compare_times is not None
                        and compare_start is not None
                        and compare_end is not None
                    ):
                        coverage_start = max(
                            phase["start_s"], float(compare_times[0] + offset)
                        )
                        coverage_end = min(
                            phase["end_s"], float(compare_times[-1] + offset)
                        )
                        if coverage_end > coverage_start:
                            coverage_pct = (
                                100.0
                                * (coverage_end - coverage_start)
                                / (phase["end_s"] - phase["start_s"])
                            )
                            in_range = (compare_times >= compare_start) & (
                                compare_times <= compare_end
                            )
                            compare_extrema = find_peak_to_peak_extrema(
                                compare_times[in_range],
                                compare_positions[in_range],
                            )
                            compare_stats = _peak_to_peak_stats(
                                compare_extrema,
                                compare_start,
                                compare_end,
                            )
                            compare_period = _cycle_period_stats(
                                compare_extrema,
                                compare_start,
                                compare_end,
                            )

                    baseline_p2p = (
                        baseline_stats["mean"]
                        if baseline_stats is not None
                        else None
                    )
                    compare_p2p = (
                        compare_stats["mean"]
                        if compare_stats is not None
                        else None
                    )
                    p2p_difference = (
                        compare_p2p - baseline_p2p
                        if compare_p2p is not None and baseline_p2p is not None
                        else None
                    )
                    baseline_cycles = (
                        baseline_stats["cycle_count"]
                        if baseline_stats is not None
                        else None
                    )
                    compare_cycles = (
                        compare_stats["cycle_count"]
                        if compare_stats is not None
                        else None
                    )
                    baseline_period_mean = (
                        baseline_period["mean"] if baseline_period is not None else None
                    )
                    compare_period_mean = (
                        compare_period["mean"] if compare_period is not None else None
                    )
                    period_difference = (
                        compare_period_mean - baseline_period_mean
                        if compare_period_mean is not None
                        and baseline_period_mean is not None
                        else None
                    )
                    cycles_difference = (
                        compare_cycles - baseline_cycles
                        if compare_cycles is not None and baseline_cycles is not None
                        else None
                    )
                    phase_correlation = None
                    if (
                        offset is not None
                        and correlation_baseline_times is not None
                        and correlation_compare_times is not None
                    ):
                        phase_baseline_values, phase_compare_values = (
                            _aligned_phase_samples(
                                correlation_baseline_times,
                                correlation_baseline_values,
                                correlation_compare_times,
                                correlation_compare_values,
                                offset,
                                phase["start_s"],
                                phase["end_s"],
                            )
                        )
                        phase_correlation = _pearson_correlation(
                            phase_baseline_values, phase_compare_values
                        )
                    _phase_key, phase_weight = _phase_weight(phase["label"])
                    confirmed_outlier_count = (
                        sum(
                            finding["tag"] == "strong_outlier"
                            and phase["start_s"] <= finding["time_s"] <= phase["end_s"]
                            for finding in self._outlier_rows
                        )
                        if self._baseline is not None and self._compare is not None
                        else ""
                    )
                    writer.writerow(
                        {
                            "baseline": (
                                self._baseline["name"]
                                if self._baseline is not None
                                else ""
                            ),
                            "compare": (
                                self._compare["name"]
                                if self._compare is not None
                                else ""
                            ),
                            "baseline_extrema": (
                                self._baseline_extrema["name"]
                                if self._baseline_extrema is not None
                                else ""
                            ),
                            "behavior": phase["label"],
                            "phase_offset_s": offset if offset is not None else "",
                            "baseline_start_s": phase["start_s"],
                            "baseline_end_s": phase["end_s"],
                            "compare_start_s": (
                                compare_start if compare_start is not None else ""
                            ),
                            "compare_end_s": (
                                compare_end if compare_end is not None else ""
                            ),
                            "compare_coverage_start_s": (
                                coverage_start if coverage_start is not None else ""
                            ),
                            "compare_coverage_end_s": (
                                coverage_end if coverage_end is not None else ""
                            ),
                            "compare_coverage_pct": (
                                coverage_pct if coverage_pct is not None else ""
                            ),
                            "expected_cycles": phase["expected_cycles"],
                            "baseline_cycles": (
                                baseline_cycles if baseline_cycles is not None else ""
                            ),
                            "compare_cycles": (
                                compare_cycles if compare_cycles is not None else ""
                            ),
                            "cycles_difference": (
                                compare_cycles - baseline_cycles
                                if compare_cycles is not None
                                and baseline_cycles is not None
                                else ""
                            ),
                            "baseline_p2p_mean_px": (
                                baseline_p2p if baseline_p2p is not None else ""
                            ),
                            "baseline_p2p_std_px": (
                                baseline_stats["std"]
                                if baseline_stats is not None
                                and baseline_stats["std"] is not None
                                else ""
                            ),
                            "baseline_p2p_samples": (
                                baseline_stats["sample_count"]
                                if baseline_stats is not None
                                else ""
                            ),
                            "compare_p2p_mean_px": (
                                compare_p2p if compare_p2p is not None else ""
                            ),
                            "compare_p2p_std_px": (
                                compare_stats["std"]
                                if compare_stats is not None
                                and compare_stats["std"] is not None
                                else ""
                            ),
                            "compare_p2p_samples": (
                                compare_stats["sample_count"]
                                if compare_stats is not None
                                else ""
                            ),
                            "p2p_difference_px": (
                                p2p_difference if p2p_difference is not None else ""
                            ),
                            "p2p_difference_pct": (
                                _relative_difference_pct(
                                    p2p_difference, baseline_p2p
                                )
                                if p2p_difference is not None
                                and baseline_p2p is not None
                                else ""
                            ),
                            "p2p_absolute_error_px": (
                                abs(p2p_difference)
                                if p2p_difference is not None
                                else ""
                            ),
                            "baseline_period_mean_s": (
                                baseline_period["mean"]
                                if baseline_period is not None
                                and baseline_period["mean"] is not None
                                else ""
                            ),
                            "baseline_period_std_s": (
                                baseline_period["std"]
                                if baseline_period is not None
                                and baseline_period["std"] is not None
                                else ""
                            ),
                            "compare_period_mean_s": (
                                compare_period["mean"]
                                if compare_period is not None
                                and compare_period["mean"] is not None
                                else ""
                            ),
                            "compare_period_std_s": (
                                compare_period["std"]
                                if compare_period is not None
                                and compare_period["std"] is not None
                                else ""
                            ),
                            "period_difference_s": (
                                period_difference
                                if period_difference is not None
                                else ""
                            ),
                            "period_difference_pct": (
                                _relative_difference_pct(
                                    period_difference, baseline_period_mean
                                )
                                if period_difference is not None
                                and baseline_period_mean is not None
                                else ""
                            ),
                            "period_absolute_error_s": (
                                abs(period_difference)
                                if period_difference is not None
                                else ""
                            ),
                            "cycles_absolute_error": (
                                abs(cycles_difference)
                                if cycles_difference is not None
                                else ""
                            ),
                            "cycles_difference_pct": (
                                _relative_difference_pct(
                                    cycles_difference, baseline_cycles
                                )
                                if cycles_difference is not None
                                and baseline_cycles is not None
                                else ""
                            ),
                            "confirmed_outlier_count": confirmed_outlier_count,
                            "phase_correlation": (
                                phase_correlation
                                if phase_correlation is not None
                                else ""
                            ),
                            "phase_weight": phase_weight,
                            "overall_weighted_correlation": (
                                overall_weighted_correlation
                                if overall_weighted_correlation is not None
                                else ""
                            ),
                            "phase_weighting": (
                                "Phase I=3; Phase II=2; Phase III=0; other=0"
                            ),
                            "correlation_method": (
                                "Pearson on compare values interpolated at baseline "
                                "times; Fisher-z aggregate weighted 3:2:0"
                            ),
                            "smooth": phase["smooth"],
                        }
                    )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self, "Export baseline ranges", f"Failed to save CSV:\n{exc}"
            )
            return
        QMessageBox.information(
            self,
            "Export baseline ranges",
            f"Saved {len(self._phase_ranges)} baseline ranges:\n{path}",
        )

    def _save_report_table(self):
        if not self._phase_ranges:
            QMessageBox.information(
                self, "Export report table", "There are no baseline ranges to report."
            )
            return

        start_dir = self._csv_start_dir()
        suggested = (
            os.path.join(start_dir, "comparison_report.docx")
            if start_dir
            else "comparison_report.docx"
        )
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export comparison report table",
            suggested,
            "Word documents (*.docx);;All files (*)",
        )
        if not path:
            return
        if not path.lower().endswith(".docx"):
            path += ".docx"

        offset = (
            float(self.offset_spin.value())
            if self._baseline is not None and self._compare is not None
            else None
        )
        phase_data = {}
        for phase in self._phase_ranges:
            label = phase["label"] or "Unlabeled phase"
            data = phase_data.setdefault(
                label,
                {
                    "ranges": [],
                    "baseline_amplitude_sum": 0.0,
                    "baseline_amplitude_count": 0,
                    "compare_amplitude_sum": 0.0,
                    "compare_amplitude_count": 0,
                    "baseline_period_sum": 0.0,
                    "baseline_period_count": 0,
                    "compare_period_sum": 0.0,
                    "compare_period_count": 0,
                    "baseline_cycles": 0,
                    "compare_cycles": 0,
                    "baseline_cycle_ranges": 0,
                    "compare_cycle_ranges": 0,
                    "baseline_samples": [],
                    "compare_samples": [],
                    "findings": [],
                    "coverage_incomplete": False,
                },
            )
            data["ranges"].append(phase)
            start_s = phase["start_s"]
            end_s = phase["end_s"]

            if self._baseline_extrema is not None:
                baseline_extrema = self._baseline_extrema["rows"]
                baseline_stats = _peak_to_peak_stats(
                    baseline_extrema, start_s, end_s
                )
                if baseline_stats["mean"] is not None:
                    data["baseline_amplitude_sum"] += (
                        baseline_stats["mean"] * baseline_stats["sample_count"]
                    )
                    data["baseline_amplitude_count"] += baseline_stats["sample_count"]
                data["baseline_cycles"] += baseline_stats["cycle_count"]
                data["baseline_cycle_ranges"] += 1
                baseline_period = _cycle_period_stats(
                    baseline_extrema, start_s, end_s
                )
                if baseline_period["mean"] is not None:
                    data["baseline_period_sum"] += (
                        baseline_period["mean"] * baseline_period["sample_count"]
                    )
                    data["baseline_period_count"] += baseline_period["sample_count"]

            if self._compare is not None and offset is not None:
                compare_mask = (
                    np.isfinite(self._compare["time_s"])
                    & np.isfinite(self._compare["px"])
                )
                compare_times = self._compare["time_s"][compare_mask]
                compare_positions = self._compare["px"][compare_mask]
                if compare_times.size >= 2:
                    compare_start = start_s - offset
                    compare_end = end_s - offset
                    in_range = (
                        (compare_times >= compare_start)
                        & (compare_times <= compare_end)
                    )
                    compare_extrema = find_peak_to_peak_extrema(
                        compare_times[in_range], compare_positions[in_range]
                    )
                    compare_stats = _peak_to_peak_stats(
                        compare_extrema, compare_start, compare_end
                    )
                    if compare_stats["mean"] is not None:
                        data["compare_amplitude_sum"] += (
                            compare_stats["mean"] * compare_stats["sample_count"]
                        )
                        data["compare_amplitude_count"] += (
                            compare_stats["sample_count"]
                        )
                    data["compare_cycles"] += compare_stats["cycle_count"]
                    data["compare_cycle_ranges"] += 1
                    compare_period = _cycle_period_stats(
                        compare_extrema, compare_start, compare_end
                    )
                    if compare_period["mean"] is not None:
                        data["compare_period_sum"] += (
                            compare_period["mean"] * compare_period["sample_count"]
                        )
                        data["compare_period_count"] += (
                            compare_period["sample_count"]
                        )
                    coverage = (
                        max(start_s, float(compare_times[0] + offset)),
                        min(end_s, float(compare_times[-1] + offset)),
                    )
                    if coverage[1] - coverage[0] < end_s - start_s:
                        data["coverage_incomplete"] = True

            if self._baseline is not None and self._compare is not None:
                position_key = "mm" if self._use_mm() else "px"
                baseline_mask = (
                    np.isfinite(self._baseline["time_s"])
                    & np.isfinite(self._baseline[position_key])
                )
                compare_mask = (
                    np.isfinite(self._compare["time_s"])
                    & np.isfinite(self._compare[position_key])
                )
                baseline_times = self._baseline["time_s"][baseline_mask]
                baseline_values = self._baseline[position_key][baseline_mask]
                compare_times = self._compare["time_s"][compare_mask]
                compare_values = self._compare[position_key][compare_mask]
                if baseline_times.size >= 2 and compare_times.size >= 2:
                    paired_baseline, paired_compare = _aligned_phase_samples(
                        baseline_times,
                        baseline_values,
                        compare_times,
                        compare_values,
                        offset,
                        start_s,
                        end_s,
                    )
                    data["baseline_samples"].append(paired_baseline)
                    data["compare_samples"].append(paired_compare)

                data["findings"].extend(
                    finding
                    for finding in self._outlier_rows
                    if finding["tag"] == "strong_outlier"
                    and start_s <= finding["time_s"] <= end_s
                )

        overall_samples = {}
        report_sections = []
        for label, data in phase_data.items():
            try:
                phase_plot = _phase_waveform_figure(
                    self._baseline,
                    self._compare,
                    data["ranges"],
                    offset,
                    self._use_mm(),
                )
            except (ValueError, RuntimeError) as exc:
                QMessageBox.warning(
                    self,
                    "Export report table",
                    f"Failed to create waveform graph for {label}:\n{exc}",
                )
                return
            baseline_amplitude = (
                data["baseline_amplitude_sum"] / data["baseline_amplitude_count"]
                if data["baseline_amplitude_count"]
                else None
            )
            compare_amplitude = (
                data["compare_amplitude_sum"] / data["compare_amplitude_count"]
                if data["compare_amplitude_count"]
                else None
            )
            baseline_period = (
                data["baseline_period_sum"] / data["baseline_period_count"]
                if data["baseline_period_count"]
                else None
            )
            compare_period = (
                data["compare_period_sum"] / data["compare_period_count"]
                if data["compare_period_count"]
                else None
            )
            baseline_cycles = (
                data["baseline_cycles"]
                if data["baseline_cycle_ranges"]
                else None
            )
            compare_cycles = (
                data["compare_cycles"]
                if data["compare_cycle_ranges"]
                else None
            )
            baseline_samples = (
                np.concatenate(data["baseline_samples"])
                if data["baseline_samples"]
                else np.array([], dtype=float)
            )
            compare_samples = (
                np.concatenate(data["compare_samples"])
                if data["compare_samples"]
                else np.array([], dtype=float)
            )
            correlation = _pearson_correlation(baseline_samples, compare_samples)
            phase_key, weight = _phase_weight(label)
            if phase_key is not None and weight > 0 and baseline_samples.size:
                if phase_key in overall_samples:
                    old_weight, (old_baseline, old_compare) = overall_samples[
                        phase_key
                    ]
                    baseline_samples = np.concatenate(
                        (old_baseline, baseline_samples)
                    )
                    compare_samples = np.concatenate((old_compare, compare_samples))
                    weight = old_weight
                overall_samples[phase_key] = (
                    weight,
                    (baseline_samples, compare_samples),
                )

            confirmed = []
            point_findings = sorted(
                (
                    finding
                    for finding in data["findings"]
                    if finding["finding_type"] == "point_residual"
                ),
                key=lambda finding: finding["time_s"],
            )
            if self._compare is not None:
                valid_compare_times = self._compare["time_s"][
                    np.isfinite(self._compare["time_s"])
                    & np.isfinite(self._compare["px"])
                ]
                sample_interval = (
                    float(np.median(np.diff(valid_compare_times)))
                    if valid_compare_times.size > 1
                    else 0.0
                )
            else:
                sample_interval = 0.0
            gap_limit = 1.5 * sample_interval
            previous_time = None
            for finding in point_findings:
                finding_time = finding["time_s"]
                if previous_time is None or finding_time - previous_time > gap_limit:
                    confirmed.append(finding)
                previous_time = finding_time
            confirmed.extend(
                finding
                for finding in data["findings"]
                if finding["finding_type"] != "point_residual"
            )
            outlier_count = len(confirmed) if self._compare is not None else None

            amplitude_difference = (
                compare_amplitude - baseline_amplitude
                if compare_amplitude is not None and baseline_amplitude is not None
                else None
            )
            period_difference = (
                compare_period - baseline_period
                if compare_period is not None and baseline_period is not None
                else None
            )
            cycle_difference = (
                compare_cycles - baseline_cycles
                if compare_cycles is not None and baseline_cycles is not None
                else None
            )
            amplitude_pct = _relative_difference_pct(
                amplitude_difference, baseline_amplitude
            )
            period_pct = _relative_difference_pct(period_difference, baseline_period)
            cycle_pct = _relative_difference_pct(cycle_difference, baseline_cycles)

            def format_value(value, suffix="", digits=3):
                if value is None:
                    return "—"
                return f"{value:.{digits}f}{suffix}"

            def comparison_value(value, difference, percent, suffix="", digits=3):
                if value is None:
                    return "—"
                result = format_value(value, suffix, digits)
                if difference is not None:
                    result += (
                        f" (Δ {difference:+.{digits}f}{suffix}, "
                        f"{percent:+.1f}%)"
                        if percent is not None
                        else f" (Δ {difference:+.{digits}f}{suffix})"
                    )
                return result

            baseline_cell = (
                format_value(baseline_amplitude, " px")
                if baseline_amplitude is not None
                else "—"
            )
            compare_cell = comparison_value(
                compare_amplitude, amplitude_difference, amplitude_pct, " px"
            )
            rows = [
                (
                    "Mean pk-pk (px)",
                    baseline_cell,
                    compare_cell,
                ),
                (
                    "Period (s)",
                    format_value(baseline_period, " s"),
                    comparison_value(
                        compare_period, period_difference, period_pct, " s"
                    ),
                ),
                (
                    "Number of cycles",
                    format_value(baseline_cycles, digits=0),
                    comparison_value(
                        compare_cycles, cycle_difference, cycle_pct, digits=0
                    ),
                ),
                (
                    "Number of confirmed outliers",
                    "Reference" if self._baseline is not None else "—",
                    format_value(outlier_count, digits=0),
                ),
                (
                    "Correlation score",
                    "Reference" if self._baseline is not None else "—",
                    format_value(correlation),
                ),
            ]
            notes = []
            if self._baseline_extrema is None:
                notes.append(
                    "Load baseline peak-to-peak extrema to calculate amplitude, "
                    "period, and baseline cycle measurements."
                )
            if self._compare is None:
                notes.append("Load a comparison tracking CSV to calculate comparison results.")
            elif data["coverage_incomplete"]:
                notes.append(
                    "Comparison coverage does not span the full selected baseline range."
                )
            if phase_key == "phase_3":
                notes.append(
                    "Phase III is excluded from the overall weighted correlation."
                )
            report_sections.append((label, rows, notes, phase_plot))

        overall_correlation = _weighted_correlation(overall_samples)
        overall_text = (
            f"{overall_correlation:.3f}"
            if overall_correlation is not None
            else "Not available (insufficient paired, varying samples)"
        )
        baseline_name = (
            self._baseline["name"] if self._baseline is not None else "Not loaded"
        )
        compare_name = (
            self._compare["name"] if self._compare is not None else "Not loaded"
        )
        try:
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            document = Document()
            title = document.add_heading("Waveform Comparison Report", level=1)
            title.alignment = WD_ALIGN_PARAGRAPH.CENTER
            metadata = document.add_paragraph()
            metadata.add_run("Baseline: ").bold = True
            metadata.add_run(baseline_name)
            metadata.add_run("    Comparison: ").bold = True
            metadata.add_run(compare_name)
            metadata.add_run("    Phase offset: ").bold = True
            metadata.add_run(
                format_value(offset, " s") if offset is not None else "—"
            )

            for label, rows, notes, phase_plot in report_sections:
                heading = document.add_heading(f"{label} table", level=2)
                heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
                table = document.add_table(rows=1, cols=3)
                table.style = "Table Grid"
                table.alignment = WD_TABLE_ALIGNMENT.CENTER
                table.autofit = True
                header_cells = table.rows[0].cells
                for cell, text in zip(
                    header_cells,
                    ("Measurement differences", "Baseline", "Comparison"),
                ):
                    cell.text = text
                    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                    for run in cell.paragraphs[0].runs:
                        run.bold = True
                    shading = OxmlElement("w:shd")
                    shading.set(qn("w:fill"), "F2F2F2")
                    cell._tc.get_or_add_tcPr().append(shading)

                for metric, baseline_value, compare_value in rows:
                    cells = table.add_row().cells
                    for cell, text in zip(
                        cells, (metric, baseline_value, compare_value)
                    ):
                        cell.text = str(text)
                        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER

                if phase_plot is not None:
                    caption = document.add_paragraph(
                        f"{label} waveform comparison "
                        "(time shown relative to the start of each selected range)"
                    )
                    caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    document.add_picture(phase_plot, width=Inches(6.3))
                    phase_plot.close()

                notes_paragraph = document.add_paragraph()
                notes_paragraph.add_run(
                    f"NSI quality assurance notes ({label}): "
                ).bold = True
                notes_paragraph.add_run(
                    " ".join(notes) if notes else "____________________________"
                )

            method = document.add_paragraph()
            method.add_run("Overall weighted correlation: ").bold = True
            method.add_run(
                f"{overall_text}. Pearson correlation is calculated per phase "
                "from aligned samples. The configured weights express relative "
                "trust in recorded points, not cycle counts. Comparison "
                "differences are relative to the baseline reference. Amplitude "
                "values are in pixels."
            )
            document.save(path)
        except OSError as exc:
            QMessageBox.warning(
                self, "Export report table", f"Failed to save report:\n{exc}"
            )
            return
        QMessageBox.information(
            self, "Export report table", f"Saved report table:\n{path}"
        )

    def _use_mm(self):
        series = [item for item in (self._baseline, self._compare) if item is not None]
        if not series:
            return False
        return all(np.isfinite(item["mm"]).any() for item in series)

    def _align_and_redraw(self, estimate_offset):
        self._outlier_rows = []
        self.review_plot.setData([], [])
        self.strong_outlier_plot.setData([], [])
        use_mm = self._use_mm()
        unit = "mm" if use_mm else "px"
        self.plot.setLabel("left", f"Position ({unit}, mean subtracted)")

        prepared = {}
        try:
            if self._baseline is not None:
                prepared["baseline"] = _series_position(self._baseline, use_mm)
                if np.isfinite(self._baseline["px"]).any():
                    prepared["baseline_px"] = _series_position(self._baseline, False)
            if self._compare is not None:
                prepared["compare"] = _series_position(self._compare, use_mm)
                if np.isfinite(self._compare["px"]).any():
                    prepared["compare_px"] = _series_position(self._compare, False)
        except Exception as exc:
            QMessageBox.warning(self, "Compare", str(exc))
            return

        if "baseline" in prepared:
            t_b, raw_y_b, mean_b = prepared["baseline"]
            y_b = _smooth_phase_ranges(
                t_b,
                raw_y_b,
                self._phase_ranges,
                baseline_extrema=(
                    self._baseline_extrema["rows"]
                    if self._baseline_extrema is not None and not use_mm
                    else None
                ),
            )
            self.baseline_curve.setData(x=t_b, y=raw_y_b)
            self.smoothed_baseline_curve.setData(x=t_b, y=y_b)
        else:
            self.baseline_curve.setData([], [])
            self.smoothed_baseline_curve.setData([], [])
            t_b = y_b = None
            raw_y_b = None
            mean_b = None

        both = "baseline" in prepared and "compare" in prepared
        has_smoothed_ranges = any(
            phase["smooth"] for phase in self._phase_ranges
        )
        self._update_curve_visibility()
        self.save_smoothed_btn.setEnabled(
            self._baseline is not None and has_smoothed_ranges
        )
        self.offset_spin.setEnabled(both)
        if both and estimate_offset:
            t_c, y_c, _mean_c = prepared["compare"]
            offset = estimate_phase_offset(t_b, raw_y_b, t_c, y_c)
            self._setting_offset = True
            self.offset_spin.setValue(offset)
            self._setting_offset = False

        offset = float(self.offset_spin.value()) if both else 0.0
        if "compare" in prepared:
            t_c, y_c, mean_c = prepared["compare"]
            self.compare_curve.setData(x=t_c + offset, y=y_c)
        else:
            self.compare_curve.setData([], [])
            t_c = y_c = None
            mean_c = None

        if both:
            sensitivity = float(self.sensitivity_spin.value())
            (
                point_times,
                point_compare,
                point_baseline,
                point_residuals,
                review_mask,
                strong_mask,
            ) = comparison_outliers(
                t_b, raw_y_b, t_c, y_c, offset
            )
            review_x = list(point_times[review_mask])
            review_y = list(point_compare[review_mask])
            strong_x = list(point_times[strong_mask])
            strong_y = list(point_compare[strong_mask])
            self._outlier_rows = [
                {
                    "finding_type": "point_residual",
                    "phase": "",
                    "time_s": float(point_times[i]),
                    "expected": "",
                    "observed": "",
                    "ratio": "",
                    "baseline_pos": float(point_baseline[i]),
                    "compare_pos": float(point_compare[i]),
                    "residual": float(point_residuals[i]),
                    "tag": "strong_outlier" if strong_mask[i] else "review",
                    "details": (
                        "Large point residual persists across neighboring samples."
                        if strong_mask[i]
                        else "Point residual exceeds the local baseline review tolerance."
                    ),
                }
                for i in np.flatnonzero(review_mask | strong_mask)
            ]
            behavior_findings = []
            if (
                "baseline_px" in prepared
                and "compare_px" in prepared
                and self._baseline_extrema is not None
            ):
                behavior_findings = behavior_outliers(
                    prepared["baseline_px"][0],
                    prepared["baseline_px"][1],
                    prepared["compare_px"][0],
                    prepared["compare_px"][1],
                    offset,
                    self._phase_ranges,
                    baseline_extrema=self._baseline_extrema["rows"],
                    n_sigmas=sensitivity,
                )
            self._outlier_rows.extend(behavior_findings)
            plotted_findings = [
                row
                for row in behavior_findings
                if row["finding_type"] == "peak_to_peak_amplitude"
            ]
            for row in plotted_findings:
                point_time = row["time_s"]
                point_position = float(np.interp(point_time - offset, t_c, y_c))
                if row["tag"] == "strong_outlier":
                    strong_x.append(point_time)
                    strong_y.append(point_position)
                else:
                    review_x.append(point_time)
                    review_y.append(point_position)
            self.review_plot.setData(x=review_x, y=review_y)
            self.strong_outlier_plot.setData(x=strong_x, y=strong_y)
        self.save_btn.setEnabled(both)
        self.plot.enableAutoRange()
        self._set_status(
            use_mm, mean_b, mean_c, offset if both else None, self._outlier_rows
        )

    def _set_status(self, use_mm, mean_b, mean_c, offset, findings):
        unit = "mm" if use_mm else "px"
        parts = []
        if self._baseline is not None and mean_b is not None:
            parts.append(
                f"Baseline {self._baseline['name']}: subtracted mean {mean_b:.3f} {unit}"
            )
        if self._compare is not None and mean_c is not None:
            parts.append(
                f"Compare {self._compare['name']}: subtracted mean {mean_c:.3f} {unit}"
            )
        if offset is not None:
            cycle_findings = [
                row
                for row in findings
                if row["finding_type"] in ("cycle_count", "baseline_cycle_count")
            ]
            amplitude_findings = sum(
                row["finding_type"] == "peak_to_peak_amplitude"
                for row in findings
            )
            point_review = sum(
                row["finding_type"] == "point_residual" and row["tag"] == "review"
                for row in findings
            )
            point_strong = sum(
                row["finding_type"] == "point_residual"
                and row["tag"] == "strong_outlier"
                for row in findings
            )
            parts.append(
                f"Phase offset {offset:.4f} s. Point flags: "
                f"{point_strong} strong, {point_review} review; "
                f"phase findings: "
                f"{len(cycle_findings) + amplitude_findings}"
            )
            parts.extend(row["details"] for row in cycle_findings)
        if not self._phase_ranges and self._baseline is not None:
            parts.append(
                "Select and add baseline behavior ranges to compare cycle counts "
                "and peak-to-peak amplitudes."
            )
        if self._phase_ranges and self._baseline_extrema is None:
            parts.append(
                "Load the baseline peak-to-peak CSV to calculate tagged cycle "
                "counts and amplitude mean/std for each range."
            )
        elif (
            self._phase_ranges
            and self._baseline_extrema is not None
            and self._compare is not None
            and (
                not np.isfinite(self._baseline["px"]).any()
                or not np.isfinite(self._compare["px"]).any()
            )
        ):
            parts.append(
                "Peak-to-peak comparisons require pixel-position columns in both "
                "tracking CSVs."
            )
        self.status.setText(" | ".join(parts) if parts else "Load a baseline CSV and a compare CSV.")

    def _save_outliers(self):
        if not self._outlier_rows:
            QMessageBox.information(
                self, "Save outliers", "No comparison outliers to save."
            )
            return
        start = self._csv_start_dir()
        suggested = os.path.join(start, "comparison_outliers.csv") if start else "comparison_outliers.csv"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save outliers CSV",
            suggested,
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        fieldnames = [
            "finding_type",
            "phase",
            "time_s",
            "expected",
            "observed",
            "ratio",
            "baseline_pos",
            "compare_pos",
            "residual",
            "tag",
            "details",
        ]
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self._outlier_rows)
        QMessageBox.information(
            self,
            "Save outliers",
            f"Saved {len(self._outlier_rows)} outlier rows:\n{path}",
        )

    def _save_smoothed_baseline(self):
        if self._baseline is None:
            return
        use_mm = self._use_mm()
        position_key = "mm" if use_mm else "px"
        values = self._baseline[position_key]
        mask = np.isfinite(self._baseline["time_s"]) & np.isfinite(values)
        times = self._baseline["time_s"][mask]
        raw_values = values[mask]
        mean = float(np.mean(raw_values))
        centered = raw_values - mean
        smoothed = (
            _smooth_phase_ranges(
                times,
                centered,
                self._phase_ranges,
                baseline_extrema=(
                    self._baseline_extrema["rows"]
                    if self._baseline_extrema is not None and not use_mm
                    else None
                ),
            )
            + mean
        )
        start = self._csv_start_dir()
        suggested = (
            os.path.join(start, f"{self._baseline['name']}_smoothed.csv")
            if start
            else f"{self._baseline['name']}_smoothed.csv"
        )
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save smoothed baseline CSV",
            suggested,
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "time_s",
                    f"original_position_{position_key}",
                    f"smoothed_position_{position_key}",
                ]
            )
            writer.writerows(zip(times, raw_values, smoothed))
        QMessageBox.information(
            self,
            "Save smoothed baseline",
            f"Saved {len(times)} position samples:\n{path}",
        )
