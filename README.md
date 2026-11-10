# Computer Vision: 1D Spring / Slider Oscillator

## Overview

Track a **spring** (vertical) or **slider** (horizontal) object in video, extract 1D position vs time

## Prerequisites

- **Python 3.10+** (tested on **3.14** with current `requirements.txt`)
- Git (optional)

> **Note:** Old exact pins (`numpy==1.25.2`, etc.) fail on Python 3.14 because they have no wheels and build from source. Use the flexible pins in this repo.

## Setup

```bash
git clone https://github.com/KhaNguyen-Q/VMT-CV-NSI
cd Harmonic-Oscillator-CV
python -m venv venv
```
*Note: python.exe, set directory to app path. Need folders and files for executeable DEBUGGING

 `pyinstaller --onedir --paths=app app/app.py`

*Note: for complete python.exe that can be shared use --onefile PROD:

`pyinstaller --onefile --paths=app app/app.py`

Activate:

- Windows: `venv\Scripts\activate`
- macOS/Linux: `source venv/bin/activate`

Install:

```bash
pip install -r requirements.txt
```

## Usage

```bash
python app/app.py
```

## Recording window
1. Open the app and choose a video, webcam, or video URL.
2. Set video frame dimentions. Commonly, Frame W: 1920  H: 1080
3. Choose **Horizontal (x)** or **Vertical (y)** to match the direction of motion.
4. Choose **Tip Color** and select **Black only** or **Blue only**
5. Change the display to **Mask**. Adjust the tip color sliders until the moving tip appears as one clear white shape and most of the background is black.
6. Check that the app is following the correct tip in the video or contour view.
7. Click **Run**. Let the video play through the part you want to measure, then click **Stop**.
8. Interract with the graphical viewbox vering the cursor over parts of the view box makes it interactable: 

#Swiping apart or together: changes the x-axis, time 

#Hold shift + Swiping up or down: changes the y-axis 

#Swiping up or down: changes both y-axis and x-axis

9. When satisfied with waveform results, Click **Zero Y (mean)** to center the waveform. Then click, **Export CSV** and **Peak to Peak**

10. Rename and save CSV files anywhere

11. Repeat steps if graph shape is not of desire

## Comparison window

1. Load preprocessed CSV data into these 3 units: 
Load baseline CSV => Set baseline graph data 
Load compare CSV => Set comparison graph data 
Load baseline peak-to-peak CSV... => Set peak-to-peak data 

2. Align the waveforms horizontally by Changing **Phase Offset (s)** to shift the Comparison graph.
3. Select intervals of interests by clicking and dragging the 2 light blue markers
*Note: Hovering the cursor over parts of the graphical view box makes it interactable 
- Swiping apart or together: changes the x-axis, time 
- Hold shift + Swiping up or down: changes the y-axis 
- Swiping up or down: changes both y-axis and x-axis 
4. Depending on the intended interval of waveform. Select **Phase 'x'** appropriate => Input **Expected cycles** ('0' for default) => Select **Add selected baseline range** 
5. When satisfied. Repeat step 4, Changing labeled **Phase 'x'** for each part. Else, Click the **Baseline row** => Click **Remove selected range** 
6. Completing all desired ranges allows the comparison report to be generated:
- **Export report table** => name and save .docx files
7. Open report in Sharepoint or any Microsoft office 365 application supporting *Word*
8. Edit report accordingly 

Use **Auto Detect Phases** as an alternative to selecting ranges manually. Detection first scans the full baseline waveform in overlapping windows for noise-relative energy and oscillation evidence, then runs the phase state machine independently on each candidate region. Choose **Auto Detect** to estimate an amplitude category from the baseline signal, or choose a treatment profile. Use **Manage profiles…** to create, duplicate, rename, tune, and delete profiles; the built-in Auto Detect profile cannot be deleted. Profiles persist in `data/json/detection_profiles.json`. **Detection diagnostics…** shows candidate region intervals and their peak density, frequency, periodicity, and noise-relative energy, followed by candidate cycle counts, frequency/amplitude stability, RMS energy, signal-to-noise ratio, qualification checks, and rejection reasons.

### Statistical analysis of exported range CSVs

After exporting range CSVs, open **Compare…** and choose **Analyze exported range CSVs…**. The analysis workflow opens in its own resizable window. Add CSV files individually or select a folder containing them, choose an output folder, and run all three analyses in one pass:

- **Phase Analysis** summarizes vibration-event (`Phase Summary`) rows by treatment profile and phase.
- **Device-Level Analysis** summarizes events by treatment profile, device, and phase.
- **Device-Variability Analysis** compares each device's event averages within a treatment profile and phase, so devices with more exported events do not have extra weight.

The three analyses run together against one loaded master dataset. The workflow prompts for a name for the output folder, which contains one `master_dataset.csv` with the original summary and cycle-detail rows, one `input_files.csv`, all analysis statistics, and a `visualizations` folder with separate `phase_analysis`, `device_level_analysis`, and `device_variability_analysis` chart folders. Device-Variability Analysis reads the generated `device_level_statistics.csv` and compares the per-device means using the `Device number` tag, so each device contributes one value regardless of how many events it has. Cycle-detail rows are retained in the master dataset but excluded from event-level statistics to avoid counting the same vibration events twice. The loader validates the exported-range schema and records skipped files/rows and missing or invalid numeric values in `input_files.csv`. Variability output also includes candidate ranges based on mean ±2/3 standard deviations and the 5th–95th percentiles. These ranges are statistical candidates, not validated acceptance limits. Waveform overlays are not included because range exports do not contain the underlying time-series data.

In the comparison window, **Export report table** first prompts for optional camera, FPS, treatment profile, device number, and client name metadata. For automatically detected ranges, the internal and client-facing reports and JSON export also include the detection mode, selected and resolved treatment profiles, parameters used, and detection confidence. The JSON contains phase-grouped report values and a flattened `excel_table` with its column order and a Graph API-compatible `graph_payload.values` matrix for appending report rows to a preconfigured Excel table. All files are saved beside the selected report; the client report uses the `_CLIENT` suffix, and the JSON file uses the same base filename. The client report keeps the phase results and waveform plots, with a results summary, but omits calculation details and internal QA notes.

### Uploading report rows to SharePoint Excel

The report export remains local/offline. To upload an exported JSON report, copy `config.example.json` to `config.json` and `mapping.example.json` to `mapping.json`, then configure `config.json` with the Microsoft Entra tenant ID, public-client application ID, SharePoint site ID, workbook path relative to the site's drive, and existing Excel table name. The Entra application must be configured as a public client with `http://localhost` as a redirect URI and granted delegated Microsoft Graph `Files.ReadWrite.All` access. Configure `mapping.json` to match the report columns to the destination Excel table columns, in the required order. The sample mapping preserves report values as text; change a mapping type to `number`, `integer`, or `boolean` only when the corresponding source values use that type.

Run the local mapping and request tests without Microsoft credentials or network access:

```bash
python -m unittest discover -s tests -p test_graph_upload.py -v
```

Run the upload explicitly from the repository root:

```bash
python app/app.py --upload-report path\to\comparison_report.json
```

You can optionally pass `--config path\to\config.json` and `--mapping path\to\mapping.json`. The app uses delegated login, silently reuses a cached session when available, and stores the token cache using the operating system's encrypted persistence. In a packaged build, copy the example files as `config.json` and `mapping.json` beside `app.exe`; run `app.exe --upload-report path\to\comparison_report.json` to upload. Do not distribute configured tenant files or user token caches in a build.

For a plain-language explanation of the report's waveform correlation scores, see [Comparison report: understanding correlation](docs/ComparisonReportCorrelation.md).

## Object detection

- **Tip Color (Red/Blue)** — tracks one translucent tip that is red and/or blue (dual HSV + area filter + track lock)
- Edge detection
- Hough circles

For a translucent tip: lower `smin` if the mask is empty; raise `min_area` if noise blobs appear; use **Red only** or **Blue only** if the wrong color in the scene is hijacking the track.
## Data collection

Frames → blob center c_x, c_y → 1D position:

- Horizontal: x = c_x
- Vertical: y = H - c_y (OpenCV y flipped so up is positive)

## Contributing

Contributions welcome via issues and pull requests.
Ping 'ben' on Microsoft teams for more details
