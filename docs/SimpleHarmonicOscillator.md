# Simple Harmonic Oscillator — Reference

This document is the in-repo guide for the **current** Harmonic-Oscillator-CV app after the spring/slider refactor and small-amplitude tooling discussed in development.

## Purpose

Track **linear (1D) harmonic motion** from video and extract analytics:

- **Slider / cart:** left–right → axis `x`
- **Spring / mass:** up–down → axis `y`
- Typical tip: translucent **red and/or blue** (dual HSV)
- Useful when tip travel is **&lt; 1 cm**, if ROI + contrast + enough pixels of motion exist

It does **not** analyze pendulum swings (circular arcs, pivot, string length).

## What was removed (pendulum era)

| Removed | Reason |
|---------|--------|
| Circle fit of trajectory | Pendulum path is an arc; spring/slider path is a line |
| Arc / rotated coordinates | Not needed for linear position |
| Pivot / string overlays | Pendulum geometry |
| \(L = g / \omega_0^2\) | Pendulum small-angle formula |
| Blocking ~2000-frame pre-scan | Made “Loading %” extremely slow; equilibrium is now live |

## Idea → implementation

**Idea:** Measure tip displacement vs time with classical CV (not a neural net), then fit an underdamped oscillator and/or FFT for frequency and amplitude.

**Applied as:**

1. Decode video on a background `QThread`
2. Detect tip (color / edge / circle)
3. Subpixel centroid → 1D position series
4. SciPy curve fit + NumPy FFT / peak-to-peak
5. Optional mm scale via user `mm/pixel`

```text
Video / webcam / URL
  → optional ROI (native resolution; no downscale inside ROI)
  → tip color (red/blue HSV) | edge | Hough circle
  → subpixel centroid (moments); Lucas–Kanade if mask drops briefly
  → 1D position (x or flipped y)
  → (time_s, position_px) + live equilibrium
  → plot + Estimate (SHO fit, P2P, FFT) + Export CSV / Export Graph
```

Playback starts immediately (no warm-up pass). Without an ROI, wide frames may downscale to max width **640** for speed; **with an ROI, detection stays full crop resolution** (critical for sub-cm tips).

## Libraries

| Library | Role |
|---------|------|
| **OpenCV (`cv2`)** | Video I/O, HSV/`inRange`, morphology, contours, Canny, Hough, Lucas–Kanade, ROI window, drawing |
| **cvzone** | Contour helpers on masks |
| **NumPy** | Arrays, moments support, FFT |
| **SciPy** | `curve_fit` for the SHO model |
| **PyQt5** | GUI, signals/slots, worker thread |
| **pyqtgraph** | Live position / fit / decay plots |
| **matplotlib** | Offline `test/damper_eqn_pred.py` only |

This is **not** a black box: every tracking step is visible in `detector.py` / `video_thread.py`.

## How displacement is tracked

### Primary path (Tip Color)

1. Blur → HSV  
2. Dual ranges: **red** (wraps near hue 0/179) and/or **blue** (~95–130)  
3. Morphology open/close  
4. Contours filtered by `min_area` / `max_area`  
5. Prefer blob near last tip center (temporal lock)  
6. **Subpixel center** via image moments: \(c_x = m_{10}/m_{00}\), \(c_y = m_{01}/m_{00}\)

### Lucas–Kanade

`cv2.calcOpticalFlowPyrLK` is used as a **short fallback** when the color mask fails (~15 frames), then reset. It is **not** the primary measurement path today. Color + moments is usually better for a colored translucent tip; LK-as-primary would need a textured marker.

### Axis / position definition

| UI | Stored | Position |
|----|--------|----------|
| Horizontal (x) | `x` | \(c_x\) (pixels from left) |
| Vertical (y) | `y` | \(H - c_y\) (up positive) |

### Time definition (important)

\[
t_{\mathrm{sec}} = \texttt{time\_scale} \times \frac{\text{frame index}}{\texttt{CAP\_PROP\_FPS}}
\]

(with a fallback FPS of 30 if metadata is missing). **Time scale** defaults to `1.0` (file metadata time). For remapped / slo-mo exports whose file duration differs from the original playback length, set:

\[
\texttt{time\_scale} = \frac{\text{original duration}}{\text{file duration}}
\]

Example: original playback \(72.5\,\mathrm{s}\), converted file \(39.5\,\mathrm{s}\) → `time_scale ≈ 1.8354`. That stretches the plot, CSV, and fits onto the original timeline.

**Caveat:** scaled time changes fitted/FFT frequency by the same factor. Use `1.0` when file FPS already matches real acquisition time.

**CFR assumption only.** Variable frame rate (common on phone “4K 60fps” when the device throttles) is **not** handled. `CAP_PROP_POS_MSEC` is *not* used yet; even that is backend-dependent and not a guarantee for all `.MOV`s. For trustworthy Hz from smartphone clips:

- Prefer **re-encode to constant frame rate (CFR)**, e.g.  
  `ffmpeg -i input.MOV -vsync cfr -r 60 -c:v libx264 -crf 18 -an out_cfr.mp4`  
- Or add PTS / robust timestamping later

“4K 60fps” on a phone is a **target mode**, not a promise that every frame is exactly \(1/60\) s apart (heat, low light, HDR, etc.).

## Display modes (view only — tracking unchanged)

| Mode | Shows | Use for |
|------|--------|---------|
| **Main Video** | Raw frames + overlays | Framing / overlays on the real tip |
| **Image Contours** | Tip marker + `tip (x,y) a=…` | Confirm which blob is locked |
| **Mask** | Binary detection mask | Tune HSV (`smin`, area, Red/Blue) |

Workflow: tune on **Mask** → verify on **Image Contours** / **Main Video**.

## Plot series: Actual, Visual gain, Fitted, Decay

| Series | Meaning | Measurement? |
|--------|---------|----------------|
| **Actual Position** | Tracked \(x(t)\) in pixels | Yes |
| **Visual gain** | \(x_{\mathrm{vis}} = \bar x + G(x-\bar x)\) (dashed plot + orange overlay) | **No** — display only |
| **Fitted curve** | SHO model \(A e^{-\gamma t}\cos(\omega t+\phi)+C\) after **Estimate** | Yes (parameters) |
| **Decay curve** | Envelopes \(C \pm A e^{-\gamma t}\) | Derived from fit |

Fitted model:

\[
x(t) = A\,e^{-\gamma t}\cos(\omega t + \phi) + C
\]

Derived after Estimate:

| Quantity | Formula |
|----------|---------|
| Period \(T\) | \(2\pi / \omega\) |
| Fit frequency \(f\) | \(\omega / 2\pi\) (Hz) |
| \(\omega_0\) | \(\sqrt{\omega^2 + \gamma^2}\) |
| \(k/m\) | \(\omega_0^2\) (mass–spring assumption) |
| **P2P (px)** | \(\max(x)-\min(x)\) over tracked samples |
| **FFT f** | Dominant FFT peak (Hz), cross-check vs fit \(f\) |

Example: P2P **32** means **32 pixels** peak-to-peak — usable for CV (far better than ~2–3 px). Physical mm requires calibration (below).

## Pixels → millimeters

The app does **not** infer mm from “4K” or camera distance.

\[
\mathrm{mm} = \mathrm{pixels} \times (\mathrm{mm/pixel})
\]

**Option A — ruler**

1. Place a ruler in the **tip plane** (same depth as the tip).  
2. Measure a known length in mm and in pixels.  
3. \(\mathrm{mm/pixel} = \mathrm{known\ mm} / \mathrm{pixel\ span}\).  
4. Enter that in the **mm/pixel** spin box → P2P (mm), A (mm), CSV `position_mm`.

**Option B — known tip length + Mask Tip L (px)**

1. Tune **HSV** in Mask view until the white blob matches the tip end-to-end.  
2. Watch **Tip L (px)** update live (overlay `L=… px (full)`; detect `WxH` is display-only).  
3. \(\mathrm{mm/pixel} = \mathrm{tip\ length\ (mm)} / \mathrm{Tip\ L\ (px)}\).  
4. Enter **mm/pixel**, then Estimate / Export.

**Tip L coordinate system**

- **Tip L** and tracked **displacement** are both in **full-frame pixels** (same space), so `mm/pixel = tip_mm / Tip_L` matches `x_px` / `y_px`.  
- Detect runs at Mask resolution (≤ 640 wide without ROI, or native ROI); values are scaled by Frame W/H (or actual size) to full-frame.  
- Overlay secondary line `WxH detect  L=…` is **display only** — explains Mask blob size; do not use it for mm calibration.  
- **Empty mask clears Tip L**. Re-tune HSV until Tip L (full) is live before calibrating.  
- Tight **ROI** keeps native detect resolution (best spatial detail).

If mm/pixel is **0**, mm fields stay blank; analysis stays in pixels.

**Rough FOV estimate only** (not for reporting): at ~1 inch distance, 4K landscape (~3840 px wide), mm/pixel often lands around ~0.005–0.015 depending on lens FOV — always prefer a ruler or known tip length.

## Tip color controls

- Mode: **Both (red or blue)** / **Red only** / **Blue only**  
- Shared **smin/smax/vmin/vmax** (translucent tips often need lower `smin`)  
- **min_area / max_area** — raise min_area to reject noise; lower max_area to avoid huge background floods  
- **Save Tip HSV** → `data/json/hsv.json`  
- One HSV band cannot cover red and blue unless dual ranges (already implemented)

## ROI

- **Set ROI** opens OpenCV `selectROI` on the **first full-resolution frame**.  
- On 4K phone video the window can look “zoomed” so the tip is hard to frame — known UX limitation (preview is not downscaled).  
- Workarounds: use a pre-cropped clip (e.g. `Crop.mp4`), or **Clear ROI** and run full-frame (worse for tiny motion).  
- Desired fix (not necessarily shipped): scale preview for picking, map rectangle back to full-res coordinates.

## Runtime memory and run-to-run differences

### Cleared on each Run

- `data_points` and plot curves  
- New `VideoThread` / detector (tip lock / LK state reset)

### Persists in the session (until changed / app restart)

| State | Notes |
|-------|--------|
| ROI | Until **Clear ROI** |
| HSV sliders | In memory; disk if you Saved |
| Axis, mask mode, visual gain, mm/pixel | UI knobs |
| Last **Estimate** labels | Stale until you Estimate again |
| Equilibrium readout | Updated live; not a full UI wipe |

### Why the “same” video can look drastically different

1. Stopped early vs full clip → different series length  
2. First locked blob can differ if several red/blue regions exist  
3. HSV tweaked during a previous run still applies  
4. ROI on vs off / different box  
5. Lost lock / LK gaps → different \(x(t)\)  
6. `curve_fit` is sensitive to noisy or partial data  

**For repeatable runs:** fix ROI, don’t touch HSV mid-run, let the clip finish (or always stop at the same time), verify Mask shows one tip, then Estimate. Restart the app for a clean slate.

Also: repeated Run can accumulate HSV signal connections to old threads (messy); restart if behavior feels haunted.

## Overlays

- Cyan rectangle = ROI  
- Red/cyan = tracked tip  
- Green = equilibrium line  
- Orange = visual-gain tip offset (**not** the measurement)

## Export / saved files

| Path | Contents |
|------|----------|
| `data/json/hsv.json` | Tip HSV + area limits |
| `data/json/parameters.json` | Last saved fit (+ mm/pixel if saved) |
| `data/csv/estimate_x.csv` or `estimate_y.csv` | Written on **Estimate**: `time_s`, `x_px`/`y_px`, `x_mm`/`y_mm` |
| `data/csv/tracked_tip_x.csv` or `tracked_tip_y.csv` | Manual **Export CSV** (same columns) |
| `data/plots/displacement_vs_time_x.png` (or `_y`) | **Export Graph**: Displacement (mm) vs Time (s) |

**Export Graph** (matplotlib) is enabled only after **mm/pixel > 0** and a successful **Estimate**. It plots the tracked series in mm with padded auto-limits so peaks are visible. Plot styling lives in `app/utils/plot_export.py`.

## Recommended workflow (small tip)

1. Prefer short, cool, well-lit clips; consider **CFR re-encode** for phone 4K60.  
2. High-contrast marker on translucent tip if Mask is unstable.  
3. Ruler in tip plane → set **mm/pixel**.  
4. **Set ROI** tightly (or use a pre-cropped video).  
5. **Tip Color** + **Mask** → tune until one clean white blob.  
6. Confirm **Frame W / H** (auto-filled; drives detect→full Tip L / position scale).  
7. **Run** → **Stop** → **Estimate** (also writes `estimate_x.csv` / `estimate_y.csv`) → compare Fit f vs FFT f → optional **Export CSV** / **Export Graph**.
8. Use **Visual gain** only to *see* small motion; ignore it for reported amplitude.

## Hardware (not code)

1. Opaque high-contrast tip marker  
2. Fill the frame with the tip (maximize px/mm)  
3. Fixed camera; scale in tip plane  
4. Optical lever only if P2P stays ~2–3 px after the above  

## Key files

| File | Role |
|------|------|
| `app/app.py` | Entry point |
| `app/processing/detector.py` | Tip mask, subpixel centroid, LK fallback |
| `app/processing/video_thread.py` | ROI, tracking loop, timebase, CSV helper |
| `app/windows/app_window.py` | GUI, ROI, plot, fit, export |
| `app/windows/analyze_widget.py` | Metrics, mm/pixel, visual gain, Export Graph |
| `app/windows/hsv_slider.py` | Tip color / area UI |
| `app/utils/utils.py` | SHO model, FFT, P2P, JSON |
| `app/utils/plot_export.py` | Matplotlib Displacement vs Time PNG export |
| `requirements.txt` | Flexible pins (Python 3.10+, including 3.14) |

## Python / install

```bash
cd Harmonic-Oscillator-CV   # project root (not a parent folder like PRODocc alone)
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
python app/app.py
```

Use the **flexible** `requirements.txt` (`numpy>=2.0`, etc.). Old exact pins (`numpy==1.25.2`, `contourpy==1.1.0`, …) fail on Python 3.14 (no wheels / source build errors).

If you keep a second copy under another folder, sync `app/` and `requirements.txt` from this tree — stale copies lack ROI / tip / analytics updates.

## Possible improvements (backlog)

1. ROI preview scaled to screen, map box back to full-res (fix 4K “zoom” UX)  
2. Prefer `CAP_PROP_POS_MSEC` when monotone/sane, else `frame/fps`; or CFR preprocess docs in UI  
3. Optional **LK / template as primary** tracker mode  
4. Click-two-points calibration for mm/pixel  
5. Disconnect old thread signals each Run; reset Analyze labels on Run  
6. Fit R² / covariance; allow Estimate on paused data with clearer messaging  
7. Process every frame at 60 FPS when ROI is small  
8. Offline CLI analyzer for batch videos  

## Related docs

- [README.md](../README.md) — quick start and usage checklist  
