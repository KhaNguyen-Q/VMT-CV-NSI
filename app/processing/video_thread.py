import os
import cv2
import numpy as np
from processing.detector import ImageProcessor
import csv
from utils.utils import (
    load_json,
    get_project_root,
)
from utils.contansts import GREEN, RED, CYAN
from PyQt5.QtCore import QThread, pyqtSignal, pyqtSlot

project_root = get_project_root(os.path.dirname(os.path.abspath(__file__)))
data_folder = os.path.join(project_root, "data")


class VideoThread(QThread):
    DETECT_MAX_WIDTH = 640
    DISPLAY_STRIDE = 10
    POSITION_STRIDE = 1
    EQUILIBRIUM_STRIDE = 25
    FRAME_OFFSET = 0
    PREVIEW_MAX_WIDTH = 960

    finished_signal = pyqtSignal()
    change_pixmap_signal = pyqtSignal(np.ndarray)
    new_contour_signal = pyqtSignal(float, float)
    parameter_signal = pyqtSignal(dict)
    processing_signal = pyqtSignal(int)

    def __init__(
        self,
        video_path,
        display_option,
        mask_option,
        motion_axis="x",
        draw_params=False,
        roi=None,
        visual_gain=1.0,
        time_scale=1.0,
        frame_width=None,
        frame_height=None,
    ):
        super().__init__()
        self._run_flag = True
        self.cap = cv2.VideoCapture(video_path)
        fps = float(self.cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self.frame_rate = fps if fps > 1e-3 else 30.0
        self.display_option = display_option
        self.mask_option = mask_option
        self.motion_axis = motion_axis if motion_axis in ("x", "y") else "x"
        self.draw_params = draw_params
        # roi: (x, y, w, h) in full-frame coords, or None
        self.roi = self._normalize_roi(roi)
        self.visual_gain = max(1.0, float(visual_gain))
        self.time_scale = max(1e-6, float(time_scale))

        self.hsvVals = load_json(os.path.join(data_folder, "json", "hsv.json"))
        self.data_points = np.empty((0, 3), dtype=float)
        cap_w = float(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0.0)
        cap_h = float(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0.0)
        # User-declared full-frame size (set before Run) drives detect→full scale
        self.width = float(frame_width) if frame_width and float(frame_width) >= 8 else cap_w
        self.height = (
            float(frame_height) if frame_height and float(frame_height) >= 8 else cap_h
        )
        self.params = {
            "axis": self.motion_axis,
            "equilibrium": None,
            "radius_bob": 0,
            "roi": self.roi,
            "fps": self.frame_rate,
            "time_scale": self.time_scale,
            "frame_width": self.width,
            "frame_height": self.height,
            "tip_length_px": None,
            "tip_length_px_med": None,
            "tip_length_detect_px": None,
        }
        self._position_samples = []
        self._tip_length_samples = []

        self.processor = ImageProcessor(self.hsvVals)

    def _normalize_roi(self, roi):
        if roi is None:
            return None
        x, y, w, h = [int(v) for v in roi]
        if w < 8 or h < 8:
            return None
        return (x, y, w, h)

    def _crop_roi(self, frame):
        if self.roi is None:
            return frame, 0, 0
        x, y, w, h = self.roi
        fh, fw = frame.shape[:2]
        x = max(0, min(x, fw - 1))
        y = max(0, min(y, fh - 1))
        w = max(1, min(w, fw - x))
        h = max(1, min(h, fh - y))
        return frame[y : y + h, x : x + w], x, y

    def _axis_position(self, cx, cy):
        """1D position in full-frame pixels (y flipped so up is positive)."""
        if self.motion_axis == "y":
            return float(self.height - cy)
        return float(cx)

    def _clear_tip_length(self, emit=True):
        """Drop Tip L samples (HSV change or lost mask)."""
        self._tip_length_samples = []
        self.params["tip_length_px"] = None
        self.params["tip_length_px_med"] = None
        self.params["tip_length_detect_px"] = None
        if emit:
            self.parameter_signal.emit(dict(self.params))

    def _record_tip_length(self, tip_length_px, tip_length_detect_px=None):
        """Sample major bbox length in full-frame px (same space as x_px / y_px)."""
        self._tip_length_samples.append(float(tip_length_px))
        if len(self._tip_length_samples) > 500:
            self._tip_length_samples = self._tip_length_samples[-500:]
        self.params["tip_length_px"] = float(tip_length_px)
        self.params["tip_length_px_med"] = float(np.median(self._tip_length_samples))
        if tip_length_detect_px is not None:
            self.params["tip_length_detect_px"] = float(tip_length_detect_px)

    def _emit_tip_length_params(self):
        self.parameter_signal.emit(dict(self.params))

    def _update_equilibrium(self, pos, radius_bob=None):
        self._position_samples.append(pos)
        if len(self._position_samples) > 300:
            self._position_samples = self._position_samples[-300:]
        self.params["equilibrium"] = float(np.mean(self._position_samples))
        self.params["axis"] = self.motion_axis
        if radius_bob is not None:
            self.params["radius_bob"] = radius_bob
        self.parameter_signal.emit(dict(self.params))

    def _draw_tip_length_overlay(
        self, img, bbox_local, tip_length_px, tip_length_detect_px=None, color=(0, 255, 255)
    ):
        """Draw tip ROI bbox; Tip L is full-frame; optional detect WxH for Mask UX."""
        x, y, w, h = [int(round(v)) for v in bbox_local]
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
        label = f"tip ROI  L={tip_length_px:.1f} px (full)"
        cv2.putText(
            img,
            label,
            (x, max(16, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            2,
            cv2.LINE_4,
        )
        if tip_length_detect_px is not None:
            cv2.putText(
                img,
                f"{w}x{h} detect  L={tip_length_detect_px:.1f} px",
                (x, max(32, y + h + 16)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                color,
                1,
                cv2.LINE_4,
            )

    def _prepare_detect_frame(self, frame):
        """
        Prepare detection frame.
        With ROI: native ROI resolution (no downscale).
        Without ROI: downscale wide frames for speed.
        scale_* maps detect → full-frame for Tip L and displacement.
        """
        crop, ox, oy = self._crop_roi(frame)
        h, w = crop.shape[:2]
        declared_w = float(self.width) if self.width and self.width >= 8 else float(w)
        declared_h = float(self.height) if self.height and self.height >= 8 else float(h)

        # Never downscale a user ROI — that destroys sub-cm spatial detail
        if self.roi is not None or w <= self.DETECT_MAX_WIDTH:
            if self.roi is None and (abs(declared_w - w) > 0.5 or abs(declared_h - h) > 0.5):
                return crop, declared_w / float(w), declared_h / float(h), ox, oy
            return crop, 1.0, 1.0, ox, oy

        scale = self.DETECT_MAX_WIDTH / float(w)
        new_w = self.DETECT_MAX_WIDTH
        new_h = max(1, int(round(h * scale)))
        detect = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_AREA)
        scale_x = declared_w / float(new_w)
        scale_y = declared_h / float(new_h)
        return detect, scale_x, scale_y, ox, oy

    def _is_tip_color_mode(self):
        return (
            self.mask_option.startswith("Tip Color")
            or self.mask_option == "Color Detection"
        )

    def _detect(self, detect_frame):
        if self._is_tip_color_mode():
            mask = self.processor.get_color_mask(detect_frame)
        elif self.mask_option == "Edge Detection":
            mask = self.processor.get_edges(detect_frame)
        else:
            mask = self.processor.get_best_circle(detect_frame)
        contours_output = self.processor.get_contours(detect_frame, mask)
        return mask, contours_output

    def _downscale_preview(self, img):
        """Shrink preview before Qt to cut UI convert cost."""
        h, w = img.shape[:2]
        if w <= self.PREVIEW_MAX_WIDTH:
            return img
        scale = self.PREVIEW_MAX_WIDTH / float(w)
        new_w = self.PREVIEW_MAX_WIDTH
        new_h = max(1, int(round(h * scale)))
        return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

    def run(self):
        frame_number = 0
        frame_offset = self.FRAME_OFFSET

        self.processor.reset_track()
        if frame_offset > 0:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_offset)

        while self._run_flag:
            ret, frame = self.cap.read()
            if not ret:
                break

            detect_frame, scale_x, scale_y, ox, oy = self._prepare_detect_frame(frame)
            mask, contours_output = self._detect(detect_frame)
            contours = contours_output["contours"]

            tip = None
            tip_length_px = None
            tip_length_detect_px = None
            bbox_local = None
            if contours:
                cx_d, cy_d = contours[0]["center"]
                # Map detect-local → full-frame (Tip L + displacement same space)
                cx = cx_d * scale_x + ox
                cy = cy_d * scale_y + oy
                bbox = contours[0]["bbox"]
                bbox_local = bbox
                w_full = float(bbox[2]) * scale_x
                h_full = float(bbox[3]) * scale_y
                tip_length_detect_px = max(float(bbox[2]), float(bbox[3]))
                tip_length_px = max(w_full, h_full)
                radius_bob = (w_full + h_full) / 4
                pos = self._axis_position(cx, cy)
                tip = (
                    cx,
                    cy,
                    pos,
                    radius_bob,
                    float(contours[0]["area"]) * scale_x * scale_y,
                )
                self._record_tip_length(tip_length_px, tip_length_detect_px)

                if frame_number % self.EQUILIBRIUM_STRIDE == 0:
                    self._update_equilibrium(pos, radius_bob)
                elif frame_number % self.DISPLAY_STRIDE == 0:
                    self._emit_tip_length_params()

                if frame_number % self.POSITION_STRIDE == 0:
                    t_sec = (
                        self.time_scale
                        * (frame_number - frame_offset)
                        / self.frame_rate
                    )
                    self.new_contour_signal.emit(t_sec, pos)
            else:
                # Lost mask: clear stale Tip L so slider wipe shows "-"
                if self._tip_length_samples or self.params.get("tip_length_px_med") is not None:
                    self._clear_tip_length(emit=(frame_number % self.DISPLAY_STRIDE == 0))
                elif frame_number % self.DISPLAY_STRIDE == 0:
                    self._emit_tip_length_params()

            show_display = frame_number % self.DISPLAY_STRIDE == 0
            if show_display:
                if self.display_option == "Mask":
                    mask_vis = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
                    if tip is not None and bbox_local is not None:
                        self._draw_tip_length_overlay(
                            mask_vis,
                            bbox_local,
                            tip_length_px,
                            tip_length_detect_px,
                            color=(0, 255, 255),
                        )
                    self.change_pixmap_signal.emit(self._downscale_preview(mask_vis))
                else:
                    display_frame = frame.copy()
                    if self.roi is not None:
                        x, y, w, h = self.roi
                        cv2.rectangle(display_frame, (x, y), (x + w, y + h), CYAN, 2)

                    if tip is not None:
                        cx, cy, pos, _radius, area = tip
                        if (
                            self.display_option == "Image Contours"
                            and bbox_local is not None
                        ):
                            bx, by, bw, bh = bbox_local
                            full_bbox = (
                                bx * scale_x + ox,
                                by * scale_y + oy,
                                bw * scale_x,
                                bh * scale_y,
                            )
                            self._draw_tip_length_overlay(
                                display_frame,
                                full_bbox,
                                tip_length_px,
                                tip_length_detect_px,
                                color=(0, 255, 255),
                            )
                            cv2.circle(
                                display_frame,
                                (int(round(cx)), int(round(cy))),
                                8,
                                (0, 255, 255),
                                2,
                            )
                            cv2.putText(
                                display_frame,
                                f"tip ({cx:.1f}, {cy:.1f}) a={int(area)}",
                                (int(cx) + 12, int(cy)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.5,
                                (0, 0, 0),
                                2,
                                cv2.LINE_4,
                            )
                        self.draw_param(display_frame, (cx, cy), pos)

                    self.change_pixmap_signal.emit(self._downscale_preview(display_frame))

            frame_number += 1

        self.cap.release()
        # Final params push so Tip L median (or cleared) is available after Stop
        self.parameter_signal.emit(dict(self.params))
        self.finished_signal.emit()

    def draw_param(self, frame, bob_pos, pos):
        if not self.draw_params:
            return
        cx, cy = bob_pos
        cv2.circle(frame, (int(round(cx)), int(round(cy))), 6, RED, -1)
        cv2.circle(frame, (int(round(cx)), int(round(cy))), 12, CYAN, 2)

        equilibrium = self.params.get("equilibrium")
        if equilibrium is None:
            return

        h, w = frame.shape[:2]
        if self.motion_axis == "x":
            x_eq = int(round(equilibrium))
            cv2.line(frame, (x_eq, 0), (x_eq, h - 1), GREEN, 2)
            if self.visual_gain > 1.0:
                x_vis = int(round(equilibrium + self.visual_gain * (pos - equilibrium)))
                cv2.circle(frame, (x_vis, int(round(cy))), 5, (0, 165, 255), -1)
        else:
            y_eq = int(round(self.height - equilibrium))
            cv2.line(frame, (0, y_eq), (w - 1, y_eq), GREEN, 2)
            if self.visual_gain > 1.0:
                y_phys = equilibrium + self.visual_gain * (pos - equilibrium)
                y_vis = int(round(self.height - y_phys))
                cv2.circle(frame, (int(round(cx)), y_vis), 5, (0, 165, 255), -1)

    def stop(self):
        self._run_flag = False
        self.wait()

    @pyqtSlot(dict)
    def update_tip_config(self, tip_cfg):
        self.processor.hsv_vals = tip_cfg
        self.processor.reset_track()
        self._clear_tip_length(emit=True)

    @staticmethod
    def save_timeseries_csv(
        filename, data_points, mm_per_pixel=0.0, motion_axis="x"
    ):
        """
        Save tracked series.
        data_points: iterable of (time_s, position_px) along motion_axis.
        """
        axis = "y" if motion_axis == "y" else "x"
        path = os.path.join(data_folder, "csv", filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="") as csvfile:
            csv_writer = csv.writer(csvfile)
            csv_writer.writerow(
                ["time_s", f"{axis}_px", f"{axis}_mm"]
            )
            for t_sec, pos_px in data_points:
                pos_mm = pos_px * mm_per_pixel if mm_per_pixel else ""
                csv_writer.writerow([t_sec, pos_px, pos_mm])
        return path
