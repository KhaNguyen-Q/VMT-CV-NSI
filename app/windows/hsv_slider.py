import os
from PyQt5.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QLabel,
    QGridLayout,
    QWidget,
    QSlider,
    QPushButton,
    QMessageBox,
    QComboBox,
)
from utils.utils import load_json, save_json, get_project_root
from processing.detector import DEFAULT_TIP_HSV, normalize_tip_hsv

project_root = get_project_root(os.path.dirname(os.path.abspath(__file__)))
data_folder = os.path.join(project_root, "data")


class HSVSlider(QWidget):
    """Tip color controls: red and/or blue translucent tip + area limits."""

    tip_config_signal = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.initUI()

    def initUI(self):
        loaded = load_json(os.path.join(data_folder, "json", "hsv.json"))
        self.cfg = normalize_tip_hsv(loaded if loaded else DEFAULT_TIP_HSV)

        self.mode_combo = QComboBox(self)
        self.mode_combo.addItems(["Both (red or blue)", "Red only", "Blue only"])
        mode_to_index = {"both": 0, "red": 1, "blue": 2}
        self.mode_combo.setCurrentIndex(mode_to_index.get(self.cfg["mode"], 0))
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        self.sliders = {}
        self.labels = {}
        self.value_labels = {}

        # Shared S/V for translucent tip tuning (applied to active color(s))
        red = self.cfg["red"]
        self.create_slider("smin", 0, 255, red["smin"])
        self.create_slider("smax", 0, 255, red["smax"])
        self.create_slider("vmin", 0, 255, red["vmin"])
        self.create_slider("vmax", 0, 255, red["vmax"])
        self.create_slider("min_area", 1, 20000, self.cfg["min_area"])
        self.create_slider("max_area", 100, 500000, self.cfg["max_area"])

        self.save_button = QPushButton("Save Tip HSV")
        self.save_button.clicked.connect(self.save_hsv)
        self.reset_button = QPushButton("Reset Defaults")
        self.reset_button.clicked.connect(self.reset_defaults)

        layout = QGridLayout()
        layout.addWidget(QLabel("Tip color:"), 0, 0)
        layout.addWidget(self.mode_combo, 0, 1, 1, 2)
        self.add_to_layout(layout, "smin", 1)
        self.add_to_layout(layout, "smax", 2)
        self.add_to_layout(layout, "vmin", 3)
        self.add_to_layout(layout, "vmax", 4)
        self.add_to_layout(layout, "min_area", 5)
        self.add_to_layout(layout, "max_area", 6)
        layout.addWidget(self.save_button, 7, 0)
        layout.addWidget(self.reset_button, 7, 1)
        hint = QLabel(
            "Tip: use Mask view. Lower smin if translucent. "
            "Raise max_area so the whole tip is accepted; min_area rejects noise. "
            "Tracker picks the largest in-range blob and boxes it as the tip ROI."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint, 8, 0, 1, 3)
        self.setLayout(layout)

    def create_slider(self, name, min_val, max_val, value):
        slider = QSlider(Qt.Horizontal)
        label = QLabel(f"{name}:")
        value_label = QLabel(str(value))
        slider.setRange(min_val, max_val)
        slider.setValue(int(value))
        self.sliders[name] = slider
        self.labels[name] = label
        self.value_labels[name] = value_label
        slider.valueChanged.connect(
            lambda v, n=name: self.update_value_label(n, v)
        )
        slider.valueChanged.connect(self.emit_tip_config)

    def add_to_layout(self, layout, name, row):
        layout.addWidget(self.labels[name], row, 0)
        layout.addWidget(self.sliders[name], row, 1)
        layout.addWidget(self.value_labels[name], row, 2)

    def update_value_label(self, name, value):
        self.value_labels[name].setText(str(value))

    def _on_mode_changed(self, _index):
        self.emit_tip_config()

    def _mode_key(self):
        idx = self.mode_combo.currentIndex()
        return {0: "both", 1: "red", 2: "blue"}[idx]

    def current_config(self):
        smin = self.sliders["smin"].value()
        smax = self.sliders["smax"].value()
        vmin = self.sliders["vmin"].value()
        vmax = self.sliders["vmax"].value()
        cfg = normalize_tip_hsv(self.cfg)
        cfg["mode"] = self._mode_key()
        for color in ("red", "blue"):
            cfg[color]["smin"] = smin
            cfg[color]["smax"] = smax
            cfg[color]["vmin"] = vmin
            cfg[color]["vmax"] = vmax
        cfg["min_area"] = self.sliders["min_area"].value()
        cfg["max_area"] = self.sliders["max_area"].value()
        return cfg

    @pyqtSlot()
    def emit_tip_config(self):
        self.tip_config_signal.emit(self.current_config())

    def save_hsv(self):
        cfg = self.current_config()
        self.cfg = cfg
        save_json(os.path.join(data_folder, "json", "hsv.json"), cfg)
        message_box = QMessageBox()
        message_box.setWindowTitle("Tip HSV")
        message_box.setText("Saved tip color settings successfully")
        message_box.exec_()

    def reset_defaults(self):
        self.cfg = normalize_tip_hsv(DEFAULT_TIP_HSV)
        self.mode_combo.setCurrentIndex(0)
        red = self.cfg["red"]
        self.sliders["smin"].setValue(red["smin"])
        self.sliders["smax"].setValue(red["smax"])
        self.sliders["vmin"].setValue(red["vmin"])
        self.sliders["vmax"].setValue(red["vmax"])
        self.sliders["min_area"].setValue(self.cfg["min_area"])
        self.sliders["max_area"].setValue(self.cfg["max_area"])
        self.emit_tip_config()
