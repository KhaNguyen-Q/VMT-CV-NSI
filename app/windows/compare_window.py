"""Experimental overlay of two exported tracking CSVs. Does not touch the live plot."""

import csv
import os

import matplotlib

matplotlib.use("Agg")
import numpy as np
import pyqtgraph as pg
from scipy.ndimage import percentile_filter
from scipy.signal import savgol_filter
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QDialogButtonBox,
)

try:
    from automation.detection_profiles import (
        default_profiles_path,
        default_treatment_profiles,
        load_treatment_profiles,
        save_treatment_profiles,
    )
    from automation.phase_detector import detect_phase_ranges_with_auto_refinement
    from automation.signal_features import resample_extrema_signal
except ModuleNotFoundError:
    from app.automation.detection_profiles import (
        default_profiles_path,
        default_treatment_profiles,
        load_treatment_profiles,
        save_treatment_profiles,
    )
    from app.automation.phase_detector import detect_phase_ranges_with_auto_refinement
    from app.automation.signal_features import resample_extrema_signal
try:
    from windows.treatment_profile_dialog import TreatmentProfileDialog
except ModuleNotFoundError:
    from app.windows.treatment_profile_dialog import TreatmentProfileDialog

from utils.utils import (
    dominant_fft_frequency,
    find_peak_to_peak_extrema,
    get_project_root,
)
from windows.graph_viewbox import GraphViewBox
from windows.report_export import (
    ReportExportButton,
    _peak_to_peak_stats,
    _phase_weight,
    _series_position,
)
from windows.report_metadata_dialog import ReportMetadataDialog
from windows.range_analysis_window import RangeAnalysisWindow
try:
    from windows.baseline_selection_dialog import BaselineSelectionDialog
except ModuleNotFoundError:
    from app.windows.baseline_selection_dialog import BaselineSelectionDialog
from windows.range_csv_export import (
    available_phases,
    ranges_for_phase,
    tagged_cycle_count,
    write_range_export_csv,
)
try:
    from analysis.comparator import load_range_comparison_csv
except ModuleNotFoundError:
    from app.analysis.comparator import load_range_comparison_csv

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
    labels = []
    for row in rows:
        t = _to_float(row.get("time_s"))
        if t is None:
            continue
        times.append(t)
        px.append(_to_float(row.get(px_col)) if px_col else None)
        mm.append(_to_float(row.get(mm_col)) if mm_col else None)
        labels.append((row.get("label") or "").strip().casefold())
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
    labels = np.asarray(labels)[order]
    extrema_only = (
        px_col == "position_px"
        and np.isfinite(px_arr).all()
        and len(labels) >= 8
        and np.isin(labels, ("top", "bottom")).all()
        and np.all(labels[1:] != labels[:-1])
    )
    return {
        "name": os.path.splitext(os.path.basename(path))[0],
        "path": path,
        "time_s": times,
        "px": px_arr,
        "mm": mm_arr,
        "extrema_only": bool(extrema_only),
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


def _detected_extrema(series):
    mask = np.isfinite(series["time_s"]) & np.isfinite(series["px"])
    times = series["time_s"][mask]
    positions = series["px"][mask]
    if times.size < 3:
        return None
    rows = find_peak_to_peak_extrema(times, positions)
    if not rows:
        return None
    return {
        "name": f"{series['name']} (detected)",
        "path": series["path"],
        "rows": rows,
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
    compare_extrema=None,
    n_sigmas=3.0,
):
    """Compare each range with baseline extrema and configured expectations."""
    findings = []
    compare_extrema = compare_extrema or []
    phase2_baseline_counts = [
        _peak_to_peak_stats(baseline_extrema, phase["start_s"], phase["end_s"])[
            "cycle_count"
        ]
        for phase in phase_ranges
        if baseline_extrema is not None
        and _phase_weight(phase["label"])[0] == "phase_2"
    ]
    phase2_baseline_counts = [
        count for count in phase2_baseline_counts if count is not None
    ]
    phase2_mean = (
        float(np.mean(phase2_baseline_counts))
        if phase2_baseline_counts
        else None
    )
    phase2_std = (
        float(np.std(phase2_baseline_counts, ddof=1))
        if len(phase2_baseline_counts) >= 2
        else None
    )
    for phase in phase_ranges:
        start = phase["start_s"]
        end = phase["end_s"]
        compare_start = start - offset
        compare_end = end - offset

        baseline_stats = (
            _peak_to_peak_stats(baseline_extrema, start, end)
            if baseline_extrema is not None
            else {"cycle_count": 0, "sample_count": 0, "mean": None, "std": None}
        )
        compare_stats = _peak_to_peak_stats(
            compare_extrema, compare_start, compare_end
        )
        phase_compare_extrema = [
            row
            for row in compare_extrema
            if compare_start <= row["time_s"] <= compare_end
        ]
        baseline_cycles = baseline_stats["cycle_count"]
        compare_cycles = compare_stats["cycle_count"]
        expected_cycles = phase["expected_cycles"] or baseline_cycles
        phase_key, _weight = _phase_weight(phase["label"])

        cycle_expectation = expected_cycles
        cycle_difference = None
        if phase_key == "phase_1" and expected_cycles is not None:
            cycle_difference = abs(compare_cycles - expected_cycles)
            cycle_mismatch = cycle_difference >= 2
        elif (
            phase_key == "phase_2"
            and phase2_mean is not None
            and phase2_std is not None
        ):
            cycle_expectation = phase2_mean
            cycle_difference = abs(compare_cycles - phase2_mean)
            if phase2_std == 0:
                cycle_deviations = (
                    0.0 if cycle_difference == 0 else float("inf")
                )
            else:
                cycle_deviations = cycle_difference / phase2_std
            cycle_mismatch = cycle_deviations > 2
        elif phase_key == "phase_1" or phase_key == "phase_2":
            cycle_mismatch = False
        else:
            cycle_mismatch = expected_cycles is not None and (
                compare_cycles != expected_cycles
            )
            if cycle_mismatch:
                cycle_difference = abs(compare_cycles - expected_cycles)

        if cycle_mismatch:
            if phase_key == "phase_2":
                description = (
                    f"Phase 2 cycle count is {cycle_deviations:.2f} baseline "
                    "standard deviations from the mean"
                )
                tag = "strong_outlier" if cycle_deviations > 3 else "review"
            else:
                description = (
                    f"Cycle count differs from expected for {phase['label']}"
                )
                tag = (
                    "strong_outlier"
                    if phase_key == "phase_1" and cycle_difference >= 3
                    else "review"
                )
            findings.append(
                {
                    "finding_type": "cycle_count",
                    "phase": phase["label"],
                    "time_s": start,
                    "expected": cycle_expectation,
                    "observed": compare_cycles,
                    "ratio": (
                        compare_cycles / cycle_expectation
                        if cycle_expectation
                        else ""
                    ),
                    "baseline_pos": "",
                    "compare_pos": "",
                    "residual": "",
                    "tag": tag,
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

        for row in phase_compare_extrema:
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
        self._comparison_csv_path = None
        self._comparison_range_df = None
        self._compare_extrema = None
        self._baseline_selection = None
        self._outlier_rows = []
        self._phase_ranges = []
        self._setting_offset = False
        self._phase_region = None
        self._detection_metadata = None
        self._last_detection_result = None
        self._range_analysis_window = None
        self._detection_profile_path = default_profiles_path()
        try:
            self._treatment_profiles = load_treatment_profiles(
                self._detection_profile_path
            )
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(
                self,
                "Treatment profiles",
                f"Could not load treatment profiles:\n{exc}\n\n"
                "Built-in profiles will be used until the profile library is saved.",
            )
            self._treatment_profiles = default_treatment_profiles()

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
        baseline_btn.setToolTip(
            "Load the raw reference signal used to detect and label phase ranges. "
            "The statistical report baseline is selected during report export."
        )
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

        profile_controls = QHBoxLayout()
        profile_controls.addWidget(QLabel("Detection method:"))
        self.detection_method_combo = QComboBox()
        self.detection_method_combo.addItems(["FSM", "Signals"])
        self.detection_method_combo.setToolTip(
            "FSM uses the existing finite-state detector. Signals uses rolling "
            "cycle trends and energy decay."
        )
        profile_controls.addWidget(self.detection_method_combo)
        profile_controls.addWidget(QLabel("Treatment profile:"))
        self.treatment_profile_combo = QComboBox()
        self._refresh_treatment_profile_combo()
        profile_controls.addWidget(self.treatment_profile_combo)
        self.manage_profiles_btn = QPushButton("Manage profiles…")
        self.manage_profiles_btn.clicked.connect(self._manage_treatment_profiles)
        profile_controls.addWidget(self.manage_profiles_btn)
        self.detection_diagnostics_btn = QPushButton("Detection diagnostics…")
        self.detection_diagnostics_btn.setEnabled(False)
        self.detection_diagnostics_btn.clicked.connect(
            self._show_detection_diagnostics
        )
        profile_controls.addWidget(self.detection_diagnostics_btn)
        profile_controls.addStretch(1)
        layout.addLayout(profile_controls)

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
        expected_cycles_label = QLabel(
            "Expected cycles (0 = detected baseline count):"
        )
        expected_cycles_label.setToolTip(
            "Used for Phase 1 cycle assessment and smoothing. Phase 2 cycle "
            "assessment uses the mean and standard deviation of selected "
            "baseline ranges."
        )
        phase_controls.addWidget(expected_cycles_label)
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
        self.auto_detect_phases_btn = QPushButton("Auto Detect Phases")
        self.auto_detect_phases_btn.setEnabled(False)
        self.auto_detect_phases_btn.setToolTip(
            "Detect complete vibration events from the loaded baseline signal. "
            "Successful detection replaces the current phase ranges."
        )
        self.auto_detect_phases_btn.clicked.connect(self._auto_detect_phases)
        phase_controls.addWidget(self.auto_detect_phases_btn)
        self.export_report_btn = ReportExportButton(self)
        phase_controls.addWidget(self.export_report_btn)
        phase_controls.addStretch(1)
        layout.addLayout(phase_controls)

        analysis_controls = QHBoxLayout()
        self.range_analysis_btn = QPushButton("Analyze exported range CSVs…")
        self.range_analysis_btn.setToolTip(
            "Combine exported range CSVs and run phase, device, or "
            "device-variability statistics."
        )
        self.range_analysis_btn.clicked.connect(self._open_range_analysis)
        analysis_controls.addWidget(self.range_analysis_btn)
        analysis_controls.addWidget(
            QLabel("Select exported range CSVs to analyze multiple devices.")
        )
        analysis_controls.addStretch(1)
        layout.addLayout(analysis_controls)

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

    def _open_range_analysis(self):
        if self._range_analysis_window is None:
            self._range_analysis_window = RangeAnalysisWindow(self)
        self._range_analysis_window.show()
        self._range_analysis_window.raise_()
        self._range_analysis_window.activateWindow()

    def prepare_baseline_selection(self):
        dialog = BaselineSelectionDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            return None
        try:
            selection = dialog.selection()
            if selection["comparison_format"] == "range_export":
                self._comparison_range_df = load_range_comparison_csv(
                    selection["comparison_csv"],
                    selection["treatment_profile"],
                )
                self._compare = None
                self._compare_extrema = None
                self._comparison_csv_path = selection["comparison_csv"]
                self._align_and_redraw(estimate_offset=False)
            else:
                self._comparison_range_df = None
                self.load_comparison_csv(selection["comparison_csv"])
        except (OSError, ValueError, UnicodeError) as exc:
            QMessageBox.warning(
                self,
                "Prepare comparison report",
                f"Could not load comparison data or baseline selection:\n{exc}",
            )
            return None
        self._baseline_selection = selection
        return selection

    def load_comparison_csv(self, path):
        """Load a selected comparison tracking CSV into the comparison view."""
        self._compare = _read_tracking_csv(path)
        self._comparison_csv_path = os.path.abspath(path)
        self._comparison_range_df = None
        self._compare_extrema = _detected_extrema(self._compare)
        self._align_and_redraw(estimate_offset=True)

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
        self._baseline_extrema = _detected_extrema(self._baseline)
        self._detection_metadata = None
        self._last_detection_result = None
        self.detection_diagnostics_btn.setEnabled(False)
        self.extrema_source_label.setText(
            "Detected from baseline tracking CSV."
            if self._baseline_extrema is not None
            else "No baseline peak-to-peak data detected."
        )
        self._phase_ranges = []
        self.phase_table.setRowCount(0)
        self.remove_phase_btn.setEnabled(False)
        self.export_phase_ranges_btn.setEnabled(False)
        self.auto_detect_phases_btn.setEnabled(False)
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
        self.auto_detect_phases_btn.setEnabled(True)
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
            self.load_comparison_csv(path)
        except Exception as exc:
            QMessageBox.warning(self, "Load compare", f"Failed to load CSV:\n{exc}")
            return
        self._baseline_selection = None

    def _on_offset_changed(self, _value):
        if self._setting_offset:
            return
        self._align_and_redraw(estimate_offset=False)

    def _on_sensitivity_changed(self, _value):
        self._align_and_redraw(estimate_offset=False)

    def _on_phase_label_changed(self, label):
        self.expected_cycles_spin.setValue(0)

    def _auto_detect_phases(self):
        if self._baseline is None:
            return
        selected_profile = next(
            profile
            for profile in self._treatment_profiles
            if profile.name == self.treatment_profile_combo.currentText()
        )
        try:
            times = self._baseline["time_s"]
            positions = self._baseline["px"]
            if self._baseline.get("extrema_only", False):
                times, positions = resample_extrema_signal(times, positions)
            result = detect_phase_ranges_with_auto_refinement(
                times,
                positions,
                extrema=(
                    self._baseline_extrema["rows"]
                    if self._baseline_extrema is not None
                    else None
                ),
                profile=selected_profile,
                profiles=self._treatment_profiles,
                method=self.detection_method_combo.currentText(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Auto Detect Phases", str(exc))
            return
        self._last_detection_result = result
        self.detection_diagnostics_btn.setEnabled(True)
        if not result.phase_ranges:
            QMessageBox.information(
                self,
                "Auto Detect Phases",
                f"Detected {len(result.events)} vibration events."
                f"{self._format_auto_refinement_summary(result.profile_metadata)}",
            )
            return

        self._phase_ranges = result.phase_ranges
        self._detection_metadata = result.profile_metadata
        self._refresh_phase_table()
        self._align_and_redraw(estimate_offset=False)
        QMessageBox.information(
            self,
            "Auto Detect Phases",
            f"Detected {len(result.events)} vibration events."
            f"{self._format_auto_refinement_summary(result.profile_metadata)}",
        )

    @staticmethod
    def _format_auto_refinement_summary(metadata):
        refinement = metadata.get("auto_refinement")
        if refinement is None:
            return ""
        status = refinement["status"]
        if status == "tagged_extrema_unavailable":
            return (
                "\nTagged-cycle refinement was not run because baseline "
                "tagged extrema are unavailable."
            )
        if status == "no_phase1_ranges":
            return (
                "\nTagged-cycle refinement was not run because no Phase 1 "
                "ranges were detected."
            )
        counts = ", ".join(
            str(count) for count in refinement["tagged_phase1_counts"]
        )
        exact = refinement["exact_11_count"]
        total = refinement["phase1_count"]
        attempts = refinement["attempted_candidates"]
        if refinement["all_phase1_have_11"]:
            outcome = f"all {total} Phase 1 range(s) have exactly 11 tagged cycles"
        else:
            outcome = (
                f"{exact}/{total} Phase 1 range(s) have exactly 11 tagged cycles "
                f"(counts: {counts})"
            )
        return (
            f"\nAuto refinement: {outcome}; evaluated {attempts} alternative "
            f"profile(s)."
        )

    def _show_detection_diagnostics(self):
        if self._last_detection_result is None:
            return
        result = self._last_detection_result
        lines = [
            f"Detection method: {result.profile_metadata['detection_method']}",
            f"Selected profile: {result.profile_metadata['selected_profile']}",
            f"Resolved profile: {result.profile_metadata['resolved_profile']}",
            f"Amplitude category: "
            f"{result.profile_metadata['expected_amplitude_category']}",
            f"Whole-waveform candidate regions: {len(result.candidate_regions)}",
            f"Candidates evaluated: {len(result.diagnostics)}",
            "",
        ]
        for index, region in enumerate(result.candidate_regions, start=1):
            lines.append(
                f"Region {index}: {region['start_s']:.3f}-"
                f"{region['end_s']:.3f}s "
                f"({region['duration_s']:.2f}s); "
                f"peak density {region['peak_density_hz']:.3g} Hz; "
                f"frequency {region['mean_frequency_hz']:.3g} Hz; "
                f"periodicity {region['mean_periodicity']:.3g}; "
                f"RMS/noise {region['maximum_rms_noise_ratio']:.3g}; "
                f"amplitude/noise "
                f"{region['maximum_amplitude_noise_ratio']:.3g}"
            )
        if result.candidate_regions:
            lines.append("")
        for index, candidate in enumerate(result.diagnostics, start=1):
            status = "accepted" if candidate["passed"] else "rejected"
            reasons = ", ".join(candidate["rejection_reasons"]) or "none"
            checks = ", ".join(
                (
                    f"{name}={passed:.3g}"
                    if isinstance(passed, (int, float))
                    and not isinstance(passed, bool)
                    else f"{name}={'pass' if passed else 'fail' if passed is False else 'not evaluated'}"
                )
                for name, passed in candidate["qualification_checks"].items()
            )
            lines.append(
                f"Candidate {index} (region "
                f"{candidate.get('candidate_region', 'unknown')}, {status})\n"
                f"  Cycles: {candidate['candidate_cycles']}; "
                f"average frequency: {candidate['average_frequency_hz']:.3g} Hz; "
                f"frequency stability: {candidate['frequency_stability']:.3g}; "
                f"amplitude stability: {candidate['amplitude_stability']:.3g}\n"
                f"  Average amplitude: {candidate['average_amplitude']:.3g}; "
                f"RMS energy: {candidate['rms_energy']:.3g}; "
                f"SNR: {candidate['signal_to_noise_ratio']:.3g}; "
                f"minimum cycle SNR: {candidate['minimum_cycle_snr']:.3g}; "
                f"periodicity: {candidate['periodicity_strength']:.3g}\n"
                f"  Qualification checks: {checks}\n"
                f"  Rejection reasons: {reasons}"
            )
            if "frequency_slope" in candidate:
                lines.append(
                    f"  Trend: frequency slope {candidate['frequency_slope']:.3g} "
                    f"Hz/s (CV {candidate['frequency_cv']:.3g}, "
                    f"rolling ratio {candidate['rolling_frequency_ratio']:.3g}); "
                    f"amplitude slope {candidate['amplitude_slope']:.3g} px/s "
                    f"(CV {candidate['amplitude_cv']:.3g}); "
                    f"energy slope {candidate['energy_slope']:.3g} px/s; "
                    f"transition score {candidate['transition_score']:.3g}; "
                    f"phase confidences "
                    f"I {candidate['phase1_confidence']:.3g}, "
                    f"II {candidate['phase2_confidence']:.3g}, "
                    f"III {candidate['phase3_confidence']:.3g}; "
                    f"stable Phase 1 windows {candidate['stable_phase1_windows']}"
                )
        dialog = QDialog(self)
        dialog.setWindowTitle("Automatic phase detection diagnostics")
        dialog.resize(850, 600)
        text = QPlainTextEdit(dialog)
        text.setReadOnly(True)
        text.setPlainText("\n\n".join(lines))
        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout = QVBoxLayout(dialog)
        layout.addWidget(text)
        layout.addWidget(buttons)
        dialog.exec_()

    def _refresh_treatment_profile_combo(self, selected_name=None):
        if not hasattr(self, "treatment_profile_combo"):
            return
        selected_name = selected_name or self.treatment_profile_combo.currentText()
        self.treatment_profile_combo.clear()
        self.treatment_profile_combo.addItems(
            [profile.name for profile in self._treatment_profiles]
        )
        if selected_name not in {
            profile.name for profile in self._treatment_profiles
        }:
            selected_name = "Auto Detect"
        self.treatment_profile_combo.setCurrentText(selected_name)

    def _manage_treatment_profiles(self):
        dialog = TreatmentProfileDialog(self._treatment_profiles, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        profiles = dialog.profiles()
        try:
            save_treatment_profiles(self._detection_profile_path, profiles)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(
                self, "Treatment profiles", f"Could not save profiles:\n{exc}"
            )
            return
        selected_name = self.treatment_profile_combo.currentText()
        self._treatment_profiles = profiles
        self._refresh_treatment_profile_combo(selected_name)

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
        self._detection_metadata = None
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
        if selected_rows:
            self._detection_metadata = None
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
                str(phase["expected_cycles"] or "detected"),
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

    def _on_phase_selection_changed(self):
        selected = self.phase_table.selectionModel().selectedRows()
        self.remove_phase_btn.setEnabled(bool(selected))

    def _save_phase_ranges(self):
        if not self._phase_ranges:
            QMessageBox.information(
                self, "Export ranges", "There are no baseline ranges to export."
            )
            return
        if self._baseline is None or self._baseline_extrema is None:
            QMessageBox.warning(
                self,
                "Export ranges",
                "Load baseline tracking data with detectable peaks before exporting.",
            )
            return

        phase_names = available_phases(self._phase_ranges)
        phase_choices = phase_names + ["All phases"]
        default_choice = next(
            (
                index
                for index, name in enumerate(phase_names)
                if name.casefold() == "phase 1"
            ),
            len(phase_choices) - 1,
        )
        selected_phase, accepted = QInputDialog.getItem(
            self,
            "Select phases",
            "Choose which phases to include in the CSV:",
            phase_choices,
            default_choice,
            False,
        )
        if not accepted:
            return
        ranges = ranges_for_phase(self._phase_ranges, selected_phase)

        start_dir = self._csv_start_dir()
        default_name = (
            "comparison_ranges_report.csv"
            if self._compare is not None
            else "baseline_ranges.csv"
        )
        suggested = os.path.join(start_dir, default_name) if start_dir else default_name
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

        metadata_dialog = ReportMetadataDialog(self)
        if metadata_dialog.exec_() != QDialog.Accepted:
            return
        try:
            cycle_count = write_range_export_csv(
                path,
                ranges,
                self._baseline_extrema["rows"],
                self._baseline,
                metadata_dialog.metadata(),
            )
        except (OSError, ValueError, csv.Error) as exc:
            QMessageBox.warning(
                self, "Export ranges", f"Failed to save CSV:\n{exc}"
            )
            return
        QMessageBox.information(
            self,
            "Export ranges",
            f"Saved {len(ranges)} vibration ranges ({cycle_count} cycles):\n{path}",
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
                    compare_extrema=(
                        self._compare_extrema["rows"]
                        if self._compare_extrema is not None
                        else []
                    ),
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
