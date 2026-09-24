from PyQt5 import QtGui
from PyQt5.QtWidgets import (
    QWidget,
    QLabel,
    QGridLayout,
    QPushButton,
    QFileDialog,
    QComboBox,
    QInputDialog,
    QHBoxLayout,
    QSplitter,
    QMessageBox,
    QSpinBox,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap, QColor
import pyqtgraph as pg
import os
import cv2
from PyQt5.QtCore import pyqtSlot
import numpy as np
from scipy.optimize import curve_fit
from processing.video_thread import VideoThread
from windows.hsv_slider import HSVSlider
from windows.analyze_widget import AnalyzeWidget
from utils.utils import (
    underdamped_harmonic_oscillator,
    simple_harmonic_oscillator,
    upper_decaying_component_curve,
    lower_decaying_component_curve,
    peak_to_peak,
    dominant_fft_frequency,
    get_project_root,
    find_peak_to_peak_extrema,
    write_peak_to_peak_csv,
)
from utils.plot_export import (
    series_to_mm,
    export_displacement_vs_time,
    default_plot_path,
    default_peak_to_peak_plot_path,
    export_peak_to_peak_plot,
)
from utils.contansts import (
    lower_bounds,
    upper_bounds,
    simple_lower_bounds,
    simple_upper_bounds,
)

project_root = get_project_root(os.path.dirname(os.path.abspath(__file__)))


class GraphViewBox(pg.ViewBox):
    def _axis_from_modifiers(self, modifiers):
        if modifiers & Qt.ControlModifier:
            return 0
        if modifiers & Qt.ShiftModifier:
            return 1
        return None

    def wheelEvent(self, event, axis=None):
        if axis is None:
            axis = self._axis_from_modifiers(event.modifiers())
        super().wheelEvent(event, axis=axis)

    def mouseDragEvent(self, event, axis=None):
        if axis is None and event.button() == Qt.RightButton:
            axis = self._axis_from_modifiers(event.modifiers())
        super().mouseDragEvent(event, axis=axis)


class AppWindow(QWidget):
    PLOT_UPDATE_EVERY = 10

    def __init__(self):
        super().__init__()

        self.postion_plot_color = QColor(3, 252, 194)
        self.fitted_plot_color = QColor(255, 127, 80)

        self.setWindowTitle("1D Spring/Slider Oscillator")
        self.disply_width = 1280
        self.display_height = 720
        self.video_path = None
        self.initial_guess = [1.0, 0.1, 1.0, 0.0, 0.0]
        self.data_points = []  # (time_s, position_px)
        self._plot_t = []
        self._plot_y = []
        self._plot_since_refresh = 0
        self._y_zeroed = False
        self._tip_length_px_med = None
        self._estimate_done = False
        self.thread = None
        self._hsv_tip_slot = None
        self.selected_display_option = "Image Contours"
        self.selected_mask_option = "Tip Color (Red/Blue)"
        self.selected_motion_axis = "x"
        self.draw_params = True
        self.roi = None  # (x, y, w, h) or None
        self._frame_width = 0
        self._frame_height = 0

        self.create_buttons()
        self.create_labels()
        self.create_graph_layout()
        self.create_hsv_slider()
        self.create_analyze_widget()

        self.create_layout()
        self.setFixedWidth(self.disply_width)

    def create_buttons(self):
        self.run_button = QPushButton("Run", self)
        self.run_button.clicked.connect(self.start_video_thread)
        self.run_button.setEnabled(False)

        self.stop_button = QPushButton("Stop", self)
        self.stop_button.clicked.connect(self.stop_video_thread)
        self.stop_button.setEnabled(False)

        self.select_file_button = QPushButton("Select Video", self)
        self.select_file_button.clicked.connect(self.select_video_file)

        self.webcam_button = QPushButton("Webcam", self)
        self.webcam_button.clicked.connect(self.webcam_selection)

        self.draw_param_button = QPushButton("Hide Param", self)
        self.draw_param_button.clicked.connect(self.draw_param_selection)

        self.url_button = QPushButton("URL", self)
        self.url_button.clicked.connect(self.url_submission)

        self.roi_button = QPushButton("Set ROI", self)
        self.roi_button.clicked.connect(self.select_roi)
        self.roi_button.setEnabled(False)

        self.clear_roi_button = QPushButton("Clear ROI", self)
        self.clear_roi_button.clicked.connect(self.clear_roi)

        self.display_options = QComboBox(self)
        self.display_options.addItems(["Image Contours", "Main Video", "Mask"])
        self.display_options.currentTextChanged.connect(self.display_selection_changed)

        self.mask_options = QComboBox(self)
        self.mask_options.addItems(
            ["Tip Color (Red/Blue)", "Edge Detection", "Circle Detection"]
        )
        self.mask_options.currentTextChanged.connect(self.mask_selection_changed)

        self.axis_options = QComboBox(self)
        self.axis_options.addItems(["Horizontal (x)", "Vertical (y)"])
        self.axis_options.currentTextChanged.connect(self.axis_selection_changed)

        self.frame_width_spin = QSpinBox(self)
        self.frame_width_spin.setRange(0, 16000)
        self.frame_width_spin.setValue(0)
        self.frame_width_spin.setToolTip(
            "Full-frame video width (px). Auto-filled from the file; "
            "edit before Run — used for Tip L / position scale (detect → full)."
        )
        self.frame_height_spin = QSpinBox(self)
        self.frame_height_spin.setRange(0, 16000)
        self.frame_height_spin.setValue(0)
        self.frame_height_spin.setToolTip(
            "Full-frame video height (px). Auto-filled from the file; "
            "edit before Run — used for Tip L / position scale (detect → full)."
        )

    def create_labels(self):
        self.video_label = QLabel("No video selected", self)
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet(
            "font-size: 24px; background-color: black; color: white;"
        )
        self.roi_status_label = QLabel("ROI: full frame")

    def create_graph_layout(self):
        self.graph_layout = pg.GraphicsLayoutWidget()
        self.plot = self.graph_layout.addPlot(
            title="Position vs time",
            viewBox=GraphViewBox(),
        )
        self.graph_layout.setToolTip(
            "Wheel: zoom both axes | Ctrl+wheel/right-drag: X axis | "
            "Shift+wheel/right-drag: Y axis | Left-drag: pan"
        )
        self.plot.showGrid(x=True, y=True)
        self.plot.addLegend()
        self.plot.setLabel("left", "Position (pixels)")
        self.plot.setLabel("bottom", "time (s)")

        self.position_plot_data = self.plot.plot(
            pen=pg.mkPen(color=self.postion_plot_color, width=3),
            symbol="o",
            symbolPen="b",
            symbolBrush=self.postion_plot_color,
            symbolSize=3,
            name="Actual Position",
        )
        self.position_plot_data.setDownsampling(auto=True, method="subsample")
        self.position_plot_data.setClipToView(True)

        self.visual_plot_data = self.plot.plot(
            pen=pg.mkPen(color=(255, 200, 0), width=2, style=Qt.DashLine),
            name="Visual gain",
        )

        self.fitted_plot_data = self.plot.plot(
            pen=pg.mkPen(color=self.fitted_plot_color, width=3),
            name="Fitted Curve",
        )

        self.upper_decay_plot = self.plot.plot(
            pen=pg.mkPen(color="g", width=1),
        )

        self.lower_decay_plot = self.plot.plot(
            pen=pg.mkPen(color="g", width=1),
            name="Decay Curve",
        )

        self.peak_top_plot = self.plot.plot(
            pen=None,
            symbol="t",
            symbolPen=pg.mkPen(color=(255, 127, 14), width=1),
            symbolBrush=pg.mkBrush(255, 127, 14),
            symbolSize=10,
            name="Peaks (top)",
        )
        self.peak_bottom_plot = self.plot.plot(
            pen=None,
            symbol="t1",
            symbolPen=pg.mkPen(color=(23, 190, 207), width=1),
            symbolBrush=pg.mkBrush(23, 190, 207),
            symbolSize=10,
            name="Peaks (bottom)",
        )
        self.peak_plateau_plot = self.plot.plot(
            pen=None,
            symbol="s",
            symbolPen=pg.mkPen(color=(127, 127, 127), width=1),
            symbolBrush=pg.mkBrush(127, 127, 127),
            symbolSize=8,
            name="Peaks (plateau)",
        )

    def create_hsv_slider(self):
        self.hsv_slider = HSVSlider(self)
        self.hsv_slider.setEnabled(False)

    def create_analyze_widget(self):
        self.analyze_widget = AnalyzeWidget(self)
        self.analyze_widget.analyze_signal.connect(self.fit_data_point)
        self.analyze_widget.export_csv_signal.connect(self.export_csv)
        self.analyze_widget.export_graph_signal.connect(self.export_graph)
        self.analyze_widget.peak_to_peak_signal.connect(self.show_peak_to_peak)
        self.analyze_widget.zero_y_signal.connect(self.zero_position_to_mean)
        self.analyze_widget.mm_per_pixel_changed.connect(
            lambda _v: self._refresh_export_graph_button()
        )
        self.analyze_widget.setEnabled(False)
        self._refresh_export_graph_button()
        self._refresh_peak_to_peak_button()

    def _refresh_export_graph_button(self):
        enabled = (
            self._estimate_done
            and self.analyze_widget.mm_per_pixel() > 0
            and len(self.data_points) > 0
        )
        self.analyze_widget.set_export_graph_enabled(enabled)

    def _refresh_peak_to_peak_button(self):
        self.analyze_widget.set_peak_to_peak_enabled(len(self.data_points) >= 8)

    def create_layout(self):
        button_layout = QHBoxLayout()
        button_layout.addWidget(self.run_button)
        button_layout.addWidget(self.stop_button)
        button_layout.addWidget(self.select_file_button)
        button_layout.addWidget(self.webcam_button)
        button_layout.addWidget(self.url_button)
        button_layout.addWidget(self.roi_button)
        button_layout.addWidget(self.clear_roi_button)
        button_layout.addWidget(self.display_options)
        button_layout.addWidget(self.mask_options)
        button_layout.addWidget(self.axis_options)
        button_layout.addWidget(QLabel("Frame W:"))
        button_layout.addWidget(self.frame_width_spin)
        button_layout.addWidget(QLabel("H:"))
        button_layout.addWidget(self.frame_height_spin)
        button_layout.addWidget(self.draw_param_button)

        video_graph_splitter = QSplitter(Qt.Horizontal)
        video_graph_splitter.addWidget(self.video_label)
        video_graph_splitter.addWidget(self.graph_layout)
        video_graph_splitter.setSizes(
            [int(self.display_height * 0.4), int(self.display_height * 0.6)]
        )

        slider_analyze_splitter = QSplitter(Qt.Horizontal)
        slider_analyze_splitter.addWidget(self.hsv_slider)
        slider_analyze_splitter.addWidget(self.analyze_widget)
        slider_analyze_splitter.setSizes(
            [int(self.display_height * 0.4), int(self.display_height * 0.6)]
        )

        grid_layout = QGridLayout()
        grid_layout.addLayout(button_layout, 0, 0)
        grid_layout.addWidget(self.roi_status_label, 1, 0)
        grid_layout.addWidget(video_graph_splitter, 2, 0)
        grid_layout.addWidget(slider_analyze_splitter, 3, 0)
        grid_layout.setRowStretch(0, 0)
        grid_layout.setRowStretch(1, 0)
        grid_layout.setRowStretch(2, 1)
        grid_layout.setRowStretch(3, 0)
        self.setLayout(grid_layout)

    @pyqtSlot()
    def url_submission(self):
        dialog = QInputDialog()
        dialog.setInputMode(QInputDialog.TextInput)
        dialog.setWindowTitle("Submit video URL")
        dialog.setLabelText("URL:")
        dialog.setTextValue("")
        dialog.resize(400, 200)

        pressed = dialog.exec_()

        if pressed == QInputDialog.Accepted:
            self.video_path = dialog.textValue()
            self._probe_and_set_frame_size(self.video_path)
            self.video_label.setText("Video Url Submitted\nSet Frame W/H if needed, then Run")
            self.video_label.adjustSize()
            self.run_button.setEnabled(True)
            self.roi_button.setEnabled(True)

    @pyqtSlot()
    def webcam_selection(self):
        self.video_path = 0
        self._probe_and_set_frame_size(0)
        self.video_label.setText("Webcam selected\nConfirm Frame W/H, then Run")
        self.video_label.adjustSize()
        self.run_button.setEnabled(True)
        self.roi_button.setEnabled(True)

    @pyqtSlot()
    def draw_param_selection(self):
        self.draw_params = not self.draw_params
        self.draw_param_button.setText(
            "Hide Param" if self.draw_params else "Draw Params"
        )

    def _probe_and_set_frame_size(self, video_path):
        """Read container frame size and fill Frame W/H (editable before Run)."""
        cap = cv2.VideoCapture(video_path)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if w < 8 or h < 8:
            ret, frame = cap.read()
            if ret and frame is not None:
                h, w = frame.shape[:2]
        cap.release()
        self._frame_width = max(0, int(w))
        self._frame_height = max(0, int(h))
        self.frame_width_spin.setValue(self._frame_width)
        self.frame_height_spin.setValue(self._frame_height)

    @pyqtSlot()
    def select_video_file(self):
        if self.thread is not None and self.thread.isRunning():
            return
        options = QFileDialog.Options()
        options |= QFileDialog.ReadOnly
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video File",
            "",
            "Video Files (*.mp4 *.avi *.mov *.MOV);;All Files (*)",
            options=options,
        )

        if file_name:
            self.video_path = file_name
            self._probe_and_set_frame_size(file_name)
            self.video_label.setText(
                f"{os.path.basename(file_name)} selected\n"
                f"Frame {self.frame_width_spin.value()}×{self.frame_height_spin.value()} "
                "(edit if wrong) → Set ROI (optional) → Run"
            )
            self.video_label.adjustSize()
            self.run_button.setEnabled(True)
            self.roi_button.setEnabled(True)

    @pyqtSlot()
    def select_roi(self):
        if self.video_path is None:
            return
        if self.thread is not None and self.thread.isRunning():
            QMessageBox.information(self, "ROI", "Stop playback before setting ROI.")
            return

        cap = cv2.VideoCapture(self.video_path)
        ret, frame = cap.read()
        cap.release()
        if not ret:
            QMessageBox.warning(self, "ROI", "Could not read a frame from the video.")
            return

        # Interactive OpenCV ROI on a preview window
        preview = frame.copy()
        cv2.putText(
            preview,
            "Drag tip ROI, ENTER/SPACE confirm, c cancel",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 255),
            2,
        )
        roi = cv2.selectROI("Select tip ROI", preview, showCrosshair=True, fromCenter=False)
        cv2.destroyWindow("Select tip ROI")
        x, y, w, h = [int(v) for v in roi]
        if w < 8 or h < 8:
            QMessageBox.information(self, "ROI", "ROI too small — keeping previous/full frame.")
            return
        self.roi = (x, y, w, h)
        self.roi_status_label.setText(f"ROI: x={x} y={y} w={w} h={h} (native resolution)")

    @pyqtSlot()
    def clear_roi(self):
        self.roi = None
        self.roi_status_label.setText("ROI: full frame")

    @pyqtSlot(int)
    def processing_frame(self, count):
        self.video_label.setText(f"Loading: {count}%")

    @pyqtSlot(np.ndarray)
    def update_image(self, cv_img):
        qt_img = self.convert_cv_qt(cv_img)
        self.video_label.setPixmap(qt_img)

    @pyqtSlot(float, float)
    def update_graph(self, t_sec, pos_px):
        self.data_points.append((t_sec, pos_px))
        self._plot_t.append(t_sec)
        self._plot_y.append(pos_px)
        self._plot_since_refresh += 1
        if self._plot_since_refresh >= self.PLOT_UPDATE_EVERY:
            self._refresh_position_plot()
        if len(self.data_points) == 8:
            self._refresh_peak_to_peak_button()

    def _refresh_position_plot(self):
        if not self._plot_t:
            return
        self.position_plot_data.setData(x=self._plot_t, y=self._plot_y)
        gain = self.analyze_widget.visual_gain()
        if gain > 1 and len(self._plot_y) > 2:
            y_arr = np.asarray(self._plot_y, dtype=float)
            mean_y = float(np.mean(y_arr))
            y_vis = mean_y + gain * (y_arr - mean_y)
            self.visual_plot_data.setData(x=self._plot_t, y=y_vis)
        else:
            self.visual_plot_data.clear()
        self._plot_since_refresh = 0

    def convert_cv_qt(self, cv_img):
        if len(cv_img.shape) == 2:
            rgb_image = cv2.cvtColor(cv_img, cv2.COLOR_GRAY2RGB)
        else:
            rgb_image = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
        rgb_image = np.ascontiguousarray(rgb_image)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        qimg = QtGui.QImage(
            rgb_image.data, w, h, bytes_per_line, QtGui.QImage.Format_RGB888
        ).copy()
        label_size = self.video_label.size()
        target_w = max(1, label_size.width())
        target_h = max(1, label_size.height())
        # Fallback if label not laid out yet
        if target_w < 8 or target_h < 8:
            target_w, target_h = self.disply_width, self.display_height
        scaled = qimg.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return QPixmap.fromImage(scaled)

    def closeEvent(self, event):
        if self.thread:
            self._disconnect_thread_signals()
            self.thread.stop()
            event.accept()

    def update_button_states(self, running):
        self.run_button.setEnabled(not running and self.video_path is not None)
        self.select_file_button.setEnabled(not running)
        self.webcam_button.setEnabled(not running)
        self.display_options.setEnabled(not running)
        self.mask_options.setEnabled(not running)
        self.axis_options.setEnabled(not running)
        self.url_button.setEnabled(not running)
        self.draw_param_button.setEnabled(not running)
        self.roi_button.setEnabled(not running and self.video_path is not None)
        self.clear_roi_button.setEnabled(not running)
        self.frame_width_spin.setEnabled(not running)
        self.frame_height_spin.setEnabled(not running)
        self.analyze_widget.setEnabled(not running)

        self.stop_button.setEnabled(running)
        self.hsv_slider.setEnabled(running)

    def _disconnect_thread_signals(self):
        if self._hsv_tip_slot is not None:
            try:
                self.hsv_slider.tip_config_signal.disconnect(self._hsv_tip_slot)
            except TypeError:
                pass
            self._hsv_tip_slot = None
        if self.thread is None:
            return
        for signal, slot in (
            (self.thread.finished_signal, self.video_thread_finished),
            (self.thread.change_pixmap_signal, self.update_image),
            (self.thread.new_contour_signal, self.update_graph),
            (self.thread.parameter_signal, self.on_thread_params),
            (self.thread.processing_signal, self.processing_frame),
        ):
            try:
                signal.disconnect(slot)
            except TypeError:
                pass

    def start_video_thread(self):
        if (
            self.thread is None
            or not self.thread.isRunning()
            and self.video_path is not None
        ):
            fw = int(self.frame_width_spin.value())
            fh = int(self.frame_height_spin.value())
            if fw < 8 or fh < 8:
                QMessageBox.warning(
                    self,
                    "Frame size",
                    "Set Frame W and Frame H (full video resolution in pixels) "
                    "before Run. Select a video to auto-fill, then edit if needed.",
                )
                return

            self._disconnect_thread_signals()

            self.thread = VideoThread(
                video_path=self.video_path,
                display_option=self.selected_display_option,
                mask_option=self.selected_mask_option,
                motion_axis=self.selected_motion_axis,
                draw_params=self.draw_params,
                roi=self.roi,
                visual_gain=self.analyze_widget.visual_gain(),
                time_scale=self.analyze_widget.time_scale(),
                frame_width=fw,
                frame_height=fh,
            )
            self.data_points.clear()
            self._plot_t.clear()
            self._plot_y.clear()
            self._plot_since_refresh = 0
            self._y_zeroed = False
            self._tip_length_px_med = None
            self._estimate_done = False
            self.position_plot_data.clear()
            self.visual_plot_data.clear()
            self.fitted_plot_data.clear()
            self.upper_decay_plot.clear()
            self.lower_decay_plot.clear()
            self.peak_top_plot.clear()
            self.peak_bottom_plot.clear()
            self.peak_plateau_plot.clear()
            self._set_position_axis_label(zeroed=False)
            self.analyze_widget.tip_length_px_label.setText("-")
            self.analyze_widget.tip_length_detect_px_label.setText("-")
            self._refresh_export_graph_button()
            self._refresh_peak_to_peak_button()

            self.thread.finished_signal.connect(self.video_thread_finished)
            self.thread.change_pixmap_signal.connect(self.update_image)
            self.thread.new_contour_signal.connect(self.update_graph)
            self.thread.parameter_signal.connect(self.on_thread_params)
            self.thread.processing_signal.connect(self.processing_frame)
            self._hsv_tip_slot = self.thread.update_tip_config
            self.hsv_slider.tip_config_signal.connect(self._hsv_tip_slot)
            self.thread.update_tip_config(self.hsv_slider.current_config())

            self.thread.start()
            self.update_button_states(running=True)

    def stop_video_thread(self):
        if self.thread and self.thread.isRunning():
            self.thread.stop()
            tip_l = self.thread.params.get("tip_length_px_med")
            if tip_l is not None:
                self._tip_length_px_med = float(tip_l)
                self.analyze_widget.show_params(dict(self.thread.params))
            self._refresh_position_plot()
        self.update_button_states(running=False)

    def display_selection_changed(self, selected_option):
        self.selected_display_option = selected_option

    def mask_selection_changed(self, selected_option):
        self.selected_mask_option = selected_option

    def axis_selection_changed(self, selected_option):
        self.selected_motion_axis = "y" if "Vertical" in selected_option else "x"
        self._set_position_axis_label(zeroed=self._y_zeroed)

    def _set_position_axis_label(self, zeroed=False):
        if zeroed:
            axis = "y" if self.selected_motion_axis == "y" else "x"
            self.plot.setLabel("left", f"Displacement {axis} (px, mean=0)")
        elif self.selected_motion_axis == "y":
            self.plot.setLabel("left", "Position y (pixels, up+)")
        else:
            self.plot.setLabel("left", "Position x (pixels)")

    @pyqtSlot()
    def zero_position_to_mean(self):
        """Subtract mean position so the series / plot is centered at 0."""
        if self.thread is not None and self.thread.isRunning():
            QMessageBox.information(
                self, "Zero Y", "Stop tracking before zeroing the Y axis."
            )
            return
        if len(self.data_points) < 2:
            QMessageBox.warning(self, "Zero Y", "Need tracked points first.")
            return
        if self._y_zeroed:
            QMessageBox.information(
                self, "Zero Y", "Series is already zeroed to the mean."
            )
            return

        y = np.asarray([p[1] for p in self.data_points], dtype=float)
        mean_y = float(np.mean(y))
        self.data_points = [(t, pos - mean_y) for t, pos in self.data_points]
        self._plot_t = [t for t, _ in self.data_points]
        self._plot_y = [pos for _, pos in self.data_points]
        self._y_zeroed = True

        self.fitted_plot_data.clear()
        self.upper_decay_plot.clear()
        self.lower_decay_plot.clear()
        self.peak_top_plot.clear()
        self.peak_bottom_plot.clear()
        self.peak_plateau_plot.clear()
        self._set_position_axis_label(zeroed=True)
        self._refresh_position_plot()
        self.analyze_widget.equilibrium_value_label.setText("0.00 px (zeroed)")
        self._estimate_done = False
        self._refresh_export_graph_button()
        self._refresh_peak_to_peak_button()
        QMessageBox.information(
            self,
            "Zero Y",
            f"Subtracted mean {mean_y:.2f} px.\n"
            "Plot and series are now centered at 0. Time / wavelength unchanged.\n"
            "Re-run Estimate if you already fitted.",
        )

    @pyqtSlot(dict)
    def on_thread_params(self, params):
        tip_l = params.get("tip_length_px_med")
        self._tip_length_px_med = float(tip_l) if tip_l is not None else None
        self.analyze_widget.show_params(params)

    def video_thread_finished(self):
        if self.thread is not None:
            tip_l = self.thread.params.get("tip_length_px_med")
            self._tip_length_px_med = float(tip_l) if tip_l is not None else None
            self.analyze_widget.show_params(dict(self.thread.params))
        self._refresh_position_plot()
        self._disconnect_thread_signals()
        self.update_button_states(False)
        self._refresh_peak_to_peak_button()

    def fit_data_point(self):
        if len(self.data_points) < 8:
            QMessageBox.warning(self, "Estimate", "Need more tracked points first.")
            return

        data = np.array(self.data_points)
        t_data = data[:, 0]
        y_data = data[:, 1]
        fit_model = self.analyze_widget.fit_model()

        amp0 = 0.5 * (np.max(y_data) - np.min(y_data)) or 1.0
        c0 = float(np.mean(y_data))
        w0 = 2.0 * np.pi * max(dominant_fft_frequency(t_data, y_data), 0.5)

        try:
            if fit_model == "simple":
                p0 = [amp0, w0, 0.0, c0]
                raw_params, _ = curve_fit(
                    simple_harmonic_oscillator,
                    t_data,
                    y_data,
                    p0=p0,
                    bounds=(simple_lower_bounds, simple_upper_bounds),
                    maxfev=5000,
                    ftol=1e-6,
                )
                A, w, phi, C = raw_params
                gamma = 0.0
                params = [A, gamma, w, phi, C]
                self.fitted_plot_data.setData(
                    x=t_data,
                    y=simple_harmonic_oscillator(t_data, A, w, phi, C),
                )
                # Constant amplitude bounds (no decay)
                self.upper_decay_plot.setData(
                    x=t_data, y=np.full_like(t_data, C + abs(A), dtype=float)
                )
                self.lower_decay_plot.setData(
                    x=t_data, y=np.full_like(t_data, C - abs(A), dtype=float)
                )
            else:
                p0 = [amp0, 0.05, w0, 0.0, c0]
                raw_params, _ = curve_fit(
                    underdamped_harmonic_oscillator,
                    t_data,
                    y_data,
                    p0=p0,
                    bounds=(lower_bounds, upper_bounds),
                    maxfev=5000,
                    ftol=1e-6,
                )
                A, gamma, w, phi, C = raw_params
                params = [A, gamma, w, phi, C]
                self.fitted_plot_data.setData(
                    x=t_data,
                    y=underdamped_harmonic_oscillator(t_data, A, gamma, w, phi, C),
                )
                self.upper_decay_plot.setData(
                    x=t_data, y=upper_decaying_component_curve(t_data, A, gamma, C)
                )
                self.lower_decay_plot.setData(
                    x=t_data, y=lower_decaying_component_curve(t_data, A, gamma, C)
                )
        except Exception as exc:
            QMessageBox.warning(self, "Estimate", f"Curve fit failed: {exc}")
            self._estimate_done = False
            self._refresh_export_graph_button()
            return

        analytics = {
            "p2p_px": peak_to_peak(y_data),
            "fft_hz": dominant_fft_frequency(t_data, y_data),
            "tip_length_px": self._tip_length_px_med,
            "tip_length_detect_px": self.analyze_widget.visual_params.get(
                "tip_length_detect_px"
            ),
        }
        self.analyze_widget.update_params(
            params, analytics=analytics, fit_model=fit_model
        )

        axis = self.selected_motion_axis
        csv_name = f"estimate_{axis}.csv"
        path = VideoThread.save_timeseries_csv(
            csv_name,
            self.data_points,
            mm_per_pixel=self.analyze_widget.mm_per_pixel(),
            motion_axis=axis,
        )
        self._estimate_done = True
        self._refresh_export_graph_button()
        model_label = "simple" if fit_model == "simple" else "damped"
        tip_msg = (
            f"\nTip L (px, median mask bbox): {self._tip_length_px_med:.2f}"
            if self._tip_length_px_med is not None
            else "\nTip L (px): unavailable (no mask contour samples)"
        )
        QMessageBox.information(
            self,
            "Estimate",
            f"Fit complete ({model_label}).\nSaved time + {axis} series:\n{path}"
            f"{tip_msg}\n"
            "mm/pixel ≈ tip_mm / Tip L (px) after tuning Mask/HSV.",
        )

    @pyqtSlot()
    def export_csv(self):
        if not self.data_points:
            QMessageBox.warning(self, "Export CSV", "No tracked data to export.")
            return
        axis = self.selected_motion_axis
        path = VideoThread.save_timeseries_csv(
            f"tracked_tip_{axis}.csv",
            self.data_points,
            mm_per_pixel=self.analyze_widget.mm_per_pixel(),
            motion_axis=axis,
        )
        QMessageBox.information(self, "Export CSV", f"Saved:\n{path}")

    @pyqtSlot()
    def export_graph(self):
        mpp = self.analyze_widget.mm_per_pixel()
        if not self._estimate_done or mpp <= 0 or not self.data_points:
            QMessageBox.warning(
                self,
                "Export Graph",
                "Set mm/pixel > 0 and run Estimate successfully before exporting.",
            )
            return
        try:
            t = [p[0] for p in self.data_points]
            y_px = [p[1] for p in self.data_points]
            times_s, displacement_mm = series_to_mm(t, y_px, mpp)
            axis = self.selected_motion_axis
            path = default_plot_path(project_root, axis)
            export_displacement_vs_time(path, times_s, displacement_mm)
        except Exception as exc:
            QMessageBox.warning(self, "Export Graph", f"Failed to export graph:\n{exc}")
            return
        QMessageBox.information(self, "Export Graph", f"Saved:\n{path}")

    @pyqtSlot()
    def show_peak_to_peak(self):
        if len(self.data_points) < 8:
            QMessageBox.warning(
                self, "Peak to Peak", "Need at least 8 tracked points first."
            )
            return

        t = [p[0] for p in self.data_points]
        y_px = [p[1] for p in self.data_points]
        axis = self.selected_motion_axis
        try:
            rows = find_peak_to_peak_extrema(t, y_px)
            if not rows:
                QMessageBox.warning(
                    self, "Peak to Peak", "No extrema found in the tracked series."
                )
                return

            csv_dir = os.path.join(project_root, "data", "csv")
            csv_path = os.path.join(csv_dir, f"peak_to_peak_{axis}.csv")
            write_peak_to_peak_csv(csv_path, rows)

            tops_t, tops_y = [], []
            bottoms_t, bottoms_y = [], []
            plateaus_t, plateaus_y = [], []
            for row in rows:
                if row["label"] == "top":
                    tops_t.append(row["time_s"])
                    tops_y.append(row["position_px"])
                elif row["label"] == "bottom":
                    bottoms_t.append(row["time_s"])
                    bottoms_y.append(row["position_px"])
                else:
                    plateaus_t.append(row["time_s"])
                    plateaus_y.append(row["position_px"])

            self.peak_top_plot.setData(x=tops_t, y=tops_y)
            self.peak_bottom_plot.setData(x=bottoms_t, y=bottoms_y)
            self.peak_plateau_plot.setData(x=plateaus_t, y=plateaus_y)

            png_path = default_peak_to_peak_plot_path(project_root, axis)
            export_peak_to_peak_plot(png_path, t, y_px, rows, axis=axis)
        except Exception as exc:
            QMessageBox.warning(
                self, "Peak to Peak", f"Failed to compute peak-to-peak:\n{exc}"
            )
            return

        QMessageBox.information(
            self,
            "Peak to Peak",
            f"Found {len(rows)} extrema.\nSaved CSV:\n{csv_path}\nSaved PNG:\n{png_path}",
        )
