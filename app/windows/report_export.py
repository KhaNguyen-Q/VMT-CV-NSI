"""Export comparison reports from the CompareWindow data and controls."""

import json
import os
from io import BytesIO

import matplotlib

matplotlib.use("Agg")
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
import numpy as np
import pandas as pd
from PyQt5.QtWidgets import QDialog, QFileDialog, QMessageBox, QPushButton

from windows.NSIReportformat import format_value, save_report_json, save_reports
from windows.range_csv_export import tagged_cycle_count
from windows.report_metadata_dialog import ReportMetadataDialog
try:
    from analysis.comparator import compare_observed_value
    from analysis.loader import NUMERIC_COLUMNS
except ModuleNotFoundError:
    from app.analysis.comparator import compare_observed_value
    from app.analysis.loader import NUMERIC_COLUMNS

_PX_TO_MM = 1.0 / 10.0


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
    return {
        "rows": rows,
        "cycle_count": tagged_cycle_count(extrema, start_s, end_s),
        "sample_count": int(amplitudes.size),
        "amplitudes": amplitudes.tolist(),
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
        "periods": periods.tolist(),
        "mean": float(np.mean(periods)) if periods.size else None,
        "std": (
            float(np.std(periods, ddof=1 if periods.size > 1 else 0))
            if periods.size
            else None
        ),
        "sample_count": int(periods.size),
    }

def _statistical_range_rows(window, phase_label, measurements):
   """Build report rows from the selected statistical comparator baseline."""
   selection = getattr(window, "_baseline_selection", None)
   if not selection:
       return []

   rows = []
   for label, measurement, observed, suffix, digits in measurements:
       try:
           result = compare_observed_value(
               selection["config_path"],
               selection["treatment_profile"],
               "phase_population",
               measurement,
               observed,
               phase=phase_label,
               record_type="Phase Summary",
               vibration="All Vibrations",
               range_selection_method=selection["range_selection_method"],
           )
       except (OSError, ValueError, KeyError, TypeError) as exc:
           rows.append(
               (
                   f"Statistical baseline—{label}",
                   "Not available",
                   f"INCONCLUSIVE ({exc})",
               )
           )
           continue

       if result["status"] == "INCONCLUSIVE":
           rows.append(
               (
                   f"Statistical baseline—{label}",
                   "Not available",
                   "INCONCLUSIVE",
               )
           )
           continue
       rows.append(
           (
               f"Statistical baseline—{label}",
               f"[{result['lower']:.{digits}f}, "
               f"{result['upper']:.{digits}f}] {suffix}",
               f"{result['observed']:.{digits}f}{suffix} — {result['status']}",
           )
       )
   return rows


def _evaluate_cycle_checks(phase_key, cycle_checks):
    baseline_counts = [
        check["baseline"]
        for check in cycle_checks
        if check["baseline"] is not None
    ]
    phase2_mean = (
        float(np.mean(baseline_counts))
        if phase_key == "phase_2" and baseline_counts
        else None
    )
    phase2_std = (
        float(np.std(baseline_counts, ddof=1))
        if phase_key == "phase_2" and len(baseline_counts) >= 2
        else None
    )
    results = []
    for check in cycle_checks:
        result = {
            "check": check,
            "status": "INCONCLUSIVE",
            "expected": None,
            "difference": None,
            "deviations": None,
            "percent_difference": None,
            "baseline_mean": phase2_mean,
            "baseline_std": phase2_std,
        }
        observed = check["observed"]
        if phase_key == "phase_1":
            expected = check["expected"]
            result["expected"] = expected
            if expected is not None and observed is not None:
                result["difference"] = abs(observed - expected)
                result["status"] = (
                    "REVIEW" if result["difference"] >= 2 else "PASS"
                )
        elif phase_key == "phase_2":
            if phase2_mean is not None:
                if observed is not None:
                    difference = abs(observed - phase2_mean)
                    deviations = (
                        None
                        if phase2_std is None
                        else 0.0
                        if difference == 0
                        else float("inf")
                        if phase2_std == 0
                        else difference / phase2_std
                    )
                    percent_difference = (
                        0.0
                        if difference == 0
                        else float("inf")
                        if phase2_mean == 0
                        else difference / abs(phase2_mean) * 100
                    )
                    result["difference"] = difference
                    result["deviations"] = deviations
                    result["percent_difference"] = percent_difference
                    result["status"] = (
                        "FAIL"
                        if difference >= 20
                        else "REVIEW"
                        if percent_difference >= 20
                        else "PASS"
                    )
            if check["baseline"] is None:
                result["status"] = "INCONCLUSIVE"
        results.append(result)

    unresolved = []
    if phase_key == "phase_2" and len(baseline_counts) < 2:
        unresolved.append(
            "Phase 2 cycle counts: at least two selected baseline ranges are "
            "needed to calculate the baseline mean and sample standard deviation."
        )
    for result in results:
        check = result["check"]
        if phase_key == "phase_1" and result["expected"] is None:
            unresolved.append(
                f"Phase 1 range {check['start_s']:.3f}-{check['end_s']:.3f} s: "
                "could not determine the expected cycle count."
            )
        elif check["observed"] is None:
            unresolved.append(
                f"{phase_key.replace('_', ' ').title()} range "
                f"{check['start_s']:.3f}-{check['end_s']:.3f} s: "
                "could not determine the comparison cycle count."
            )
        if phase_key == "phase_2" and check["baseline"] is None:
            unresolved.append(
                f"Phase 2 range {check['start_s']:.3f}-{check['end_s']:.3f} s: "
                "could not determine the baseline cycle count."
            )
    return results, unresolved

def _assess_baseline_specifications(
    phase_data, compare_loaded, sigma=3.0
):
    failures = []
    reviews = []
    unresolved = []
    if not compare_loaded:
        return "INCONCLUSIVE", ["Comparison tracking data was not loaded."]

    for label, data in phase_data.items():
        _phase_key, weight = _phase_weight(label)
        if weight <= 0:
            continue
        cycle_checks = data["cycle_checks"]
        cycle_results, cycle_unresolved = _evaluate_cycle_checks(
            _phase_key, cycle_checks
        )
        unresolved.extend(cycle_unresolved)
        for result in cycle_results:
            check = result["check"]
            observed = check["observed"]
            if _phase_key == "phase_1" and result["expected"] is not None:
                if observed is None:
                    continue
                cycle_detail = (
                    f"{label} range {check['start_s']:.3f}-{check['end_s']:.3f} s: "
                    f"{observed} comparison cycles vs expected "
                    f"{result['expected']} (difference "
                    f"{result['difference']})."
                )
            elif (
                _phase_key == "phase_2"
                and result["baseline_mean"] is not None
                and observed is not None
            ):
                cycle_detail = (
                    f"{label} range {check['start_s']:.3f}-{check['end_s']:.3f} s: "
                    f"{observed} comparison cycles; baseline mean "
                    f"{result['baseline_mean']:.3g}; difference "
                    f"{result['difference']:.2f} cycles "
                    f"({result['percent_difference']:.1f}% of baseline mean)."
                )
                if result["status"] == "FAIL":
                    cycle_detail += " Phase 2 cycle-count difference of at least 20 fails."
            else:
                continue

            if result["status"] == "FAIL":
                failures.append(cycle_detail)
            elif result["status"] == "REVIEW":
                reviews.append(cycle_detail)

        for metric, unit, baseline_values, compare_values in (
            (
                "peak-to-peak amplitude",
                "px",
                data["baseline_amplitudes"],
                data["compare_amplitudes"],
            ),
            (
                "period",
                "s",
                data["baseline_periods"],
                data["compare_periods"],
            ),
        ):
            if _phase_key == "phase_1":
                baseline_values = _boxplot_inliers(baseline_values)
                compare_values = _boxplot_inliers(compare_values)
            if len(baseline_values) < 2:
                unresolved.append(
                    f"{label} {metric}: at least two baseline measurements are "
                    "needed to calculate a standard deviation."
                )
                continue
            if len(compare_values) == 0:
                unresolved.append(
                    f"{label} {metric}: no comparison measurements were available."
                )
                continue

            baseline_mean = float(np.mean(baseline_values))
            baseline_std = float(np.std(baseline_values, ddof=1))
            compare_mean = float(np.mean(compare_values))
            lower = baseline_mean - sigma * baseline_std
            upper = baseline_mean + sigma * baseline_std
            metric_detail = (
                f"{label} mean {metric}: comparison {compare_mean:.4g} {unit}; "
                f"allowed {lower:.4g} to {upper:.4g} {unit} based on baseline "
                f"mean {baseline_mean:.4g}, SD {baseline_std:.4g}, "
                f"n={len(baseline_values)}."
            )
            if compare_mean < lower or compare_mean > upper:
                failures.append(metric_detail)

        if data["coverage_incomplete"]:
            unresolved.append(
                f"{label} comparison coverage does not span all selected ranges."
            )

    if not any(_phase_weight(label)[1] > 0 for label in phase_data):
        unresolved.append(
            "No Phase 1 or Phase 2 ranges were selected for assessment."
        )

    if failures:
        return "FAIL", failures
    if unresolved:
        return "INCONCLUSIVE", reviews + unresolved
    if reviews:
        return "REVIEW", reviews
    return "PASS", [_client_assessment_description("PASS")]

def _client_assessment_description(assessment):
    descriptions = {
        "PASS": (
            "The quality assurance results indicate that the SONIK device is "
            "working as intended. The observed performance was consistent with "
            "the expected behavior for the selected tests."
        ),
        "REVIEW": (
            "The results are satisfactory overall, although some observations "
            "may benefit from additional review. Please contact NSI for more "
            "information."
        ),
        "FAIL": (
            "The results indicate that the device is non-conforming with one or "
            "more expected performance criteria. Please contact NSI to discuss "
            "the findings."
        ),
        "INCONCLUSIVE": (
            "The available results were not sufficient to determine device "
            "performance. Please ensure the test instructions were followed "
            "thoroughly, then contact NSI for more information."
        ),
    }
    return descriptions.get(
        assessment,
        "The available results could not be interpreted. Please contact NSI "
        "for more information.",
    )

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

def _phase_waveform_figure(
    baseline,
    compare,
    ranges,
    offset,
    use_mm,
    client_facing=False,
    px_to_mm=False,
):
    """Render each selected range against baseline-relative elapsed time."""
    if baseline is None:
        return None

    if px_to_mm:
        unit = "mm"

        def position(series):
            if np.isfinite(series["px"]).any():
                times, values, _ = _series_position(series, False)
                return times, values * _PX_TO_MM, None
            return _series_position(series, True)

    else:
        unit = "mm" if use_mm else "px"

        def position(series):
            return _series_position(series, use_mm)

    baseline_times, baseline_values, _ = position(baseline)
    compare_data = None
    if compare is not None:
        compare_times, compare_values, _ = position(compare)
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
    range_color_pairs = (
        ("#1f77b4", "#ff7f0e"),
        ("#2ca02c", "#d62728"),
        ("#9467bd", "#8c564b"),
        ("#e377c2", "#7f7f7f"),
        ("#bcbd22", "#17becf"),
    )

    for index, phase in enumerate(ranges, start=1):
        baseline_color, compare_color = range_color_pairs[
            (index - 1) % len(range_color_pairs)
        ]
        start_s = phase["start_s"]
        end_s = phase["end_s"]
        duration = end_s - start_s
        label = (
            "Selected range"
            if client_facing
            else f"Range {index} ({start_s:.3f}–{end_s:.3f} s)"
        )
        baseline_mask = (
            (baseline_times >= start_s) & (baseline_times <= end_s)
        )
        baseline_x = baseline_times[baseline_mask] - start_s
        baseline_y = baseline_values[baseline_mask]
        if baseline_x.size:
            overlay_axis.plot(
                baseline_x,
                baseline_y,
                color=baseline_color,
                linewidth=1.25,
                label=f"Baseline — {label}",
            )
            baseline_axis.plot(
                baseline_x,
                baseline_y,
                color=baseline_color,
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
                    color=compare_color,
                    linewidth=1.15,
                    alpha=0.9,
                    label=f"Comparison — {label}",
                )
                compare_axis.plot(
                    compare_x,
                    compare_y,
                    color=compare_color,
                    linewidth=1.15,
                    label=label,
                )

    for axis in (overlay_axis, baseline_axis, compare_axis):
        axis.set_xlim(0, max(max_duration, 1e-6))

    overlay_axis.set_title("Waveform overlay")
    axis_label = (
        f"Displacement ({unit})"
        if px_to_mm
        else "Relative displacement"
        if client_facing
        else f"Displacement ({unit}, mean-subtracted)"
    )
    overlay_axis.set_ylabel(axis_label)
    if client_facing:
        overlay_axis.grid(False)
    else:
        overlay_axis.grid(True, alpha=0.25)
    if not client_facing:
        overlay_axis.legend(loc="best", fontsize="small")

    baseline_axis.set_title("Baseline")
    baseline_axis.set_ylabel(axis_label)
    baseline_axis.set_xlabel(
        "Relative time" if client_facing else "Elapsed time from range start (s)"
    )
    if client_facing:
        baseline_axis.grid(False)
    else:
        baseline_axis.grid(True, alpha=0.25)
    if baseline_axis.lines and not client_facing:
        baseline_axis.legend(loc="best", fontsize="small")

    compare_axis.set_title("Comparison")
    compare_axis.set_ylabel(axis_label)
    compare_axis.set_xlabel(
        "Relative time" if client_facing else "Elapsed time from range start (s)"
    )
    if client_facing:
        compare_axis.grid(False)
    else:
        compare_axis.grid(True, alpha=0.25)
    if compare_axis.lines:
        if not client_facing:
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

    if client_facing and not px_to_mm:
        for axis in (overlay_axis, baseline_axis, compare_axis):
            axis.set_xticks([])
            axis.set_yticks([])
    elif client_facing:
        for axis in (overlay_axis, baseline_axis, compare_axis):
            axis.set_xticks([])

    figure.suptitle("Selected-range waveform comparison")
    output = BytesIO()
    canvas.print_png(output)
    figure.clear()
    output.seek(0)
    return output

def _phase_boxplot_figure(
    label,
    baseline_amplitudes,
    compare_amplitudes,
    baseline_periods,
    compare_periods,
):
    figure = Figure(figsize=(7.2, 3.2), constrained_layout=True)
    canvas = FigureCanvasAgg(figure)
    amplitude_axis, period_axis = figure.subplots(1, 2)

    for axis, title, baseline_values, compare_values, unit in (
        (
            amplitude_axis,
            "Peak-to-peak px",
            baseline_amplitudes,
            compare_amplitudes,
            "px",
        ),
        (period_axis, "Cycle period", baseline_periods, compare_periods, "time (s)"),
    ):
        groups = []
        labels = []
        for series_label, values in (
            ("Baseline", baseline_values),
            ("Comparison", compare_values),
        ):
            values = np.asarray(values, dtype=float)
            values = values[np.isfinite(values)]
            if values.size:
                groups.append(values)
                labels.append(series_label)
        axis.set_title(title)
        axis.set_ylabel(unit)
        axis.grid(axis="y", alpha=0.25)
        if groups:
            axis.boxplot(
                groups,
                tick_labels=labels,
                showmeans=True,
                flierprops={
                    "marker": "o",
                    "markerfacecolor": "none",
                    "markersize": 4,
                },
            )
        else:
            axis.text(
                0.5,
                0.5,
                "Measurements unavailable",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )

    figure.suptitle(f"{label}: measurement distributions")
    output = BytesIO()
    canvas.print_png(output)
    figure.clear()
    output.seek(0)
    return output

def _boxplot_outlier_count(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if not values.size:
        return None
    if values.size < 2:
        return 0
    lower_quartile, upper_quartile = np.percentile(values, [25, 75])
    iqr = upper_quartile - lower_quartile
    lower_fence = lower_quartile - 1.5 * iqr
    upper_fence = upper_quartile + 1.5 * iqr
    return int(np.count_nonzero((values < lower_fence) | (values > upper_fence)))

def _boxplot_inliers(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 2:
        return values
    lower_quartile, upper_quartile = np.percentile(values, [25, 75])
    iqr = upper_quartile - lower_quartile
    lower_fence = lower_quartile - 1.5 * iqr
    upper_fence = upper_quartile + 1.5 * iqr
    return values[(values >= lower_fence) & (values <= upper_fence)]


def _range_export_report_sections(window, selection):
    comparison = window._comparison_range_df
    profile = selection["treatment_profile"]
    summary_rows = comparison[
        comparison["Record Type"].astype(str).str.casefold()
        == "phase summary"
    ]
    report_sections = []
    suffixes = {
        "Duration (s)": " s",
        "High Peak (mm)": " mm",
        "Low Peak (mm)": " mm",
        "Peak-to-Peak (mm)": " mm",
        "Amplitude (mm)": " mm",
        "Cycle Period (s)": " s",
        "Frequency (Hz)": " Hz",
        "Cycle Count": "",
    }

    for (device, phase), phase_rows in summary_rows.groupby(
        ["Device number", "Phase"],
        dropna=False,
        sort=False,
    ):
        rows = []
        for measurement in NUMERIC_COLUMNS:
            values = pd.to_numeric(
                phase_rows[measurement], errors="coerce"
            ).dropna()
            if values.empty:
                continue
            observed = float(values.mean())
            suffix = suffixes.get(measurement, "")
            try:
                result = compare_observed_value(
                    selection["config_path"],
                    profile,
                    "phase_population",
                    measurement,
                    observed,
                    phase=str(phase),
                    record_type="Phase Summary",
                    vibration="All Vibrations",
                    range_selection_method=selection["range_selection_method"],
                )
                baseline_value = (
                    f"[{result['lower']:.3f}, {result['upper']:.3f}]"
                    f"{suffix}"
                )
                status = (
                    "Within range"
                    if result["status"] == "PASS"
                    else "Outside range"
                )
                comparison_value = (
                    f"{observed:.3f}{suffix} (n={len(values)}) — {status}"
                )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                baseline_value = "Not available"
                comparison_value = (
                    f"{observed:.3f}{suffix} (n={len(values)}) — "
                    f"reference unavailable: {exc}"
                )
            rows.append((measurement, baseline_value, comparison_value))

        report_sections.append(
            (
                f"{phase} — Device {device}",
                rows,
                [
                    f"Comparison values are means across Phase Summary rows "
                    f"for device {device} and the selected profile.",
                    "Statistical boundaries are descriptive references; this "
                    "report does not assign an overall pass/fail assessment.",
                ],
                None,
                None,
                None,
            )
        )
    return report_sections


def _save_range_export_report(window, path, report_metadata, selection):
    report_sections = _range_export_report_sections(window, selection)
    if not any(section[1] for section in report_sections):
        QMessageBox.information(
            window,
            "Export report table",
            "The selected treatment profile has no numeric Phase Summary "
            "measurements to report.",
        )
        return

    baseline_name = os.path.basename(
        os.path.dirname(selection["config_path"])
    ) or os.path.basename(selection["config_path"])
    compare_name = os.path.splitext(
        os.path.basename(selection["comparison_csv"])
    )[0]
    client_path = os.path.splitext(path)[0] + "_CLIENT.docx"
    json_path = os.path.splitext(path)[0] + ".json"
    assessment = "INCONCLUSIVE"
    assessment_reasons = [
        "This report compares phase-summary means with descriptive statistical "
        "ranges and does not apply the separate waveform-based assessment rules."
    ]
    client_description = (
        "This report presents descriptive comparisons against the selected "
        "statistical reference ranges. It does not determine an overall "
        "pass/fail assessment."
    )
    try:
        save_reports(
            path,
            client_path,
            baseline_name,
            compare_name,
            None,
            assessment,
            assessment_reasons,
            client_description,
            report_sections,
            report_metadata,
        )
        save_report_json(
            json_path,
            baseline_name,
            compare_name,
            None,
            report_metadata,
            assessment,
            assessment_reasons,
            client_description,
            report_sections,
        )
    except (OSError, ValueError) as exc:
        QMessageBox.warning(
            window,
            "Export report table",
            f"Failed to save report:\n{exc}",
        )
        return
    QMessageBox.information(
        window,
        "Export report table",
        f"Saved reports:\n{path}\n{client_path}\n{json_path}",
    )


def _save_report_table(window):
    baseline_selection = window.prepare_baseline_selection()
    if baseline_selection is None:
        return

    if (
        baseline_selection["comparison_format"] != "range_export"
        and not window._phase_ranges
    ):
        QMessageBox.information(
            window,
            "Export report table",
            "Comparison data and baseline are selected. Add or detect phase "
            "ranges separately, then export the report.",
        )
        return

    start_dir = window._csv_start_dir()
    suggested = (
        os.path.join(start_dir, "comparison_report.docx")
        if start_dir
        else "comparison_report.docx"
    )
    path, _ = QFileDialog.getSaveFileName(
        window,
        "Export comparison report table",
        suggested,
        "Word documents (*.docx);;All files (*)",
    )
    if not path:
        return
    if not path.lower().endswith(".docx"):
        path += ".docx"

    metadata_dialog = ReportMetadataDialog(
        window,
        treatment_profile=baseline_selection["treatment_profile"],
    )
    if metadata_dialog.exec_() != QDialog.Accepted:
        return
    report_metadata = metadata_dialog.metadata()
    report_metadata["Treatment profile"] = baseline_selection["treatment_profile"]
    report_metadata.update(
        {
            "Comparator baseline": baseline_selection["config_path"],
            "Baseline range method": baseline_selection["range_selection_method"],
            "Comparison CSV": baseline_selection["comparison_csv"],
        }
    )
    if baseline_selection["comparison_format"] == "range_export":
        _save_range_export_report(
            window,
            path,
            report_metadata,
            baseline_selection,
        )
        return
    if window._detection_metadata is not None:
        parameters = window._detection_metadata["parameters_used"]
        report_metadata.update(
            {
                "Detection Method": window._detection_metadata[
                    "detection_method"
                ],
                "Detection Mode": window._detection_metadata["detection_mode"],
                "Selected Treatment Profile": window._detection_metadata[
                    "selected_profile"
                ],
                "Resolved Treatment Profile": window._detection_metadata[
                    "resolved_profile"
                ],
                "Expected Amplitude Category": window._detection_metadata[
                    "expected_amplitude_category"
                ],
                "Profile Parameters Used": json.dumps(
                    parameters, sort_keys=True
                ),
                "Detection Confidence": (
                    f"{window._detection_metadata['detection_confidence']:.3f}"
                    if window._detection_metadata["detection_confidence"]
                    is not None
                    else "Not available"
                ),
            }
        )

    offset = (
        float(window.offset_spin.value())
        if window._baseline is not None and window._compare is not None
        else None
    )
    phase_data = {}
    for phase in window._phase_ranges:
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
                "coverage_incomplete": False,
                "baseline_amplitudes": [],
                "compare_amplitudes": [],
                "baseline_periods": [],
                "compare_periods": [],
                "cycle_checks": [],
            },
        )
        data["ranges"].append(phase)
        start_s = phase["start_s"]
        end_s = phase["end_s"]

        baseline_stats = {
            "cycle_count": None,
            "sample_count": 0,
            "amplitudes": [],
            "mean": None,
            "std": None,
        }
        baseline_period = {"periods": [], "mean": None, "std": None, "sample_count": 0}
        if window._baseline_extrema is not None:
            baseline_extrema = window._baseline_extrema["rows"]
            baseline_stats = _peak_to_peak_stats(
                baseline_extrema, start_s, end_s
            )
            data["baseline_amplitudes"].extend(baseline_stats["amplitudes"])
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
            data["baseline_periods"].extend(baseline_period["periods"])
            if baseline_period["mean"] is not None:
                data["baseline_period_sum"] += (
                    baseline_period["mean"] * baseline_period["sample_count"]
                )
                data["baseline_period_count"] += baseline_period["sample_count"]

        compare_stats = {
            "cycle_count": None,
            "sample_count": 0,
            "amplitudes": [],
            "mean": None,
            "std": None,
        }
        compare_period = {"periods": [], "mean": None, "std": None, "sample_count": 0}
        if window._compare is not None and offset is not None:
            compare_mask = (
                np.isfinite(window._compare["time_s"])
                & np.isfinite(window._compare["px"])
            )
            compare_times = window._compare["time_s"][compare_mask]
            compare_extrema_rows = (
                window._compare_extrema["rows"]
                if window._compare_extrema is not None
                else []
            )
            if compare_times.size >= 2:
                compare_start = start_s - offset
                compare_end = end_s - offset
                in_range = (
                    (compare_times >= compare_start)
                    & (compare_times <= compare_end)
                )
                if np.count_nonzero(in_range) >= 3:
                    compare_stats = _peak_to_peak_stats(
                        compare_extrema_rows, compare_start, compare_end
                    )
                    data["compare_amplitudes"].extend(
                        compare_stats["amplitudes"]
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
                        compare_extrema_rows, compare_start, compare_end
                    )
                    data["compare_periods"].extend(compare_period["periods"])
                    if compare_period["mean"] is not None:
                        data["compare_period_sum"] += (
                            compare_period["mean"]
                            * compare_period["sample_count"]
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
        expected_cycles = (
            int(phase["expected_cycles"])
            if phase["expected_cycles"]
            else baseline_stats["cycle_count"]
        )
        phase_key, _weight = _phase_weight(phase["label"])
        data["cycle_checks"].append(
            {
                "start_s": start_s,
                "end_s": end_s,
                "expected": expected_cycles,
                "baseline": baseline_stats["cycle_count"],
                "observed": compare_stats["cycle_count"],
            }
        )

        if window._baseline is not None and window._compare is not None:
            position_key = "mm" if window._use_mm() else "px"
            baseline_mask = (
                np.isfinite(window._baseline["time_s"])
                & np.isfinite(window._baseline[position_key])
            )
            compare_mask = (
                np.isfinite(window._compare["time_s"])
                & np.isfinite(window._compare[position_key])
            )
            baseline_times = window._baseline["time_s"][baseline_mask]
            baseline_values = window._baseline[position_key][baseline_mask]
            compare_times = window._compare["time_s"][compare_mask]
            compare_values = window._compare[position_key][compare_mask]
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

    overall_samples = {}
    report_sections = []
    for label, data in phase_data.items():
        phase_key, _weight = _phase_weight(label)
        try:
            if phase_key == "phase_3":
                internal_phase_plot = None
                client_phase_plot = None
            else:
                internal_phase_plot = _phase_waveform_figure(
                    window._baseline,
                    window._compare,
                    data["ranges"],
                    offset,
                    window._use_mm(),
                )
                client_phase_plot = _phase_waveform_figure(
                    window._baseline,
                    window._compare,
                    data["ranges"],
                    offset,
                    window._use_mm(),
                    client_facing=True,
                )
            phase_boxplot = (
                _phase_boxplot_figure(
                    label,
                    data["baseline_amplitudes"],
                    data["compare_amplitudes"],
                    data["baseline_periods"],
                    data["compare_periods"],
                )
                if phase_key == "phase_1"
                else None
            )
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(
                window,
                "Export report table",
                f"Failed to create report graph for {label}:\n{exc}",
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
        baseline_max_amplitude = (
            max(data["baseline_amplitudes"])
            if data["baseline_amplitudes"]
            else None
        )
        compare_max_amplitude = (
            max(data["compare_amplitudes"])
            if data["compare_amplitudes"]
            else None
        )
        max_amplitude_difference = (
            compare_max_amplitude - baseline_max_amplitude
            if compare_max_amplitude is not None
            and baseline_max_amplitude is not None
            else None
        )
        max_amplitude_pct = _relative_difference_pct(
            max_amplitude_difference, baseline_max_amplitude
        )
        baseline_frequencies = [
            1.0 / period
            for period in data["baseline_periods"]
            if period > 0
        ]
        compare_frequencies = [
            1.0 / period
            for period in data["compare_periods"]
            if period > 0
        ]
        baseline_frequency = (
            float(np.mean(baseline_frequencies))
            if baseline_frequencies
            else None
        )
        compare_frequency = (
            float(np.mean(compare_frequencies))
            if compare_frequencies
            else None
        )
        frequency_difference = (
            compare_frequency - baseline_frequency
            if compare_frequency is not None and baseline_frequency is not None
            else None
        )
        frequency_pct = _relative_difference_pct(
            frequency_difference, baseline_frequency
        )
        baseline_frequency_range = (
            f"{min(baseline_frequencies):.3f}–"
            f"{max(baseline_frequencies):.3f} Hz"
            if baseline_frequencies
            else None
        )
        compare_frequency_range = (
            f"{min(compare_frequencies):.3f}–"
            f"{max(compare_frequencies):.3f} Hz"
            if compare_frequencies
            else None
        )

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
        baseline_amplitude_mm = (
            baseline_amplitude * _PX_TO_MM
            if baseline_amplitude is not None
            else None
        )
        compare_amplitude_mm = (
            compare_amplitude * _PX_TO_MM
            if compare_amplitude is not None
            else None
        )
        amplitude_difference_mm = (
            amplitude_difference * _PX_TO_MM
            if amplitude_difference is not None
            else None
        )
        baseline_max_amplitude_mm = (
            baseline_max_amplitude * _PX_TO_MM
            if baseline_max_amplitude is not None
            else None
        )
        compare_max_amplitude_mm = (
            compare_max_amplitude * _PX_TO_MM
            if compare_max_amplitude is not None
            else None
        )
        max_amplitude_difference_mm = (
            max_amplitude_difference * _PX_TO_MM
            if max_amplitude_difference is not None
            else None
        )
        baseline_mean_amplitude = (
            baseline_amplitude * 0.5
            if baseline_amplitude is not None
            else None
        )
        compare_mean_amplitude = (
            compare_amplitude * 0.5
            if compare_amplitude is not None
            else None
        )
        mean_amplitude_difference = (
            amplitude_difference * 0.5
            if amplitude_difference is not None
            else None
        )
        baseline_mean_amplitude_mm = (
            baseline_mean_amplitude * _PX_TO_MM
            if baseline_mean_amplitude is not None
            else None
        )
        compare_mean_amplitude_mm = (
            compare_mean_amplitude * _PX_TO_MM
            if compare_mean_amplitude is not None
            else None
        )
        mean_amplitude_difference_mm = (
            mean_amplitude_difference * _PX_TO_MM
            if mean_amplitude_difference is not None
            else None
        )
        baseline_max_amplitude_value = (
            baseline_max_amplitude * 0.5
            if baseline_max_amplitude is not None
            else None
        )
        compare_max_amplitude_value = (
            compare_max_amplitude * 0.5
            if compare_max_amplitude is not None
            else None
        )
        max_amplitude_difference_value = (
            max_amplitude_difference * 0.5
            if max_amplitude_difference is not None
            else None
        )
        baseline_max_amplitude_value_mm = (
            baseline_max_amplitude_value * _PX_TO_MM
            if baseline_max_amplitude_value is not None
            else None
        )
        compare_max_amplitude_value_mm = (
            compare_max_amplitude_value * _PX_TO_MM
            if compare_max_amplitude_value is not None
            else None
        )
        max_amplitude_difference_value_mm = (
            max_amplitude_difference_value * _PX_TO_MM
            if max_amplitude_difference_value is not None
            else None
        )
        selected_intervals = []
        compare_intervals = []
        for phase_range in data["ranges"]:
            start_s = phase_range["start_s"]
            end_s = phase_range["end_s"]
            duration = end_s - start_s
            selected_intervals.append(
                f"{start_s:.2f}–{end_s:.2f} s ({duration:.2f} s)"
            )
            if offset is not None:
                compare_intervals.append(
                    f"{start_s - offset:.2f}–{end_s - offset:.2f} s "
                    f"({duration:.2f} s)"
                )
        interval_row = (
            "Selected interval(s): start–end (duration)",
            "\n".join(selected_intervals) or "—",
            "\n".join(compare_intervals)
            if compare_intervals and window._compare is not None
            else "—",
        )
        rows = [interval_row]
        cycle_results, _cycle_unresolved = _evaluate_cycle_checks(
            phase_key, data["cycle_checks"]
        )
        if phase_key == "phase_1":
            rows.extend(
                [
                    ("Mean peak-to-peak", baseline_cell, compare_cell),
                    (
                        "Mean amplitude",
                        format_value(baseline_mean_amplitude, " px"),
                        comparison_value(
                            compare_mean_amplitude,
                            mean_amplitude_difference,
                            amplitude_pct,
                            " px",
                        ),
                    ),
                    (
                        "Mean peak-to-peak (mm)",
                        format_value(baseline_amplitude_mm, " mm"),
                        comparison_value(
                            compare_amplitude_mm,
                            amplitude_difference_mm,
                            amplitude_pct,
                            " mm",
                        ),
                    ),
                    (
                        "Mean amplitude (mm)",
                        format_value(baseline_mean_amplitude_mm, " mm"),
                        comparison_value(
                            compare_mean_amplitude_mm,
                            mean_amplitude_difference_mm,
                            amplitude_pct,
                            " mm",
                        ),
                    ),
                    (
                        "Mean frequency",
                        format_value(baseline_frequency, " Hz"),
                        comparison_value(
                            compare_frequency,
                            frequency_difference,
                            frequency_pct,
                            " Hz",
                        ),
                    ),
                    (
                        "Cycle frequency range (min–max)",
                        baseline_frequency_range or "—",
                        compare_frequency_range or "—",
                    ),
                    (
                        "Mean cycle period",
                        format_value(baseline_period, " s"),
                        comparison_value(
                            compare_period, period_difference, period_pct, " s"
                        ),
                    ),
                ]
            )
        elif phase_key == "phase_2":
            rows.extend(
                [
                    (
                        "Maximum peak-to-peak "
                        "(maximum of detected cycles)",
                        format_value(baseline_max_amplitude, " px"),
                        comparison_value(
                            compare_max_amplitude,
                            max_amplitude_difference,
                            max_amplitude_pct,
                            " px",
                        ),
                    ),
                    (
                        "Maximum amplitude",
                        format_value(baseline_max_amplitude_value, " px"),
                        comparison_value(
                            compare_max_amplitude_value,
                            max_amplitude_difference_value,
                            max_amplitude_pct,
                            " px",
                        ),
                    ),
                    (
                        "Maximum peak-to-peak (mm)",
                        format_value(baseline_max_amplitude_mm, " mm"),
                        comparison_value(
                            compare_max_amplitude_mm,
                            max_amplitude_difference_mm,
                            max_amplitude_pct,
                            " mm",
                        ),
                    ),
                    (
                        "Maximum amplitude (mm)",
                        format_value(baseline_max_amplitude_value_mm, " mm"),
                        comparison_value(
                            compare_max_amplitude_value_mm,
                            max_amplitude_difference_value_mm,
                            max_amplitude_pct,
                            " mm",
                        ),
                    ),
                    (
                        "Mean cycle period (assessment metric)",
                        format_value(baseline_period, " s"),
                        comparison_value(
                            compare_period, period_difference, period_pct, " s"
                        ),
                    ),
                ]
            )
        elif phase_key not in ("phase_1", "phase_2", "phase_3"):
            rows.extend(
                [
                    ("Mean peak-to-peak", baseline_cell, compare_cell),
                    (
                        "Mean cycle period",
                        format_value(baseline_period, " s"),
                        comparison_value(
                            compare_period, period_difference, period_pct, " s"
                        ),
                    ),
                ]
            )
        if phase_key in ("phase_1", "phase_2"):
            rows.append(
                (
                    "Total detected cycles across selected intervals",
                    format_value(baseline_cycles, digits=0),
                    comparison_value(
                        compare_cycles, cycle_difference, cycle_pct, digits=0
                    ),
                )
            )
            for result in cycle_results:
                check = result["check"]
                interval_label = (
                    f"{check['start_s']:.3f}–{check['end_s']:.3f} s"
                )
                if phase_key == "phase_1":
                    baseline_cycle_text = (
                        f"Expected {result['expected']} cycles"
                        if result["expected"] is not None
                        else "Expected count unavailable"
                    )
                    if check["observed"] is None:
                        comparison_cycle_text = "Comparison count unavailable"
                    else:
                        comparison_cycle_text = (
                            f"{check['observed']} cycles; difference "
                            f"{result['difference']} — {result['status']}"
                        )
                else:
                    baseline_cycle_text = (
                        f"{check['baseline']} cycles; reference mean "
                        f"{result['baseline_mean']:.3g}, SD "
                        f"{result['baseline_std']:.3g}"
                        if result["baseline_mean"] is not None
                        and result["baseline_std"] is not None
                        and check["baseline"] is not None
                        else "Reference mean/SD unavailable"
                    )
                    if check["observed"] is None:
                        comparison_cycle_text = "Comparison count unavailable"
                    elif result["percent_difference"] is None:
                        comparison_cycle_text = (
                            f"{check['observed']} cycles; cannot assess"
                        )
                    else:
                        comparison_cycle_text = (
                            f"{check['observed']} cycles; difference "
                            f"{result['difference']:.2f} cycles "
                            f"({result['percent_difference']:.1f}% of mean) — "
                            f"{result['status']}"
                        )
                rows.append(
                    (
                        f"Cycle-count check ({interval_label})",
                        baseline_cycle_text,
                        comparison_cycle_text,
                    )
                )
        if phase_key == "phase_1":
            rows.extend(
                [
                    (
                        "Boxplot outlier measurements—amplitude "
                        "(1.5×IQR rule)",
                        format_value(
                            _boxplot_outlier_count(data["baseline_amplitudes"]),
                            digits=0,
                        ),
                        format_value(
                            _boxplot_outlier_count(data["compare_amplitudes"]),
                            digits=0,
                        ),
                    ),
                    (
                        "Boxplot outlier measurements—period (1.5×IQR rule)",
                        format_value(
                            _boxplot_outlier_count(data["baseline_periods"]),
                            digits=0,
                        ),
                        format_value(
                            _boxplot_outlier_count(data["compare_periods"]),
                            digits=0,
                        ),
                    ),
                ]
            )
        statistical_measurements = [
            ("Peak-to-peak", "Peak-to-Peak (mm)", compare_amplitude_mm, " mm", 3),
            ("Amplitude", "Amplitude (mm)", compare_mean_amplitude_mm, " mm", 3),
            ("Cycle period", "Cycle Period (s)", compare_period, " s", 3),
            ("Frequency", "Frequency (Hz)", compare_frequency, " Hz", 3),
            ("Cycle count", "Cycle Count", compare_cycles, "", 0),
        ]
        rows.extend(
            _statistical_range_rows(window, label, statistical_measurements)
        )
        rows.append(
            (
                "Assessment role",
                "Reference for overall result"
                if phase_key in ("phase_1", "phase_2")
                else "Informational only",
                "Informational only"
                if phase_key == "phase_3"
                else "Contributes to overall result"
                if phase_key in ("phase_1", "phase_2")
                else "Not assessed",
            )
        )
        notes = []
        if window._baseline_extrema is None:
            notes.append(
                "Baseline peak-to-peak measurements were not available; "
                "amplitude, period, or cycle values may be blank."
            )
        if window._compare is None:
            notes.append("Comparison tracking data was not available.")
        elif data["coverage_incomplete"]:
            notes.append(
                "Comparison data did not cover the full selected time range."
            )
        if phase_key == "phase_3":
            notes.append(
                "Phase 3 measurements are informational only and do not "
                "affect the device assessment."
            )
        internal_plot_data = (
            internal_phase_plot.getvalue()
            if internal_phase_plot is not None
            else None
        )
        client_plot_data = (
            client_phase_plot.getvalue()
            if client_phase_plot is not None
            else None
        )
        report_sections.append(
            (
                label,
                rows,
                notes,
                internal_plot_data,
                client_plot_data,
                phase_boxplot.getvalue() if phase_boxplot is not None else None,
            )
        )
        for phase_plot in (internal_phase_plot, client_phase_plot, phase_boxplot):
            if phase_plot is not None:
                phase_plot.close()

    overall_correlation = _weighted_correlation(overall_samples)
    overall_text = (
        f"{overall_correlation:.3f}"
        if overall_correlation is not None
        else "Not available (insufficient paired, varying samples)"
    )
    assessment, assessment_reasons = _assess_baseline_specifications(
        phase_data,
        compare_loaded=window._compare is not None,
    )
    baseline_name = (
        window._baseline["name"] if window._baseline is not None else "Not loaded"
    )
    compare_name = (
        window._compare["name"] if window._compare is not None else "Not loaded"
    )
    client_path = os.path.splitext(path)[0] + "_CLIENT.docx"
    json_path = os.path.splitext(path)[0] + ".json"
    client_description = _client_assessment_description(assessment)
    try:
        save_reports(
            path,
            client_path,
            baseline_name,
            compare_name,
            offset,
            assessment,
            assessment_reasons,
            client_description,
            report_sections,
            report_metadata,
        )
        save_report_json(
            json_path,
            baseline_name,
            compare_name,
            offset,
            report_metadata,
            assessment,
            assessment_reasons,
            client_description,
            report_sections,
        )
    except OSError as exc:
        QMessageBox.warning(
            window, "Export report table", f"Failed to save report:\n{exc}"
        )
        return
    QMessageBox.information(
        window,
        "Export report table",
        f"Saved reports:\n{path}\n{client_path}\n{json_path}",
    )


class ReportExportButton(QPushButton):
    """Button that delegates report generation to this module."""

    def __init__(self, compare_window):
        super().__init__("Export report table…", compare_window)
        self.setToolTip(
            "Choose a comparison tracking CSV and aggregated statistical baseline "
            "to generate the report."
        )
        self.clicked.connect(
            lambda _checked=False: _save_report_table(compare_window)
        )
