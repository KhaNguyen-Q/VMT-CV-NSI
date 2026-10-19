# Application Architecture and Contributor Guide

This document describes the implementation behind the recording and comparison workflows in the project [README](../README.md). It is for contributors who need to trace the main components, understand the data passed between them, or find the right place to make a change. User-facing steps belong in the README; this guide focuses on how the app works.

## Project layout

```text
app/
  app.py                       Qt application entry point
  windows/
    app_window.py              Main UI; recording, graph, analysis, and exports
    analyze_widget.py          Analysis controls and result labels
    hsv_slider.py              Tip-color controls and HSV configuration
    graph_viewbox.py           Shared graph zoom and pan behavior
    compare_window.py          CSV comparison, ranges, findings, and reports
  processing/
    video_thread.py            Video capture and per-frame tracking pipeline
    detector.py                Masks, contours, tip selection, and optical-flow fallback
  utils/
    utils.py                   Motion models, frequency/extrema helpers, CSV/JSON helpers
    plot_export.py             Matplotlib PNG export helpers
    contansts.py               Fit bounds and OpenCV colors
    clean_csv.py               Separate oscilloscope CSV cleanup and plotting workflow
data/
  json/
    hsv.json                   Saved tip-detection settings
    parameters.json            Explicitly saved fit parameters and scale
  csv/                         Default tracking, estimate, and comparison CSVs
  plots/                       Default graph and peak-to-peak images
test/
  damper_eqn_pred.py           Standalone legacy fitting script, not a test runner
docs/
  SimpleHarmonicOscillator.md  This contributor guide
requirements.txt               Runtime dependencies
setup.py                       Package metadata and dependency loading
```

The primary recording flow runs through `AppWindow`, `VideoThread`, and `ImageProcessor`. The analysis widget and utility functions provide model fitting and exports. The comparison window is a separate workflow that reads exported CSV files; it does not control the active recording thread or its graph.

## Launch and UI wiring

[`app/app.py`](../app/app.py) creates the `QApplication`, constructs an [`AppWindow`](../app/windows/app_window.py), displays it, and starts the Qt event loop. Run the app from the project root:

```bash
python app/app.py
```

`AppWindow.__init__` sets the initial recording state and builds the interface through `create_buttons`, `create_labels`, `create_graph_layout`, `create_hsv_slider`, `create_analyze_widget`, and `create_layout`.

The main coordination responsibilities in [`app/windows/app_window.py`](../app/windows/app_window.py) are:

| Method | Responsibility |
|--------|----------------|
| `select_video_file`, `webcam_selection`, `url_submission` | Set the capture source and enable recording controls. |
| `_probe_and_set_frame_size` | Read the source dimensions and populate editable **Frame W/H** values. |
| `select_roi`, `clear_roi` | Select or clear an ROI in source-frame coordinates. |
| `start_video_thread`, `stop_video_thread` | Create, connect, start, and stop the capture worker; reset per-run UI/plot state. |
| `update_image`, `update_graph`, `on_thread_params` | Receive worker signals and update the video, time series, and parameter labels. |
| `zero_position_to_mean` | Subtract the mean from the recorded positions after tracking stops. |
| `export_csv`, `show_peak_to_peak` | Save tracking data or the tagged extrema table and plot. |
| `open_compare_window` | Open the independent CSV comparison UI. |

The `AnalyzeWidget` uses Qt signals (`export_csv_signal`, `peak_to_peak_signal`, and `zero_y_signal`) to request the README workflow's actions from `AppWindow`. Keep the computation and ownership of the recording series in `AppWindow`; the widget supplies controls and displays results rather than owning the tracked data.

## Recording pipeline

### 1. Capture source and frame dimensions

Selecting a file, webcam, or URL sets `AppWindow.video_path`. `_probe_and_set_frame_size` attempts to read the dimensions with OpenCV and falls back to reading a frame. Frame width and height are editable before a run and are passed to `VideoThread`; the app requires each to be at least 8 pixels.

The full-frame height sets the vertical position reference, and declared dimensions are used when mapping a downscaled detection frame back to source pixels. For an ROI, the crop's `(x, y)` offset restores source-frame coordinates. Preserve these conversions when changing source or frame preparation code.

### 2. Start a worker and send signals

`AppWindow.start_video_thread` creates a new [`VideoThread`](../app/processing/video_thread.py) with the source, display mode, detector mode, selected axis, ROI, visual gain, time scale, and frame dimensions. It clears the prior run's points and plot overlays, connects the worker's Qt signals, sends the current tip configuration, and starts the thread.

The worker communicates with the GUI through:

| Signal | Payload | Main-window receiver |
|--------|---------|----------------------|
| `change_pixmap_signal` | OpenCV image (`numpy.ndarray`) | `update_image` |
| `new_contour_signal` | `(time_s, position_px)` floats | `update_graph` |
| `parameter_signal` | Dictionary of run parameters | `on_thread_params` |
| `processing_signal` | Integer progress value | `processing_frame` |
| `finished_signal` | No payload | `video_thread_finished` |

Use signals for UI updates from the worker; do not manipulate Qt widgets directly inside the capture loop. `stop_video_thread` asks the worker to stop and waits for it. The main window disconnects old thread and HSV signal connections before starting another run.

### 3. Prepare each frame and detect the tip

[`VideoThread.run`](../app/processing/video_thread.py) reads frames using `cv2.VideoCapture`. It creates an [`ImageProcessor`](../app/processing/detector.py) for the run and resets its tracking state at the start.

The detection image is prepared by `_prepare_detect_frame`:

- With an ROI, the worker crops the source frame and keeps the crop at native resolution.
- Without an ROI, frames wider than `DETECT_MAX_WIDTH` (640 pixels) are resized for detection speed.
- The worker retains scale factors and ROI offsets so detected centers and tip lengths can be mapped back to the full-frame coordinate system.

`VideoThread._detect` chooses a detector using the mask option:

- **Tip Color** calls `ImageProcessor.get_color_mask`.
- **Edge Detection** calls `ImageProcessor.get_edges`.
- The remaining circle mode calls `ImageProcessor.get_best_circle`.

The result is passed to `ImageProcessor.get_contours`, which returns a dictionary containing the mask, a contour-annotated image, and zero or one selected contour. The tracking pipeline expects the chosen item to provide a center, area, and bounding box.

In [`app/processing/detector.py`](../app/processing/detector.py), `normalize_tip_hsv` merges saved or legacy settings with defaults. `get_color_mask` blurs the frame, converts it to HSV, combines the active color ranges (including the two red hue ranges), and applies morphological opening and closing. **Black only** instead selects pixels by their brightness threshold. Contour candidates are filtered by the configured area limits. `_pick_tip_contour` prefers the largest candidate near the previous center (within 120 detection-frame pixels), otherwise it chooses the largest candidate.

`_subpixel_from_contour` refines the selected center using binary image moments through `subpixel_centroid_from_mask`. When a color contour is briefly missing, `_lk_fallback` attempts Lucas–Kanade optical flow for up to 15 missed frames before resetting the track. This fallback is short-term continuity support; the normal colored-tip measurement comes from the mask and contour.

`HSVSlider` in [`app/windows/hsv_slider.py`](../app/windows/hsv_slider.py) owns the UI controls for mode, HSV/brightness thresholds, and area limits. `current_config` produces the detector configuration, `tip_config_signal` sends updates to the active worker, `save_hsv` writes `data/json/hsv.json`, and `reset_defaults` restores the detector defaults. If adding a detection setting, update its defaults/normalization in `detector.py` as well as the slider and persistence path where applicable.

### 4. Convert a detection into a time-position sample

For each selected center, `VideoThread.run` maps the detection-frame center to full-frame coordinates, including ROI offsets and resize factors. `_axis_position` returns:

- Horizontal: `position_px = center_x`
- Vertical: `position_px = full_frame_height - center_y`, so upward motion is positive

The emitted time is:

```text
time_s = time_scale * frame_number / frame_rate
```

`frame_rate` comes from video metadata, with a 30 FPS fallback when it is missing or invalid. The app assumes a constant frame rate; it does not use per-frame presentation timestamps. `time_scale` defaults to 1.0 and is applied before samples reach analysis or CSV export.

On a detected frame, the worker emits a `(time_s, position_px)` pair through `new_contour_signal`. A frame with no detected or temporarily tracked tip does not produce a position sample. `AppWindow.update_graph` appends each received pair to `data_points` and refreshes the visible plot periodically. The working series is therefore a list of `(time_s, position_px)` tuples, not image frames or detector objects.

## Position plot and view interaction

`AppWindow.create_graph_layout` creates the live position plot and its overlays/markers. Display-only overlays should not be used as inputs to the tracking CSV or comparison series.

[`GraphViewBox`](../app/windows/graph_viewbox.py) is the shared pyqtgraph view. Its modifier handling maps Ctrl to the x axis and Shift to the y axis. The plot tooltip documents wheel zoom, modifier-assisted axis zoom, and left-drag panning. This same view class is used by the comparison plot.

`select_roi` uses OpenCV's ROI selector on a preview capped at 1000 by 650 pixels, then maps the selected rectangle back to source-frame coordinates. The `roi` stored by the main window and passed to `VideoThread` is `(x, y, width, height)` in full-frame pixels. The worker crops the original frame using that rectangle; it does not use preview coordinates for detection.

## Zeroing and single-run exports

`AnalyzeWidget` in [`app/windows/analyze_widget.py`](../app/windows/analyze_widget.py) supplies the **Zero Y (mean)**, **Export CSV**, and **Peak to Peak** controls. It emits signals for these actions; `AppWindow.create_analyze_widget` connects them to the corresponding main-window methods.

`zero_position_to_mean` requires at least two samples and a stopped worker. It subtracts the mean position from every recorded sample and rebuilds the plot series. It does not change timestamps. The original absolute position is not retained in `data_points` after this action, so a later export contains the centered values.

`export_csv` delegates writing to `VideoThread.save_timeseries_csv`, passing the current samples, selected axis, and optional millimeter scale. A run can be exported without using the separate **Estimate** fit action; fitting is not a prerequisite for the README's recording-and-export workflow.

**Peak to Peak** calls `find_peak_to_peak_extrema` to find and tag tops, bottoms, and plateaus. The resulting markers are added to the live plot; the CSV and PNG each have their own save dialog. Extrema are found using SciPy `find_peaks` with prominence based on the signal's overall range.

### Output formats

`VideoThread.save_timeseries_csv` writes `time_s`, `{axis}_px`, and `{axis}_mm`. The millimeter column is blank when `mm_per_pixel` is zero; otherwise it is `position_px * mm_per_pixel`. The comparison reader also accepts `position_px` and matching `x_mm`/`y_mm` columns for imported series.

- **Export CSV** writes the current tracked series through a save dialog, suggested as `tracked_tip_{axis}.csv`.
- **Peak to Peak** can save a tagged extrema CSV (`time_s`, `position_px`, `label`, `peak_to_peak_px`) and a PNG.

`find_peak_to_peak_extrema` and its CSV writer are in [`app/utils/utils.py`](../app/utils/utils.py). Keep exported column names and units synchronized with the comparison readers when changing either schema. Optional fit and displacement-graph exports use other `AnalyzeWidget` controls and are separate from the README workflow.

## Comparison workflow and data model

[`CompareWindow`](../app/windows/compare_window.py) loads exported files and maintains its own state: `_baseline`, `_compare`, optional `_baseline_extrema`, configured `_phase_ranges`, the phase-selection graph region, and generated `_outlier_rows`. Loading a new baseline clears the old ranges and extrema because they belong to the previous baseline.

### Input formats and normalization

`_read_tracking_csv` requires `time_s` and at least two numeric timestamps. It accepts either `x_px` / `y_px`, `position_px`, or `x_mm` / `y_mm`; it reads optional millimeter values when present, skips rows without numeric times, sorts samples by time, and stores arrays under `time_s`, `px`, and `mm`.

`_read_peak_to_peak_csv` requires `time_s`, `label`, and `peak_to_peak_px`. It retains valid `top`, `bottom`, and `plateau` rows and sorts them by time. These tagged extrema are optional, but are needed for baseline cycle and P2P statistics that use the exported baseline peaks.

### Alignment, ranges, and comparison

Loading both tracking files calls `_align_and_redraw(estimate_offset=True)`. `estimate_phase_offset` searches candidate time shifts (bounded to about one estimated cycle when a baseline frequency is available) and chooses the shift with the strongest normalized overlap correlation. The displayed compare times are `compare_time + phase_offset`; changing **Phase offset (s)** redraws and recalculates comparisons without rerunning the estimate.

When both inputs contain usable millimeter values, the plot uses millimeters; otherwise it compares pixel values. `_series_position` mean-centers the selected series for overlay. Display checkboxes control curve visibility only and do not change the measurements.

The baseline's `LinearRegionItem` selects a time interval. `_add_phase_range` saves each range as:

```python
{
    "label": str,
    "start_s": float,
    "end_s": float,
    "expected_cycles": int,
    "smooth": bool,
}
```

Ranges are sorted by start time and overlapping ranges are rejected. A zero `expected_cycles` value means use the tagged baseline cycle count when available. `_remove_phase_range` removes selected rows from the table.

During redraw, `_smooth_phase_ranges` creates the optional display/export baseline curve for ranges marked for smoothing. With tagged baseline extrema, the smoothing path can use phase-specific extrema information. It does not overwrite the raw baseline series. `comparison_outliers` finds point residuals over the overlapping time interval; `behavior_outliers` checks range-level cycle and peak-to-peak findings when the necessary pixel series and baseline extrema are available.

### Comparison exports and report generation

- `Export ranges CSV…` writes one row per configured baseline range, with available baseline and aligned compare coverage/statistics.
- `Save outliers CSV…` writes the current point and behavior findings, including a `tag` severity column.
- `Save smoothed baseline CSV…` writes the baseline series with the selected ranges' smoothing applied.
- `Export report table…` builds an internal and a client-facing Word report from the selected ranges and available comparison data. Report construction is handled by `_save_report_table` and its helpers in `compare_window.py`; `python-docx` creates the documents and a headless Matplotlib canvas supplies waveform images.

`_save_report_table` groups ranges by behavior, maps compare samples into each baseline interval using the phase offset, and calculates available coverage and motion statistics. `_aligned_phase_samples`, `_pearson_correlation`, and `_weighted_correlation` prepare and score aligned waveform samples. The internal and client report variants are built separately; update both when changing shared report data or presentation. Comparison exports can contain incomplete coverage or unavailable measurements, so preserve explicit missing values rather than assuming each range has a complete counterpart. Keep the UI table, CSV column list, and report content aligned when extending the range data model.

## Extension map

| Change | Start here | Also check |
|--------|------------|------------|
| Add or alter a recording control | `app/windows/app_window.py` | Worker constructor arguments and signal wiring in `video_thread.py` |
| Change tip-color settings or add a mode | `app/windows/hsv_slider.py` | Defaults, normalization, masking, and tracking in `processing/detector.py`; saved `data/json/hsv.json` compatibility |
| Change frame scaling, time, axis position, ROI, or emitted values | `app/processing/video_thread.py` | `AppWindow.update_graph`, CSV writers/readers, and `compare_window.py` |
| Change tracking behavior or fallback | `app/processing/detector.py` | Mask and tracking display behavior in `video_thread.py` |
| Change zeroing or tracking/peak-to-peak exports | `app/windows/app_window.py` | `VideoThread.save_timeseries_csv` and extrema helpers in `app/utils/utils.py` |
| Change accepted comparison CSVs or calculations | `app/windows/compare_window.py` | `VideoThread.save_timeseries_csv` and peak-to-peak CSV writers |
| Change comparison range fields or reports | `app/windows/compare_window.py` | Range table, range CSV fields, smoothing, outlier logic, and both report variants |

## Development notes

- Python package dependencies and the supported Python range are declared in [`requirements.txt`](../requirements.txt) and [`setup.py`](../setup.py). The project currently uses PyQt5, OpenCV, NumPy, SciPy, pyqtgraph, Matplotlib, pandas, and python-docx in the relevant workflows.
- Run the GUI from the project root so the `app` imports and project data paths resolve as expected.
- The repository's `test/damper_eqn_pred.py` is a standalone legacy fitting script that reads a particular CSV; it is not a discoverable automated test suite. No test runner configuration is defined here.
- For a focused manual check of a recording change, load a short video, exercise the intended axis and detector mode, confirm the emitted graph and `time_s`/position CSV, and verify that the comparison window can reload the resulting CSV. For comparison changes, use baseline, compare, and tagged peak-to-peak CSVs when the changed calculations depend on extrema.
- Keep changes to the README separate from this architecture reference: the README is the short user-facing workflow, while this document explains its implementation.
