"""Experimental overlay of two exported tracking CSVs. Does not touch the live plot."""

import csv
import os

import numpy as np
import pyqtgraph as pg
from PyQt5.QtWidgets import (
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


def comparison_outliers(t_base, y_base, t_cmp, y_cmp, offset, n_sigmas=3.0):
    """Flag compare samples whose residual vs the baseline exceeds a MAD threshold."""
    t_plot = t_cmp + offset
    t0 = max(float(t_base[0]), float(t_plot[0]))
    t1 = min(float(t_base[-1]), float(t_plot[-1]))
    mask = (t_plot >= t0) & (t_plot <= t1)
    if not np.any(mask):
        empty = np.array([], dtype=float)
        return empty, empty, empty, empty

    t = t_plot[mask]
    y_c = y_cmp[mask]
    y_b = np.interp(t, t_base, y_base)
    residual = y_c - y_b
    mad = float(np.median(np.abs(residual - np.median(residual))))
    # MAD == 0 means the overlap is flat; anything off that level is an outlier.
    if mad == 0:
        scale = float(np.ptp(y_b)) or 1.0
        keep = np.abs(residual) > (1e-6 * scale)
    else:
        keep = np.abs(residual) > (n_sigmas * 1.4826 * mad)
    return t[keep], y_c[keep], y_b[keep], residual[keep]


def behavior_outliers(
    t_base, y_base, t_cmp, y_cmp, offset, phase_ranges, n_sigmas=3.0
):
    """Compare cycle counts and peak-to-peak amplitudes in labeled baseline ranges."""
    findings = []
    for phase in phase_ranges:
        start = phase["start_s"]
        end = phase["end_s"]
        baseline_mask = (t_base >= start) & (t_base <= end)
        compare_start = start - offset
        compare_end = end - offset
        compare_mask = (t_cmp >= compare_start) & (t_cmp <= compare_end)

        baseline_extrema = find_peak_to_peak_extrema(
            t_base[baseline_mask], y_base[baseline_mask]
        )
        compare_extrema = find_peak_to_peak_extrema(
            t_cmp[compare_mask], y_cmp[compare_mask]
        )
        baseline_cycles = min(
            sum(row["label"] == "top" for row in baseline_extrema),
            sum(row["label"] == "bottom" for row in baseline_extrema),
        )
        compare_cycles = min(
            sum(row["label"] == "top" for row in compare_extrema),
            sum(row["label"] == "bottom" for row in compare_extrema),
        )
        expected_cycles = phase["expected_cycles"] or baseline_cycles

        if (
            phase["expected_cycles"]
            and baseline_cycles != phase["expected_cycles"]
        ):
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
                    "tag": "baseline expectation mismatch",
                    "details": (
                        f"Selected baseline range contains {baseline_cycles} "
                        f"complete cycles; configured expectation is "
                        f"{phase['expected_cycles']}."
                    ),
                }
            )

        if compare_cycles != expected_cycles:
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
                    "tag": "outlier",
                    "details": description,
                }
            )

        baseline_amplitudes = np.asarray(
            [
                row["peak_to_peak_px"]
                for row in baseline_extrema
                if row["peak_to_peak_px"] is not None
            ],
            dtype=float,
        )
        if baseline_amplitudes.size < 3:
            continue

        amplitude_median = float(np.median(baseline_amplitudes))
        amplitude_mad = float(
            np.median(np.abs(baseline_amplitudes - amplitude_median))
        )
        threshold = n_sigmas * 1.4826 * amplitude_mad
        if amplitude_mad == 0:
            threshold = max(abs(amplitude_median) * 1e-6, 1e-9)

        for row in compare_extrema:
            amplitude = row["peak_to_peak_px"]
            if amplitude is None or abs(amplitude - amplitude_median) <= threshold:
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
                    "expected": amplitude_median,
                    "observed": float(amplitude),
                    "ratio": (
                        float(amplitude / amplitude_median)
                        if amplitude_median
                        else ""
                    ),
                    "baseline_pos": baseline_position,
                    "compare_pos": float(row["position_px"]),
                    "residual": "",
                    "tag": "outlier",
                    "details": (
                        f"Peak-to-peak amplitude differs from the baseline "
                        f"median by more than {n_sigmas:g} robust sigmas."
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
        self.compare_curve = self.plot.plot(
            pen=pg.mkPen(color="#ff7f50", width=2),
            name="Compare",
        )
        for curve in (self.baseline_curve, self.compare_curve):
            curve.setDownsampling(auto=True, method="subsample")
            curve.setClipToView(True)

        self.outlier_plot = self.plot.plot(
            pen=None,
            symbol="O",
            symbolSize=8,
            symbolPen=pg.mkPen(color=(255, 80, 80), width=3),
            symbolBrush=pg.mkBrush(255, 80, 80),
            name="Outliers",
        )
        layout.addWidget(self.graph_layout)

        controls = QHBoxLayout()
        baseline_btn = QPushButton("Load baseline CSV…")
        baseline_btn.clicked.connect(self._load_baseline)
        controls.addWidget(baseline_btn)

        compare_btn = QPushButton("Load compare CSV…")
        compare_btn.clicked.connect(self._load_compare)
        controls.addWidget(compare_btn)

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
        controls.addWidget(QLabel("Outlier sensitivity (robust sigmas):"))
        self.sensitivity_spin = QDoubleSpinBox()
        self.sensitivity_spin.setRange(0.1, 10.0)
        self.sensitivity_spin.setDecimals(1)
        self.sensitivity_spin.setSingleStep(0.5)
        self.sensitivity_spin.setValue(3.0)
        self.sensitivity_spin.valueChanged.connect(self._on_sensitivity_changed)
        controls.addWidget(self.sensitivity_spin)
        controls.addStretch(1)
        layout.addLayout(controls)

        phase_controls = QHBoxLayout()
        phase_controls.addWidget(QLabel("Baseline behavior:"))
        self.phase_combo = QComboBox()
        self.phase_combo.setEditable(True)
        self.phase_combo.addItems(["Phase 1", "Blurb", "Continuation", "Other"])
        self.phase_combo.currentTextChanged.connect(
            self._on_phase_label_changed
        )
        phase_controls.addWidget(self.phase_combo)
        phase_controls.addWidget(QLabel("Expected cycles (0 = baseline count):"))
        self.expected_cycles_spin = QSpinBox()
        self.expected_cycles_spin.setRange(0, 10000)
        self.expected_cycles_spin.setValue(11)
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
        phase_controls.addStretch(1)
        layout.addLayout(phase_controls)

        self.phase_table = QTableWidget(0, 4)
        self.phase_table.setHorizontalHeaderLabels(
            ["Behavior", "Start (s)", "End (s)", "Expected cycles"]
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
        self._phase_ranges = []
        self.phase_table.setRowCount(0)
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
        self.expected_cycles_spin.setValue(11 if label == "Phase 1" else 0)

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
            values = (
                phase["label"],
                f"{phase['start_s']:.4f}",
                f"{phase['end_s']:.4f}",
                str(phase["expected_cycles"] or "baseline"),
            )
            for column, value in enumerate(values):
                self.phase_table.setItem(row, column, QTableWidgetItem(value))
        self.remove_phase_btn.setEnabled(bool(self._phase_ranges))

    def _on_phase_selection_changed(self):
        selected = self.phase_table.selectionModel().selectedRows()
        self.remove_phase_btn.setEnabled(bool(selected))

    def _use_mm(self):
        series = [item for item in (self._baseline, self._compare) if item is not None]
        if not series:
            return False
        return all(np.isfinite(item["mm"]).any() for item in series)

    def _align_and_redraw(self, estimate_offset):
        self._outlier_rows = []
        self.outlier_plot.setData([], [])
        use_mm = self._use_mm()
        unit = "mm" if use_mm else "px"
        self.plot.setLabel("left", f"Position ({unit}, mean subtracted)")

        prepared = {}
        try:
            if self._baseline is not None:
                prepared["baseline"] = _series_position(self._baseline, use_mm)
            if self._compare is not None:
                prepared["compare"] = _series_position(self._compare, use_mm)
        except Exception as exc:
            QMessageBox.warning(self, "Compare", str(exc))
            return

        if "baseline" in prepared:
            t_b, y_b, mean_b = prepared["baseline"]
            self.baseline_curve.setData(x=t_b, y=y_b)
        else:
            self.baseline_curve.setData([], [])
            t_b = y_b = None
            mean_b = None

        both = "baseline" in prepared and "compare" in prepared
        self.offset_spin.setEnabled(both)
        if both and estimate_offset:
            t_c, y_c, _mean_c = prepared["compare"]
            offset = estimate_phase_offset(t_b, y_b, t_c, y_c)
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
            ot, oy, ob, residual = comparison_outliers(
                t_b, y_b, t_c, y_c, offset, n_sigmas=sensitivity
            )
            self.outlier_plot.setData(x=ot, y=oy)
            self._outlier_rows = [
                {
                    "finding_type": "point_residual",
                    "phase": "",
                    "time_s": float(ot[i]),
                    "expected": "",
                    "observed": "",
                    "ratio": "",
                    "baseline_pos": float(ob[i]),
                    "compare_pos": float(oy[i]),
                    "residual": float(residual[i]),
                    "tag": "outlier",
                    "details": "Point residual exceeds the overlap MAD threshold.",
                }
                for i in range(ot.size)
            ]
            behavior_findings = behavior_outliers(
                t_b,
                y_b,
                t_c,
                y_c,
                offset,
                self._phase_ranges,
                n_sigmas=sensitivity,
            )
            self._outlier_rows.extend(behavior_findings)
            plotted_findings = [
                row
                for row in behavior_findings
                if row["finding_type"] == "peak_to_peak_amplitude"
            ]
            if plotted_findings:
                self.outlier_plot.setData(
                    x=np.concatenate(
                        (ot, [row["time_s"] for row in plotted_findings])
                    ),
                    y=np.concatenate(
                        (oy, [row["compare_pos"] for row in plotted_findings])
                    ),
                )
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
            parts.append(
                f"Phase offset {offset:.4f} s. Point outliers: "
                f"{sum(row['finding_type'] == 'point_residual' for row in findings)}; "
                f"cycle/amplitude findings: "
                f"{len(cycle_findings) + amplitude_findings}"
            )
            parts.extend(row["details"] for row in cycle_findings)
        if not self._phase_ranges and self._baseline is not None:
            parts.append(
                "Select and add baseline behavior ranges to compare cycle counts "
                "and peak-to-peak amplitudes."
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
