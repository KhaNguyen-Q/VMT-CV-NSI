import os
import math
from PyQt5.QtCore import pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QLabel,
    QGridLayout,
    QWidget,
    QPushButton,
    QMessageBox,
    QDoubleSpinBox,
    QSpinBox,
    QComboBox,
)
from utils.utils import save_json, get_project_root

project_root = get_project_root(os.path.dirname(os.path.abspath(__file__)))
data_folder = os.path.join(project_root, "data")


class AnalyzeWidget(QWidget):
    analyze_signal = pyqtSignal()
    export_csv_signal = pyqtSignal()
    export_graph_signal = pyqtSignal()
    peak_to_peak_signal = pyqtSignal()
    zero_y_signal = pyqtSignal()
    mm_per_pixel_changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Always stored as [A, gamma, w, phi, C]; gamma=0 for simple fit
        self.params = [1.0, 0.0, 1.0, 0.0, 0.0]
        self.fit_model_name = "damped"
        self.visual_params = {}

        self.init_ui()

    def init_ui(self):
        self.create_param_labels()
        self.create_value_labels()
        self.create_buttons()
        self.create_layout()
        self.connect_buttons()

    def create_param_labels(self):
        self.param_labels = [
            QLabel("A (amplitude px): "),
            QLabel("γ (damping): "),
            QLabel("ω (rad/s): "),
            QLabel("ϕ (phase): "),
            QLabel("C (equilibrium px): "),
        ]

    def create_value_labels(self):
        self.param_value_labels = [
            QLabel("0"),
            QLabel("0"),
            QLabel("0"),
            QLabel("0"),
            QLabel("0"),
        ]

        self.period_value_label = QLabel("0 s")
        self.frequency_value_label = QLabel("0 Hz")
        self.omega_nought_value_label = QLabel("0")
        self.k_over_m_value_label = QLabel("0")
        self.axis_value_label = QLabel("-")
        self.fps_value_label = QLabel("-")
        self.time_scale_value_label = QLabel("1.0000")
        self.equilibrium_value_label = QLabel("0 px")
        self.p2p_px_label = QLabel("0 px")
        self.p2p_mm_label = QLabel("0 mm")
        self.fft_freq_label = QLabel("0 Hz")
        self.amp_mm_label = QLabel("0 mm")
        self.tip_length_px_label = QLabel("-")
        self.tip_length_px_label.setToolTip(
            "Tip L = median full-frame bbox major length from the HSV mask "
            "(same space as displacement x_px/y_px). "
            "Mask may show a smaller detect L for display only. "
            "mm/pixel ≈ tip_mm / Tip_L. Empty mask clears Tip L."
        )
        self.tip_length_detect_px_label = QLabel("-")
        self.tip_length_detect_px_label.setToolTip(
            "Detect L = major bbox length in the detection image "
            "(ROI crop and/or downscaled frame). Useful when the on-video "
            "overlay is clipped. Same as Tip L when scale is 1.0."
        )

        self.mm_per_pixel_spin = QDoubleSpinBox()
        self.mm_per_pixel_spin.setDecimals(6)
        self.mm_per_pixel_spin.setRange(0.0, 100.0)
        self.mm_per_pixel_spin.setSingleStep(0.01)
        self.mm_per_pixel_spin.setValue(0.0)
        self.mm_per_pixel_spin.setToolTip(
            "mm per pixel from a known scale in the tip plane. 0 = pixels only."
        )

        self.visual_gain_spin = QSpinBox()
        self.visual_gain_spin.setRange(1, 100)
        self.visual_gain_spin.setValue(1)
        self.visual_gain_spin.setToolTip(
            "Plot/overlay visual gain only. Does not change stored measurements."
        )

        self.time_scale_spin = QDoubleSpinBox()
        self.time_scale_spin.setDecimals(4)
        self.time_scale_spin.setRange(0.01, 100.0)
        self.time_scale_spin.setSingleStep(0.01)
        self.time_scale_spin.setValue(1.0)
        self.time_scale_spin.setToolTip(
            "Multiply file timestamps by this. Remapped clip: "
            "original_duration / file_duration (e.g. 72.5/39.5 ≈ 1.8354). "
            "Scales plot, CSV, and fits. Use 1.0 when file FPS matches real time."
        )

        self.fit_model_combo = QComboBox()
        self.fit_model_combo.addItem("Damped (underdamped)", "damped")
        self.fit_model_combo.addItem("Simple (undamped)", "simple")
        self.fit_model_combo.setToolTip(
            "Damped: A·e^(-γt)·cos(ωt+φ)+C\n"
            "Simple: A·cos(ωt+φ)+C (γ fixed at 0)"
        )

    def create_buttons(self):
        self.curve_fit_button = QPushButton("Estimate")
        self.save_button = QPushButton("Save Params")
        self.export_csv_button = QPushButton("Export CSV")
        self.export_graph_button = QPushButton("Export Graph")
        self.export_graph_button.setEnabled(False)
        self.export_graph_button.setToolTip(
            "Save Displacement vs Time (mm) as PNG. "
            "Requires mm/pixel > 0 and a successful Estimate."
        )
        self.zero_y_button = QPushButton("Zero Y (mean)")
        self.zero_y_button.setToolTip(
            "Subtract mean position so the plot / series is centered at 0. "
            "Does not change time or wavelength. Apply after tracking stops."
        )
        self.peak_to_peak_button = QPushButton("Peak to Peak")
        self.peak_to_peak_button.setEnabled(False)
        self.peak_to_peak_button.setToolTip(
            "Find local extrema, overlay markers, and save "
            "peak_to_peak_{axis}.csv + PNG."
        )

    def create_layout(self):
        layout = QGridLayout()

        for row, (param_label, param_value_label) in enumerate(
            zip(self.param_labels, self.param_value_labels)
        ):
            layout.addWidget(param_label, row, 0)
            layout.addWidget(param_value_label, row, 1)

        layout.addWidget(QLabel("Period T: "), 0, 2)
        layout.addWidget(self.period_value_label, 0, 3)
        layout.addWidget(QLabel("Fit f: "), 1, 2)
        layout.addWidget(self.frequency_value_label, 1, 3)
        layout.addWidget(QLabel("ω₀: "), 2, 2)
        layout.addWidget(self.omega_nought_value_label, 2, 3)
        layout.addWidget(QLabel("k/m (= ω₀²): "), 3, 2)
        layout.addWidget(self.k_over_m_value_label, 3, 3)
        layout.addWidget(QLabel("Axis: "), 4, 2)
        layout.addWidget(self.axis_value_label, 4, 3)

        layout.addWidget(QLabel("Equilibrium: "), 0, 4)
        layout.addWidget(self.equilibrium_value_label, 0, 5)
        layout.addWidget(QLabel("P2P (px): "), 1, 4)
        layout.addWidget(self.p2p_px_label, 1, 5)
        layout.addWidget(QLabel("P2P (mm): "), 2, 4)
        layout.addWidget(self.p2p_mm_label, 2, 5)
        layout.addWidget(QLabel("FFT f: "), 3, 4)
        layout.addWidget(self.fft_freq_label, 3, 5)
        layout.addWidget(QLabel("A (mm): "), 4, 4)
        layout.addWidget(self.amp_mm_label, 4, 5)
        layout.addWidget(QLabel("File FPS: "), 5, 4)
        layout.addWidget(self.fps_value_label, 5, 5)
        tip_l_label = QLabel("Tip L (px): ")
        tip_l_label.setToolTip(self.tip_length_px_label.toolTip())
        layout.addWidget(tip_l_label, 6, 4)
        layout.addWidget(self.tip_length_px_label, 6, 5)
        detect_l_label = QLabel("Detect L (px): ")
        detect_l_label.setToolTip(self.tip_length_detect_px_label.toolTip())
        layout.addWidget(detect_l_label, 7, 4)
        layout.addWidget(self.tip_length_detect_px_label, 7, 5)

        layout.addWidget(QLabel("mm/pixel: "), 5, 0)
        layout.addWidget(self.mm_per_pixel_spin, 5, 1)
        layout.addWidget(QLabel("Visual gain: "), 5, 2)
        layout.addWidget(self.visual_gain_spin, 5, 3)

        layout.addWidget(QLabel("Time scale: "), 6, 0)
        layout.addWidget(self.time_scale_spin, 6, 1)
        layout.addWidget(QLabel("Applied scale: "), 6, 2)
        layout.addWidget(self.time_scale_value_label, 6, 3)

        layout.addWidget(QLabel("Fit model: "), 7, 0)
        layout.addWidget(self.fit_model_combo, 7, 1, 1, 2)

        layout.addWidget(self.curve_fit_button, 8, 0)
        layout.addWidget(self.save_button, 8, 1)
        layout.addWidget(self.export_csv_button, 8, 2)
        layout.addWidget(self.export_graph_button, 8, 3)
        layout.addWidget(self.zero_y_button, 8, 4)
        layout.addWidget(self.peak_to_peak_button, 8, 5)

        self.setLayout(layout)

    def connect_buttons(self):
        self.curve_fit_button.clicked.connect(self.trigger_analysis)
        self.save_button.clicked.connect(self.save_params)
        self.export_csv_button.clicked.connect(self.export_csv_signal.emit)
        self.export_graph_button.clicked.connect(self.export_graph_signal.emit)
        self.peak_to_peak_button.clicked.connect(self.peak_to_peak_signal.emit)
        self.zero_y_button.clicked.connect(self.zero_y_signal.emit)
        self.mm_per_pixel_spin.valueChanged.connect(
            lambda v: self.mm_per_pixel_changed.emit(float(v))
        )

    def set_export_graph_enabled(self, enabled: bool):
        self.export_graph_button.setEnabled(bool(enabled))

    def set_peak_to_peak_enabled(self, enabled: bool):
        self.peak_to_peak_button.setEnabled(bool(enabled))

    def trigger_analysis(self):
        self.analyze_signal.emit()

    def fit_model(self):
        return self.fit_model_combo.currentData()

    def mm_per_pixel(self):
        return float(self.mm_per_pixel_spin.value())

    def visual_gain(self):
        return int(self.visual_gain_spin.value())

    def time_scale(self):
        return float(self.time_scale_spin.value())

    def update_params(self, params, analytics=None, fit_model="damped"):
        """
        params: sequence [A, gamma, w, phi, C]
        For simple fits, gamma should be 0.
        """
        self.params = list(params)
        self.fit_model_name = fit_model
        for i, value_label in enumerate(self.param_value_labels):
            value_label.setText(f"{params[i]:.3f}")

        omega, damp_coff = params[2], params[1]
        amp_px = abs(params[0])
        if fit_model == "simple":
            omega_0 = abs(omega)
        else:
            omega_0 = math.sqrt(omega**2 + damp_coff**2)
        period = (2 * math.pi / omega) if omega != 0 else float("inf")
        frequency = omega / (2 * math.pi) if omega != 0 else 0.0
        k_over_m = omega_0**2
        mpp = self.mm_per_pixel()

        self.period_value_label.setText(f"{period:.4f} s")
        self.frequency_value_label.setText(f"{frequency:.4f} Hz")
        self.omega_nought_value_label.setText(f"{omega_0:.4f}")
        self.k_over_m_value_label.setText(f"{k_over_m:.4f}")
        self.amp_mm_label.setText(f"{amp_px * mpp:.4f} mm" if mpp else "-")

        if analytics:
            p2p_px = analytics.get("p2p_px", 0.0)
            fft_f = analytics.get("fft_hz", 0.0)
            self.p2p_px_label.setText(f"{p2p_px:.3f} px")
            self.p2p_mm_label.setText(f"{p2p_px * mpp:.4f} mm" if mpp else "-")
            self.fft_freq_label.setText(f"{fft_f:.4f} Hz")
            tip_l = analytics.get("tip_length_px")
            if tip_l is not None:
                self.tip_length_px_label.setText(f"{float(tip_l):.2f} px")
            else:
                self.tip_length_px_label.setText("-")
            if "tip_length_detect_px" in analytics:
                detect_l = analytics.get("tip_length_detect_px")
                if detect_l is not None:
                    self.tip_length_detect_px_label.setText(f"{float(detect_l):.2f} px")
                else:
                    self.tip_length_detect_px_label.setText("-")

    def save_params(self):
        A, gamma, w, phi, C = self.params
        tip_l = self.visual_params.get("tip_length_px_med")
        param_json = {
            "model": self.fit_model_name,
            "A": A,
            "gamma": gamma,
            "w": w,
            "phi": phi,
            "C": C,
            "mm_per_pixel": self.mm_per_pixel(),
            "tip_length_px": tip_l,
        }
        save_json(os.path.join(data_folder, "json", "parameters.json"), param_json)
        self.show_message_box("Parameters", "Saved parameters successfully")

    @pyqtSlot(dict)
    def show_params(self, params):
        self.visual_params = params
        axis = params.get("axis", "-")
        self.axis_value_label.setText(str(axis))
        fps = params.get("fps")
        if fps is not None:
            self.fps_value_label.setText(f"{float(fps):.3f}")
        else:
            self.fps_value_label.setText("-")
        time_scale = params.get("time_scale")
        if time_scale is not None:
            self.time_scale_value_label.setText(f"{float(time_scale):.4f}")
        else:
            self.time_scale_value_label.setText("-")
        equilibrium = params.get("equilibrium")
        if equilibrium is not None:
            self.equilibrium_value_label.setText(f"{equilibrium:.2f} px")
        else:
            self.equilibrium_value_label.setText("-")
        tip_l = params.get("tip_length_px_med")
        if tip_l is not None:
            self.tip_length_px_label.setText(f"{float(tip_l):.2f} px")
        else:
            self.tip_length_px_label.setText("-")
        detect_l = params.get("tip_length_detect_px")
        if detect_l is not None:
            self.tip_length_detect_px_label.setText(f"{float(detect_l):.2f} px")
        else:
            self.tip_length_detect_px_label.setText("-")

    def show_message_box(self, title, message):
        message_box = QMessageBox()
        message_box.setWindowTitle(title)
        message_box.setText(message)
        message_box.exec_()
