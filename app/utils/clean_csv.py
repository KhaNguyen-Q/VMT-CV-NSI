import os
import sys
import numpy as np
import pandas as pd

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox
)
from PyQt5.QtCore import Qt
import pyqtgraph as pg

# Configure global pyqtgraph appearance
pg.setConfigOption('background', '#1e1e1e')
pg.setConfigOption('foreground', '#dcdcdc')
pg.setConfigOptions(antialias=True)


class Plot_csv(QWidget):


    def __init__(self, dataframe, title="Cleaned oscilloscope data"):
        super().__init__()
        self.setWindowTitle(title)
        self.resize(1100, 700)
        self.df = dataframe

        layout = QVBoxLayout(self)

        # 1. Main Plot Widget
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setLabel('bottom', 'Time', units='s')
        self.plot_widget.setLabel('left', 'Voltage', units='V')
        self.plot_widget.setTitle(title)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.35)

        # Plot data line
        self.curve = self.plot_widget.plot(
            self.df["Second"].values,
            self.df["Volt"].values,
            pen=pg.mkPen(color="#03fcc2", width=1.5),
            name="Voltage"
        )
        layout.addWidget(self.plot_widget)

        # 2. Interactive Crosshairs
        self.v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('#777', style=Qt.DashLine))
        self.h_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen('#777', style=Qt.DashLine))
        self.plot_widget.addItem(self.v_line, ignoreBounds=True)
        self.plot_widget.addItem(self.h_line, ignoreBounds=True)

        # 3. Mouse Coordinate Readout
        self.coordinates = QLabel("Move the cursor over the plot to inspect a point")
        self.coordinates.setStyleSheet("font-size: 13px; font-weight: bold; padding: 4px;")
        layout.addWidget(self.coordinates)

        # 4. Manual Axis Adjustment Panel
        layout.addWidget(self._create_axis_control_panel())

        # Connect signals
        self.plot_widget.scene().sigMouseMoved.connect(self._show_coordinates)




    def _create_axis_control_panel(self):
        """Creates interactive controls to adjust X and Y axis bounds manually."""
        panel = QGroupBox("Axis Limits Control")
        panel_layout = QHBoxLayout(panel)

        # Time (X) Inputs
        panel_layout.addWidget(QLabel("Time Min:"))
        self.x_min_input = QLineEdit()
        panel_layout.addWidget(self.x_min_input)

        panel_layout.addWidget(QLabel("Time Max:"))
        self.x_max_input = QLineEdit()
        panel_layout.addWidget(self.x_max_input)

        # Voltage (Y) Inputs
        panel_layout.addWidget(QLabel("Volt Min:"))
        self.y_min_input = QLineEdit()
        panel_layout.addWidget(self.y_min_input)

        panel_layout.addWidget(QLabel("Volt Max:"))
        self.y_max_input = QLineEdit()
        panel_layout.addWidget(self.y_max_input)

        # Action Buttons
        apply_btn = QPushButton("Apply Bounds")
        apply_btn.clicked.connect(self._apply_axis_bounds)
        panel_layout.addWidget(apply_btn)

        reset_btn = QPushButton("Reset View")
        reset_btn.clicked.connect(self._reset_view)
        panel_layout.addWidget(reset_btn)

        return panel

    def _show_coordinates(self, pos):
        """Update crosshair location and coordinate text on mouse hover."""
        mouse_point = self.plot_widget.plotItem.vb.mapSceneToView(pos)
        if self.plot_widget.plotItem.sceneBoundingRect().contains(pos):
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
                self.plot_widget.setXRange(x_min, x_max, padding=0)

            if self.y_min_input.text() and self.y_max_input.text():
                y_min = float(self.y_min_input.text())
                y_max = float(self.y_max_input.text())
                self.plot_widget.setYRange(y_min, y_max, padding=0)
        except ValueError:
            self.coordinates.setText("Error: Enter valid numeric values for bounds.")

    def _reset_view(self):
        """Reset plot view back to full dataset extent."""
        self.plot_widget.enableAutoRange()
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


def clean_csv(filename, out_path=None, show_plot=False):
        """
        Read CSV, set time column bounds, drop invalid rows, and write cleaned CSV.
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

        # 5. Deduplicate timestamps and sort
        df = df.sort_values("Second").drop_duplicates(subset="Second")
        df = df.reset_index(drop=True)

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
        # Example usage
        input_file = r"C:\Users\Ben\Desktop\VMT\VMT-CV-NSI\data\csv\OscilliscopeCSV(in).csv"
        output_file = r"C:\Users\Ben\Desktop\VMT\VMT-CV-NSI\data\csv\OscilliscopeCSV(out).csv"
        clean_csv(input_file, output_file, show_plot=True)
