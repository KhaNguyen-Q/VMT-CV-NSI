import cv2
import cvzone
import numpy as np


# OpenCV HSV: H in [0, 179]. Red wraps around 0; blue is mid-range.
DEFAULT_TIP_HSV = {
    "mode": "both",  # both | red | blue
    "red": {
        "hmin": 0,
        "hmax": 10,
        "hmin2": 170,
        "hmax2": 179,
        "smin": 50,
        "smax": 255,
        "vmin": 50,
        "vmax": 255,
    },
    "blue": {
        "hmin": 95,
        "hmax": 130,
        "smin": 50,
        "smax": 255,
        "vmin": 50,
        "vmax": 255,
    },
    "min_area": 30,
    # Whole tip blobs are often >> 20k px² at detect resolution
    "max_area": 200000,
}


def normalize_tip_hsv(hsv_vals: dict | None) -> dict:
    """Accept legacy single-range JSON or tip red/blue config."""
    cfg = {
        "mode": DEFAULT_TIP_HSV["mode"],
        "red": dict(DEFAULT_TIP_HSV["red"]),
        "blue": dict(DEFAULT_TIP_HSV["blue"]),
        "min_area": DEFAULT_TIP_HSV["min_area"],
        "max_area": DEFAULT_TIP_HSV["max_area"],
    }
    if not hsv_vals:
        return cfg

    if "red" in hsv_vals or "blue" in hsv_vals or "mode" in hsv_vals:
        cfg["mode"] = hsv_vals.get("mode", cfg["mode"])
        if "red" in hsv_vals:
            cfg["red"].update(hsv_vals["red"])
        if "blue" in hsv_vals:
            cfg["blue"].update(hsv_vals["blue"])
        cfg["min_area"] = int(hsv_vals.get("min_area", cfg["min_area"]))
        cfg["max_area"] = int(hsv_vals.get("max_area", cfg["max_area"]))
        return cfg

    for key in ("smin", "smax", "vmin", "vmax"):
        if key in hsv_vals:
            cfg["red"][key] = hsv_vals[key]
            cfg["blue"][key] = hsv_vals[key]
    return cfg


def subpixel_centroid_from_mask(mask) -> tuple[float, float] | None:
    """Subpixel center via image moments."""
    m = cv2.moments(mask, binaryImage=True)
    if m["m00"] <= 1e-6:
        return None
    return float(m["m10"] / m["m00"]), float(m["m01"] / m["m00"])


class ImageProcessor:
    def __init__(self, hsv_vals: dict, gaussian_kernel: tuple = (5, 5)):
        self.hsv_vals = normalize_tip_hsv(hsv_vals)
        self.gaussian_kernel = gaussian_kernel
        self.last_center = None  # float (cx, cy) in current detect frame
        self._lk_prev_gray = None
        self._lk_points = None
        self._lk_misses = 0

    def reset_track(self):
        self.last_center = None
        self._lk_prev_gray = None
        self._lk_points = None
        self._lk_misses = 0

    def _range_mask(self, hsv, hmin, hmax, smin, smax, vmin, vmax):
        lower = np.array([hmin, smin, vmin], dtype=np.uint8)
        upper = np.array([hmax, smax, vmax], dtype=np.uint8)
        return cv2.inRange(hsv, lower, upper)

    def get_color_mask(self, frame):
        """Mask for a translucent tip that is red and/or blue."""
        blurred = cv2.GaussianBlur(frame, self.gaussian_kernel, 0)
        hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
        mode = self.hsv_vals.get("mode", "both")
        mask = np.zeros(hsv.shape[:2], dtype=np.uint8)

        if mode in ("both", "red"):
            red = self.hsv_vals["red"]
            mask_red = self._range_mask(
                hsv,
                red["hmin"],
                red["hmax"],
                red["smin"],
                red["smax"],
                red["vmin"],
                red["vmax"],
            )
            mask_red2 = self._range_mask(
                hsv,
                red["hmin2"],
                red["hmax2"],
                red["smin"],
                red["smax"],
                red["vmin"],
                red["vmax"],
            )
            mask = cv2.bitwise_or(mask, cv2.bitwise_or(mask_red, mask_red2))

        if mode in ("both", "blue"):
            blue = self.hsv_vals["blue"]
            mask_blue = self._range_mask(
                hsv,
                blue["hmin"],
                blue["hmax"],
                blue["smin"],
                blue["smax"],
                blue["vmin"],
                blue["vmax"],
            )
            mask = cv2.bitwise_or(mask, mask_blue)

        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        return mask

    def get_contours(self, frame, mask, min_area: int = None, max_area: int = None):
        """
        Return the single best tip with subpixel centroid.
        Falls back to Lucas-Kanade if the color mask briefly fails.
        """
        if min_area is None:
            min_area = int(self.hsv_vals.get("min_area", 30))
        if max_area is None:
            max_area = int(self.hsv_vals.get("max_area", 200000))

        frame_with_contours = frame.copy()
        found = cvzone.findContours(frame, mask, minArea=min_area)[1] or []
        candidates = [c for c in found if min_area <= c["area"] <= max_area]
        best = self._pick_tip_contour(candidates)

        tip = None
        if best is not None:
            tip = self._subpixel_from_contour(mask, best)
            self._lk_misses = 0
        else:
            tip = self._lk_fallback(frame)
            if tip is not None:
                self._lk_misses += 1
                if self._lk_misses > 15:
                    tip = None
                    self.reset_track()

        contours = []
        if tip is not None:
            cx, cy, area, bbox = tip
            self.last_center = (cx, cy)
            self._update_lk_state(frame, cx, cy)
            contours = [
                {
                    "center": (cx, cy),
                    "area": area,
                    "bbox": bbox,
                }
            ]
            cv2.circle(frame_with_contours, (int(round(cx)), int(round(cy))), 8, (0, 255, 255), 2)
            cv2.putText(
                frame_with_contours,
                f"tip ({cx:.1f}, {cy:.1f}) a={int(area)}",
                (int(cx) + 12, int(cy)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 0, 0),
                2,
                cv2.LINE_4,
            )
        else:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            self._lk_prev_gray = gray

        return {
            "mask": mask,
            "image_contours": frame_with_contours,
            "contours": contours,
        }

    def _subpixel_from_contour(self, mask, contour_dict):
        """Refine integer contour center with moments on the contour region."""
        x, y, w, h = contour_dict["bbox"]
        pad = 2
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(mask.shape[1], x + w + pad)
        y1 = min(mask.shape[0], y + h + pad)
        roi = mask[y0:y1, x0:x1]
        centroid = subpixel_centroid_from_mask(roi)
        if centroid is None:
            cx, cy = contour_dict["center"]
            return float(cx), float(cy), float(contour_dict["area"]), contour_dict["bbox"]
        cx = centroid[0] + x0
        cy = centroid[1] + y0
        return cx, cy, float(contour_dict["area"]), contour_dict["bbox"]

    def _update_lk_state(self, frame, cx, cy):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self._lk_prev_gray = gray
        self._lk_points = np.array([[[cx, cy]]], dtype=np.float32)

    def _lk_fallback(self, frame):
        """Track last tip with optical flow when mask is empty briefly."""
        if self._lk_prev_gray is None or self._lk_points is None:
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        next_pts, status, _ = cv2.calcOpticalFlowPyrLK(
            self._lk_prev_gray,
            gray,
            self._lk_points,
            None,
            winSize=(21, 21),
            maxLevel=2,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
        )
        self._lk_prev_gray = gray
        if next_pts is None or status is None or status[0][0] == 0:
            return None
        cx, cy = float(next_pts[0][0][0]), float(next_pts[0][0][1])
        self._lk_points = next_pts
        # Synthetic bbox around LK point
        r = 8
        bbox = (int(cx - r), int(cy - r), 2 * r, 2 * r)
        return cx, cy, float(np.pi * r * r), bbox

    def _pick_tip_contour(self, candidates):
        """
        Prefer the largest in-range blob (whole tip), not the smallest fragment.
        If we already have a track, pick the largest blob near the last center.
        """
        if not candidates:
            return None

        def largest(cands):
            return max(cands, key=lambda c: c["area"])

        if self.last_center is not None:
            lx, ly = self.last_center
            nearby = []
            for c in candidates:
                cx, cy = c["center"]
                dist2 = (cx - lx) ** 2 + (cy - ly) ** 2
                if dist2 <= (120**2):
                    nearby.append(c)
            if nearby:
                return largest(nearby)

        return largest(candidates)

    def get_edges(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, self.gaussian_kernel, 0)
        edges = cv2.Canny(blurred, 50, 150)
        return edges

    def _get_circles_mask(self, frame, best_circle: bool = False):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, self.gaussian_kernel, 0)
        circles = cv2.HoughCircles(
            blurred,
            cv2.HOUGH_GRADIENT,
            dp=1,
            minDist=20,
            param1=50,
            param2=30,
            minRadius=5,
            maxRadius=50,
        )

        circle_mask = np.zeros_like(gray)
        max_acc = 0
        best_circle_coords = None

        if circles is not None:
            circles = np.round(circles[0, :]).astype("int")
            for x, y, r in circles:
                if best_circle:
                    acc = r
                    if acc > max_acc:
                        max_acc = acc
                        best_circle_coords = (x, y, r)
                else:
                    cv2.circle(circle_mask, (x, y), r, 255, -1)

        if best_circle:
            if best_circle_coords is not None:
                x, y, r = best_circle_coords
                cv2.circle(circle_mask, (x, y), r, 255, -1)

        return circle_mask

    def get_circles(self, frame):
        return self._get_circles_mask(frame)

    def get_best_circle(self, frame):
        return self._get_circles_mask(frame, best_circle=True)
