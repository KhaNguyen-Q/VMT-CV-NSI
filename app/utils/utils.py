import os
import csv
import json
import numpy as np
from scipy.signal import find_peaks

# Function to load JSON data from a file
def load_json(filename):
    try:
        with open(filename, "r") as f:
            hsv = json.load(f)
            return hsv
    except Exception as e:
        print(e)

# Function to save JSON data to a file
def save_json(filename, jsonfile):
    with open(filename, "w") as f:
        json.dump(jsonfile, f)

    print(f'Saved {filename}')

def get_project_root(start_directory):
    current_directory = start_directory

    # Search for the marker file (.project_root) or a specific folder (e.g., 'src')
    while True:
        if os.path.exists(os.path.join(current_directory, 'setup.py')):
            return current_directory

        current_directory = os.path.dirname(current_directory)

        # Break if we have reached the root directory
        if current_directory == start_directory or current_directory == os.path.dirname(current_directory):
            break
    return ''

# Function to model an underdamped harmonic oscillator
def underdamped_harmonic_oscillator(t, A, gamma, w, phi, C):
    """
    Calculate the position of an underdamped harmonic oscillator at time t.

    Args:
        t: Time values.
        A: Amplitude of oscillation.
        gamma: Damping coefficient.
        w: Angular frequency of oscillation.
        phi: Phase angle.
        C: Constant offset (equilibrium).

    Returns:
        Position values at the given time points.
    """
    return A * np.exp(-gamma * t) * np.cos(w * t + phi) + C

def simple_harmonic_oscillator(t, A, w, phi, C):
    """
    Calculate the position of a simple harmonic oscillator at time t.

    Args:
        t: Time values.
        A: Amplitude of oscillation.
        w: Angular frequency of oscillation.
        phi: Phase angle.
        C: Constant offset (equilibrium).

    Returns:
        Position values at the given time points.
    """
    return A * np.cos(w * t + phi) + C

# Function to model the upper decaying component of a curve
def upper_decaying_component_curve(t, A, gamma, C):
    """
    Calculate the upper decaying component of a curve at time t.

    Args:
        t: Time values.
        A: Amplitude of decay.
        gamma: Decay rate.
        C: Constant offset.

    Returns:
        Value of the upper decaying component at the given time points.
    """
    return C + A * np.exp(-gamma * t)

# Function to model the lower decaying component of a curve
def lower_decaying_component_curve(t, A, gamma, C):
    """
    Calculate the lower decaying component of a curve at time t.

    Args:
        t: Time values.
        A: Amplitude of decay.
        gamma: Decay rate.
        C: Constant offset.

    Returns:
        Value of the lower decaying component at the given time points.
    """
    return C - A * np.exp(-gamma * t)


def opencv_to_cartesian(opencv_coords, frame_height):
    """
    Convert coordinates from OpenCV coordinate system to Cartesian coordinate system.

    Parameters:
        opencv_coords (tuple): A tuple containing the (x, y) coordinates in OpenCV coordinate system.
        frame_height (int): The height (number of rows) of the image.

    Returns:
        tuple: A tuple containing the corresponding (x, y) coordinates in Cartesian coordinate system.

    Notes:
        In OpenCV coordinate system, the origin (0,0) is at the top-left corner of the image.
        In Cartesian coordinate system, the origin (0,0) is at the bottom-left corner of the image.

        This function converts OpenCV coordinates to Cartesian coordinates using the formula:
        x_cartesian = x_opencv
        y_cartesian = frame_height - y_opencv
    """
    x_opencv, y_opencv = opencv_coords
    x_cartesian = x_opencv
    y_cartesian = frame_height - y_opencv
    return x_cartesian, y_cartesian


def peak_to_peak(positions):
    """Peak-to-peak amplitude of a 1D position series."""
    arr = np.asarray(positions, dtype=float)
    if arr.size == 0:
        return 0.0
    return float(np.max(arr) - np.min(arr))


def dominant_fft_frequency(times, positions):
    """
    Estimate dominant frequency (Hz) via FFT of a (possibly uneven) time series.
    Resamples onto a uniform grid using the median dt.
    """
    t = np.asarray(times, dtype=float)
    y = np.asarray(positions, dtype=float)
    if t.size < 8:
        return 0.0

    order = np.argsort(t)
    t = t[order]
    y = y[order]
    dt = np.median(np.diff(t))
    if dt <= 0:
        return 0.0

    t_uniform = np.arange(t[0], t[-1], dt)
    if t_uniform.size < 8:
        return 0.0
    y_uniform = np.interp(t_uniform, t, y)
    y_uniform = y_uniform - np.mean(y_uniform)

    spectrum = np.fft.rfft(y_uniform)
    freqs = np.fft.rfftfreq(y_uniform.size, d=dt)
    # Ignore DC
    if freqs.size <= 1:
        return 0.0
    mag = np.abs(spectrum)
    mag[0] = 0.0
    peak_idx = int(np.argmax(mag))
    return float(freqs[peak_idx])

def find_peak_to_peak_extrema(times, positions, prominence_frac=0.05, distance=1):
    """
    Find local extrema and pair opposite peaks for peak-to-peak amplitudes.

    Returns a list of dicts:
      {time_s, position_px, label, peak_to_peak_px}
    where label is "top", "bottom", or "plateau", and peak_to_peak_px is
    |pos - previous opposite extremum| (None for first / plateaus).
    """
    t = np.asarray(times, dtype=float)
    y = np.asarray(positions, dtype=float)
    if t.size != y.size:
        raise ValueError("times and positions must have the same length")
    if t.size < 3:
        return []

    span = float(np.max(y) - np.min(y))
    prominence = max(span * float(prominence_frac), 1e-9)
    # Absolute tolerance for flat-top / flat-bottom detection
    flat_atol = max(span * 1e-6, 1e-9)

    peak_idx, peak_props = find_peaks(y, prominence=prominence, distance=distance)
    trough_idx, trough_props = find_peaks(-y, prominence=prominence, distance=distance)

    peak_plateau = peak_props.get("plateau_size")
    trough_plateau = trough_props.get("plateau_size")

    def _label_for(idx, kind, plateau_sizes, i):
        if plateau_sizes is not None and int(plateau_sizes[i]) > 1:
            return "plateau"
        val = y[idx]
        left_flat = idx > 0 and abs(float(y[idx - 1]) - float(val)) <= flat_atol
        right_flat = idx < y.size - 1 and abs(float(y[idx + 1]) - float(val)) <= flat_atol
        if left_flat or right_flat:
            return "plateau"
        return kind

    extrema = []
    for i, idx in enumerate(peak_idx):
        extrema.append((int(idx), _label_for(idx, "top", peak_plateau, i)))
    for i, idx in enumerate(trough_idx):
        extrema.append((int(idx), _label_for(idx, "bottom", trough_plateau, i)))

    extrema.sort(key=lambda item: item[0])

    rows = []
    last_opposite = None  # (label, position_px) for top/bottom only
    for idx, label in extrema:
        pos = float(y[idx])
        p2p = None
        if label in ("top", "bottom"):
            opposite = "bottom" if label == "top" else "top"
            if last_opposite is not None and last_opposite[0] == opposite:
                p2p = abs(pos - last_opposite[1])
            last_opposite = (label, pos)

        rows.append(
            {
                "time_s": float(t[idx]),
                "position_px": pos,
                "label": label,
                "peak_to_peak_px": p2p,
            }
        )
    return rows


def write_peak_to_peak_csv(out_path, rows):
    """Write peak-to-peak extrema rows to CSV. Returns out_path."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    fieldnames = ["time_s", "position_px", "label", "peak_to_peak_px"]
    with open(out_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            p2p = row.get("peak_to_peak_px")
            writer.writerow(
                {
                    "time_s": row["time_s"],
                    "position_px": row["position_px"],
                    "label": row["label"],
                    "peak_to_peak_px": "" if p2p is None else p2p,
                }
            )
    return out_path


def transform_peak_to_peak_csv(filename, out_path=None):
    """
    Read tracked_tip_x/y CSV, find extrema, write peak_to_peak_{axis}.csv.

    Returns (out_path, rows).
    """
    if not os.path.exists(filename):
        raise FileNotFoundError(f"File {filename} not found")

    df = pd.read_csv(filename)
    if "time_s" not in df.columns:
        raise ValueError(f"Column time_s not found in file {filename}")

    if "x_px" in df.columns:
        axis = "x"
        pos_col = "x_px"
    elif "y_px" in df.columns:
        axis = "y"
        pos_col = "y_px"
    else:
        raise ValueError(f"Columns x_px or y_px not found in file {filename}")

    times = df["time_s"].astype(float).to_numpy()
    positions = df[pos_col].astype(float).to_numpy()
    rows = find_peak_to_peak_extrema(times, positions)

    if out_path is None:
        out_path = os.path.join(os.path.dirname(os.path.abspath(filename)), f"peak_to_peak_{axis}.csv")

    write_peak_to_peak_csv(out_path, rows)
    return out_path, rows

