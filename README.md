# Computer Vision: 1D Spring / Slider Oscillator

## Overview

Track a **spring** (vertical) or **slider** (horizontal) object in video, extract 1D position vs time, and fit an underdamped harmonic oscillator:

\[
x(t) = A e^{-\gamma t} \cos(\omega t + \phi) + C
\]

This project no longer models pendulum arcs, pivots, or string length. For architecture details, see [docs/SPRING_SLIDER.md](docs/SPRING_SLIDER.md).

## Prerequisites

- **Python 3.10+** (tested on **3.14** with current `requirements.txt`)
- Git (optional)

> **Note:** Old exact pins (`numpy==1.25.2`, etc.) fail on Python 3.14 because they have no wheels and build from source. Use the flexible pins in this repo.

## Setup

```bash
git clone https://github.com/rudrodip/Harmonic-Oscillator-CV
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

See [docs/SPRING_SLIDER.md](docs/SPRING_SLIDER.md) for small-amplitude guidance (ROI, subpixel tracking, why not to trust motion magnification as measurement).

## Object detection

- **Tip Color (Red/Blue)** — tracks one translucent tip that is red and/or blue (dual HSV + area filter + track lock)
- Edge detection
- Hough circles

For a translucent tip: lower `smin` if the mask is empty; raise `min_area` if noise blobs appear; use **Red only** or **Blue only** if the wrong color in the scene is hijacking the track.
## Data collection

Frames → blob center \((c_x, c_y)\) → 1D position:

- Horizontal: \(x = c_x\)
- Vertical: \(y = H - c_y\) (OpenCV y flipped so up is positive)

## Curve fitting

SciPy `curve_fit` fits the underdamped model above. Envelope curves \(\pm A e^{-\gamma t} + C\) are plotted for visualization.

## Parameter estimation

| Quantity | Formula |
|----------|---------|
| Period \(T\) | \(2\pi / \omega\) |
| Frequency \(f\) | \(\omega / 2\pi\) |
| Natural \(\omega_0\) | \(\sqrt{\omega^2 + \gamma^2}\) |
| \(k/m\) (mass-spring) | \(\omega_0^2\) |

Equilibrium \(C\) is the fitted offset; a tracked mean equilibrium line is drawn on the video when overlays are enabled.

## Contributing

Contributions welcome via issues and pull requests.
