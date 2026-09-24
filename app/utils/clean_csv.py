import os
import sys
import numpy as np
import pandas as pd

# Allow `python clean_csv.py` from app/utils (or elsewhere) to import windows.*
_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox
)
from PyQt5.QtCore import Qt
import pyqtgraph as pg

from windows.graph_viewbox import GraphViewBox

# Match main-app plot look; keep antialias off for large oscilloscope traces
pg.setConfigOption('background', '#1e1e1e')
pg.setConfigOption('foreground', '#dcdcdc')
pg.setConfigOptions(antialias=False)


class Plot_csv(QWidget):

    def __init__(self, dataframe, title="Cleaned oscilloscope data"):
        super().__init__()
        self.setWindowTitle(title)
        self.resize(1100, 700)
        self.df = dataframe

        layout = QVBoxLayout(self)

        # Same interactive graph pattern as AppWindow.create_graph_layout
        self.graph_layout = pg.GraphicsLayoutWidget()
        self.plot = self.graph_layout.addPlot(
            title=title,
            viewBox=GraphViewBox(),
        )
        self.graph_layout.setToolTip(
            "Wheel: zoom both axes | Ctrl+wheel/right-drag: X axis | "
            "Shift+wheel/right-drag: Y axis | Left-drag: pan"
        )
        self.plot.showGrid(x=True, y=True)
        self.plot.setLabel("bottom", "Time", units="s")
        self.plot.setLabel("left", "Voltage", units="V")

        self.curve = self.plot.plot(
            pen=pg.mkPen(color="#03fcc2", width=1.5),
            name="Voltage",
        )
        self.curve.setDownsampling(auto=True, method="subsample")
        self.curve.setClipToView(True)
        self.curve.setData(
            x=self.df["Second"].values,
            y=self.df["Volt"].values,
        )
        layout.addWidget(self.graph_layout)

        # Crosshairs
        self.v_line = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen("#777", style=Qt.DashLine)
        )
        self.h_line = pg.InfiniteLine(
            angle=0, movable=False, pen=pg.mkPen("#777", style=Qt.DashLine)
        )
        self.plot.addItem(self.v_line, ignoreBounds=True)
        self.plot.addItem(self.h_line, ignoreBounds=True)

        self.coordinates = QLabel("Move the cursor over the plot to inspect a point")
        self.coordinates.setStyleSheet("font-size: 13px; font-weight: bold; padding: 4px;")
        layout.addWidget(self.coordinates)

        layout.addWidget(self._create_axis_control_panel())

        self.plot.scene().sigMouseMoved.connect(self._show_coordinates)

    def _create_axis_control_panel(self):
        """Creates interactive controls to adjust X and Y axis bounds manually."""
        panel = QGroupBox("Axis Limits Control")
        panel_layout = QHBoxLayout(panel)

        panel_layout.addWidget(QLabel("Time Min:"))
        self.x_min_input = QLineEdit()
        panel_layout.addWidget(self.x_min_input)

        panel_layout.addWidget(QLabel("Time Max:"))
        self.x_max_input = QLineEdit()
        panel_layout.addWidget(self.x_max_input)

        panel_layout.addWidget(QLabel("Volt Min:"))
        self.y_min_input = QLineEdit()
        panel_layout.addWidget(self.y_min_input)

        panel_layout.addWidget(QLabel("Volt Max:"))
        self.y_max_input = QLineEdit()
        panel_layout.addWidget(self.y_max_input)

        apply_btn = QPushButton("Apply Bounds")
        apply_btn.clicked.connect(self._apply_axis_bounds)
        panel_layout.addWidget(apply_btn)

        reset_btn = QPushButton("Reset View")
        reset_btn.clicked.connect(self._reset_view)
        panel_layout.addWidget(reset_btn)

        return panel

    def _show_coordinates(self, pos):
        """Update crosshair location and coordinate text on mouse hover."""
        if self.plot.sceneBoundingRect().contains(pos):
            mouse_point = self.plot.vb.mapSceneToView(pos)
            x, y = mouse_point.x(), mouse_point.y()
            self.v_line.setPos(x)
            self.h_line.setPos(y)
            self.coordinates.setText(f"Time: {x:.6g} s    Voltage: {y:.6g} V")

    def _apply_axis_bounds(self):
        """Update chart view using explicit input values."""
        try:
            if self.x_min_input.text() and self.x_max_input.text():
                x_min = float(self.x_min_input.text())
                x_max = float(self.x_max_input.text())
                self.plot.setXRange(x_min, x_max, padding=0)

            if self.y_min_input.text() and self.y_max_input.text():
                y_min = float(self.y_min_input.text())
                y_max = float(self.y_max_input.text())
                self.plot.setYRange(y_min, y_max, padding=0)
        except ValueError:
            self.coordinates.setText("Error: Enter valid numeric values for bounds.")

    def _reset_view(self):
        """Reset plot view back to full dataset extent."""
        self.plot.enableAutoRange()
        self.x_min_input.clear()
        self.x_max_input.clear()
        self.y_min_input.clear()
        self.y_max_input.clear()


def show_interactive_plot(dataframe, title="Cleaned oscilloscope data"):
        """Display a cleaned dataframe in a pannable and zoomable PyQtGraph window."""
        app = QApplication.instance()
        owns_app = app is None
        if owns_app:
            app = QApplication(sys.argv)
        window = Plot_csv(dataframe, title=title)
        window.show()
        if owns_app:
            app.exec_()
        return window


def jump_mask(voltages, dv_max=0.75):
    """Keep samples whose step from the previous point stays within dv_max volts."""
    v = np.asarray(voltages, dtype=float)
    if len(v) == 0:
        return np.array([], dtype=bool)
    dv = np.abs(np.diff(v, prepend=v[0]))
    # First sample has no predecessor; always keep it.
    dv[0] = 0.0
    return dv <= dv_max


def hampel_mask(voltages, window=101, n_sigmas=3.0):
    """
    Keep samples within n_sigmas of the rolling median (Hampel / MAD filter).
    window should be odd so the window is centered on each sample.
    """
    if window % 2 == 0:
        window += 1
    s = pd.Series(np.asarray(voltages, dtype=float))
    if len(s) == 0:
        return np.array([], dtype=bool)

    min_periods = max(3, window // 2)
    med = s.rolling(window, center=True, min_periods=min_periods).median()
    abs_dev = (s - med).abs()
    mad = abs_dev.rolling(window, center=True, min_periods=min_periods).median()
    # 1.4826 scales MAD to roughly match a normal-distribution sigma.
    # MAD == 0 on flat/quantized stretches: keep those points (no local spread).
    sigma = 1.4826 * mad
    keep = (sigma == 0) | (abs_dev <= n_sigmas * sigma)
    # If the rolling window is undefined at the edges, fall back to keeping.
    return keep.fillna(True).to_numpy()


def remove_voltage_outliers(df, dv_max=0.75, hampel_window=101, hampel_n_sigmas=3.0):
    """
    Drop sparse invalid floats that survive numeric coerce + voltage bounds
    (e.g. near-zero spikes from corrupted SDS CSV rows).
    Applies a neighbor jump filter, then a Hampel (rolling MAD) filter.
    """
    before = len(df)
    if before == 0:
        return df

    keep_jump = jump_mask(df["Volt"].values, dv_max=dv_max)
    df = df.loc[keep_jump].reset_index(drop=True)
    after_jump = len(df)

    keep_hampel = hampel_mask(
        df["Volt"].values,
        window=hampel_window,
        n_sigmas=hampel_n_sigmas,
    )
    df = df.loc[keep_hampel].reset_index(drop=True)
    after_hampel = len(df)

    print(
        f"Outlier filter: {before} -> {after_jump} after jump "
        f"(dropped {before - after_jump}), "
        f"-> {after_hampel} after Hampel "
        f"(dropped {after_jump - after_hampel})"
    )
    return df


def clean_csv(
    filename,
    out_path=None,
    show_plot=False,
    outlier_filter=True,
    dv_max=0.75,
    hampel_window=101,
    hampel_n_sigmas=3.0,
):
        """
        Read CSV, set time column bounds, drop invalid rows, and write cleaned CSV.

        After numeric coerce and user bounds, optionally removes statistical
        outliers via neighbor jump + Hampel (MAD) filters.
        """
        if not os.path.exists(filename):
            raise FileNotFoundError(f"File {filename} not found")

        # 1. Read CSV dynamically finding numeric data (skipping oscilloscope header metadata)
        try:
            df = pd.read_csv(filename, usecols=[0, 1], names=["Second", "Volt"], dtype=str, header=None, on_bad_lines='skip')


        except Exception as e:
            raise ValueError(f"Failed to parse oscilloscope CSV: {e}")

        # 2. Coerce string inputs to numeric float (invalid strings become NaN)
        df["Second"] = pd.to_numeric(df["Second"], errors="coerce")
        df["Volt"] = pd.to_numeric(df["Volt"], errors="coerce")

        # 3. Drop NaNs, infinite values, and non-physical oscilloscope voltage spikes
        df = df.dropna(subset=["Second", "Volt"])
        df = df[np.isfinite(df["Second"]) & np.isfinite(df["Volt"])]

        # Filter out corrupted voltage values outside standard physical oscilloscope range
        # Adjust min_voltage and max_voltage according to your channel/probe spec
        min_voltage = float(input("input Min Voltage: "))
        max_voltage = float(input("input Max Voltage: "))

        df = df[(df["Volt"] >= min_voltage) & (df["Volt"] <= max_voltage)]
        # 4. Get bounds and filter time interval
        time_lower_bound = float(input("Enter lower bound for time (s): "))
        time_upper_bound = float(input("Enter upper bound for time (s): "))

        df = df[(df["Second"] >= time_lower_bound) & (df["Second"] <= time_upper_bound)]

        # 5. Deduplicate timestamps and sort (required before jump / rolling filters)
        df = df.sort_values("Second").drop_duplicates(subset="Second")
        df = df.reset_index(drop=True)

        # 6. Statistical outlier removal for corrupt-but-numeric spikes
        if outlier_filter and len(df) > 0:
            df = remove_voltage_outliers(
                df,
                dv_max=dv_max,
                hampel_window=hampel_window,
                hampel_n_sigmas=hampel_n_sigmas,
            )

        print(f"Rows in cleaned dataframe: {len(df)}")

        if out_path is None:
            out_path = os.path.join(
                os.path.dirname(os.path.abspath(filename)),
                f"cleaned_{os.path.basename(filename)}",
            )

        df.to_csv(out_path, index=False)

        if show_plot:
            show_interactive_plot(df, title=f"Cleaned: {os.path.basename(filename)}")

        return out_path


if __name__ == "__main__":
        # Paths are relative to the repo root
        _repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        input_file = os.path.join(_repo_root, "data", "csv", "OscilliscopeCSV(in).csv")
        output_file = os.path.join(_repo_root, "data", "csv", "OscilliscopeCSV(out).csv")
        clean_csv(input_file, output_file, show_plot=True)
