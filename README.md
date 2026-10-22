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

In the comparison window, **Export report table** creates an internal NSI report and a client-facing report. The client report keeps the phase results and waveform plots, with a results summary, but omits calculation details and internal QA notes. It is saved beside the selected report with `_CLIENT` appended to the filename.

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
