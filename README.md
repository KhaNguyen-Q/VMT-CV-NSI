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

1. Select a video, webcam, or URL.
2. Click **Set ROI** and drag a tight box around the tip (critical for &lt;1 cm motion).
3. Choose **Horizontal (x)** or **Vertical (y)**.
4. Use **Tip Color (Red/Blue)**, set display to **Mask**, tune tip sliders.
5. Click **Run**. File videos start immediately; equilibrium updates live.
6. Stop → set **mm/pixel** if you have a scale → **Estimate** (Hz, P2P, FFT) → **Export CSV** or **Peak to Peak** for matplotlib graphs and raw csv data
7. Optional **Visual gain** enlarges the plot/overlay only; stored data stays true pixels/mm.


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
