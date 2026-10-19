# Simple Harmonic Oscillator — User Guide

This guide explains how to track a spring or slider in a video, read the results, and compare two runs. It is written for someone using the app, not for someone maintaining its code.

## What the app does

The app follows a visible point (usually a colored tip) as it moves in one direction:

- A slider or cart moving left and right uses **Horizontal (x)**.
- A spring or mass moving up and down uses **Vertical (y)**.

It turns the video into a position-over-time graph and can estimate the motion's frequency and size. It is designed for straight-line motion, not pendulum swings.

## Track a video

1. Open the app and choose a video, webcam, or video URL.
2. Choose **Set ROI** and draw a box around the moving tip. Keep the whole path inside the box. The preview is resized when needed, but the selection is mapped back to the original video.
3. Choose **Horizontal (x)** or **Vertical (y)** to match the direction of motion.
4. Choose **Tip Color** and select **Red only**, **Blue only**, or **Both (red or blue)**.
5. Change the display to **Mask**. Adjust the tip color sliders until the moving tip appears as one clear white shape and most of the background is black.
6. Check that the app is following the correct tip in the video or contour view.
7. Click **Run**. Let the video play through the part you want to measure, then click **Stop**.
8. Click **Estimate** to calculate the motion results.

For a small or hard-to-see movement, use a tight ROI and a clear, high-contrast tip. The tip should move enough pixels on screen for the app to distinguish its motion from tracking noise.

### If the app follows the wrong thing

- In **Mask**, adjust the color sliders until the tip is visible and other objects are not.
- If background specks are detected, increase the minimum area; if the tip disappears, lower it again.
- Try **Red only** or **Blue only** if another colored object is being followed.
- Adjust the ROI so it contains the tip's full path but excludes distracting objects.
- Stop and run the video again after changing the tracking setup.

## Understand the analysis

The app offers two fit choices:

- **Damped (underdamped)** allows the motion to get smaller over time. Use this for a spring or slider that gradually settles.
- **Simple (undamped)** assumes the motion stays about the same size.

After tracking stops, choose the fit model and click **Estimate**. The analysis includes:

- **Peak to Peak (P2P):** the full distance from the lowest tracked point to the highest. It is shown in pixels unless you enter a scale.
- **Fit frequency:** the frequency estimated from the selected motion model.
- **FFT frequency:** another frequency estimate, calculated from the repeated motion. Comparing it with the fit frequency can help spot an unreliable result.
- **Period:** the estimated time for one complete back-and-forth cycle.

These are estimates, not guarantees. A clean track over several cycles gives more useful results than a short, noisy, or interrupted track.

**Zero Y (mean)** re-centers the tracked positions around their average. Stop tracking before using it. It changes the displayed/analysed position baseline, not the video.

**Peak to Peak** marks the tracked peaks and valleys. It can save a peak-to-peak CSV or image using the save dialogs.

### Use millimeters instead of pixels

The app needs a scale to convert pixels to millimeters; it cannot work this out from the video resolution or camera distance alone. Find the **mm/pixel** control and enter a scale measured with the camera and object in the same positions as in your recording.

One way to get a scale is to move the tracked point between two known positions. For example, moving it from **−1 cm to +1 cm** is a total movement of **20 mm**. If the tracked position changes by 100 pixels between those points, enter **0.2 mm/pixel** (20 mm ÷ 100 pixels). Keep the camera distance and object position unchanged when using that scale.

With a valid scale, the app can show results in millimeters. If the scale is zero or unknown, use the pixel results instead. A displayed **Tip L** measurement can also help when the visible tip has a known physical length; use its full-frame measurement, not the smaller detection-preview size.

### Export a graph or tracking data

- **Export CSV** saves the tracked position and time values. Use the save dialog to choose the file name and location.
- **Export Graph** saves a displacement-versus-time image. It requires a successful estimate and a non-zero **mm/pixel** scale. Use its **Start** and **End** controls to export a time interval if needed.
- **Estimate** also saves estimate data as a CSV.

The app suggests file names and locations, but the save dialog lets you choose where to put each file.

## Compare two runs

The comparison window lets you line up a baseline run with another run and compare selected parts of their motion.

1. In the main window, open **Compare…**.
2. Choose **Load baseline CSV…** and load the first run's tracking CSV.
3. Choose **Load compare CSV…** and load the second run's tracking CSV.
4. If you have the baseline's **Peak to Peak** CSV, choose **Load baseline peak-to-peak CSV…** as well. This is optional, but provides tagged peak information for the comparison.
5. Review the overlaid graph. The app estimates how to line up the two runs; adjust **Phase offset (s)** if the traces need to move earlier or later relative to each other.
6. Select a behavior range on the baseline graph by dragging across the desired time interval.
7. Choose or enter a **Baseline behavior**, set **Expected cycles** if needed, and optionally select **Smooth this range**. Click **Add selected baseline range**. Ranges cannot overlap.
8. Repeat for other ranges you want to compare.

If tagged baseline peaks are available, an **Expected cycles** value of 0 uses their cycle count. A compare run is needed for statistics between two runs; some baseline range exports can be made with only the baseline loaded.

## Comparison exports

- **Export ranges CSV…** saves the configured ranges and available baseline/compare measurements.
- **Save outliers CSV…** saves the comparison findings and their severity labels.
- **Save smoothed baseline CSV…** saves the original and smoothed baseline data when a range uses smoothing.
- **Export report table…** creates an internal Word report and a client-facing Word report. The client file is saved beside the selected report with **_CLIENT** added to its name.

Use the save dialogs to select the destination. The client report is designed to show results without the internal calculation details and QA notes.

For a plain-language explanation of the report's waveform correlation scores, see [Comparison report: understanding correlation](ComparisonReportCorrelation.md).

## Tips for more reliable results

- Keep the camera still and the moving tip in focus.
- Use good lighting and a clear marker that stands out from the background.
- Make sure the tip moves far enough on screen to be tracked clearly.
- Keep the ROI and slider settings the same when repeating a run.
- Use **Mask** to check that the app sees one tip, not several blobs.
- Let the recording run for the same amount of time when comparing trials.
- Phone videos recorded at a variable frame rate or exported in slow motion may give unreliable timing and frequency. For the most reliable timing, use a constant-frame-rate video and check that its playback speed matches the real motion.

## Set up the app

For installation instructions, see the project [README](../README.md). Once the app and its requirements are installed, start it from the project folder with:

```bash
python app/app.py
```
