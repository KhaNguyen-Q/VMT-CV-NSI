"""Signal preparation and cycle/noise feature extraction for phase detection."""

from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks, savgol_filter

try:
    from utils.utils import dominant_fft_frequency
except ModuleNotFoundError:
    from ..utils.utils import dominant_fft_frequency


@dataclass(frozen=True)
class NoiseProfile:
    rms: float
    peak_to_peak: float
    standard_deviation: float
    average_frequency_hz: float


@dataclass(frozen=True)
class CycleFeature:
    start_s: float
    end_s: float
    frequency_hz: float
    amplitude: float
    rms: float
    periodicity_strength: float


@dataclass(frozen=True)
class QuietInterval:
    start_s: float
    end_s: float


def prepare_signal(times, positions):
    """Return finite, time-sorted samples with duplicate timestamps combined."""
    times = np.asarray(times, dtype=float)
    positions = np.asarray(positions, dtype=float)
    if times.ndim != 1 or positions.ndim != 1 or times.size != positions.size:
        raise ValueError("times and positions must be one-dimensional arrays of equal size")

    finite = np.isfinite(times) & np.isfinite(positions)
    times = times[finite]
    positions = positions[finite]
    if times.size < 8:
        raise ValueError("At least eight finite signal samples are required")

    order = np.argsort(times, kind="stable")
    times = times[order]
    positions = positions[order]
    unique_times, inverse, counts = np.unique(
        times, return_inverse=True, return_counts=True
    )
    if unique_times.size != times.size:
        positions = np.bincount(inverse, weights=positions) / counts
        times = unique_times
    if times.size < 8 or np.any(np.diff(times) <= 0):
        raise ValueError("Signal timestamps must contain at least eight unique values")
    return times, positions


def rolling_window_statistics(times, positions, window_size, step):
    windows = []
    for start in range(0, positions.size - window_size + 1, step):
        values = positions[start : start + window_size]
        centered = values - np.mean(values)
        windows.append(
            (
                start,
                start + window_size - 1,
                float(np.sqrt(np.mean(centered * centered))),
                float(np.std(values)),
                float(np.ptp(values)),
            )
        )
    return windows


def estimate_noise_profile(times, positions, window_seconds=0.5, quiet_fraction=0.2):
    """Estimate resting noise from the least-energetic overlapping signal windows."""
    times, positions = prepare_signal(times, positions)
    sample_interval = float(np.median(np.diff(times)))
    window_size = min(
        positions.size,
        max(8, int(round(window_seconds / sample_interval))),
    )
    step = max(1, window_size // 4)
    windows = rolling_window_statistics(times, positions, window_size, step)
    if not windows:
        raise ValueError("Signal is too short to estimate its resting noise floor")

    quiet_count = max(1, int(np.ceil(len(windows) * quiet_fraction)))
    quiet_windows = sorted(windows, key=lambda window: window[3])[:quiet_count]
    rms = float(np.median([window[2] for window in quiet_windows]))
    standard_deviation = float(np.median([window[3] for window in quiet_windows]))
    peak_to_peak = float(np.median([window[4] for window in quiet_windows]))

    frequencies = []
    for start, end, _rms, _std, _peak_to_peak in quiet_windows[:5]:
        frequency = dominant_fft_frequency(times[start : end + 1], positions[start : end + 1])
        frequencies.append(frequency)
    average_frequency = float(np.mean(frequencies)) if frequencies else 0.0

    return NoiseProfile(
        rms=rms,
        peak_to_peak=peak_to_peak,
        standard_deviation=standard_deviation,
        average_frequency_hz=average_frequency,
    )


def _normalized_cycle(times, positions, start, end, points=32):
    mask = (times >= start) & (times <= end)
    if np.count_nonzero(mask) < 3:
        return None
    grid = np.linspace(start, end, points)
    waveform = np.interp(grid, times[mask], positions[mask])
    waveform -= np.mean(waveform)
    norm = float(np.linalg.norm(waveform))
    if norm <= np.finfo(float).eps:
        return None
    return waveform / norm


def extract_cycles(
    times,
    positions,
    noise_profile,
    smoothing_seconds=0.08,
    minimum_peak_distance_seconds=0.08,
):
    """Extract peak-to-peak cycles and their frequency, amplitude, RMS, and periodicity."""
    times, positions = prepare_signal(times, positions)
    sample_interval = float(np.median(np.diff(times)))
    minimum_peak_distance = max(
        1, int(round(minimum_peak_distance_seconds / sample_interval))
    )
    smoothing_window = max(5, int(round(smoothing_seconds / sample_interval)))
    if smoothing_window % 2 == 0:
        smoothing_window += 1
    smoothing_window = min(
        smoothing_window,
        positions.size if positions.size % 2 else positions.size - 1,
    )
    detection_positions = (
        savgol_filter(positions, smoothing_window, polyorder=2)
        if smoothing_window >= 5
        else positions
    )
    signal_span = float(np.ptp(detection_positions))
    prominence = max(
        noise_profile.standard_deviation * 1.5,
        signal_span * 0.005,
        np.finfo(float).eps,
    )
    peaks, _ = find_peaks(
        detection_positions,
        prominence=prominence,
        distance=minimum_peak_distance,
    )

    cycles = []
    signatures = []
    for left, right in zip(peaks[:-1], peaks[1:]):
        period = float(times[right] - times[left])
        if period <= 0:
            continue
        segment = detection_positions[left : right + 1]
        amplitude = float(np.ptp(segment))
        centered = segment - np.mean(segment)
        waveform_rms = float(np.sqrt(np.mean(centered * centered)))
        signature = _normalized_cycle(
            times,
            detection_positions,
            float(times[left]),
            float(times[right]),
        )
        periodicity = 1.0
        if signature is not None and signatures:
            periodicity = float(
                np.mean(
                    [
                        max(0.0, float(np.dot(signature, previous)))
                        for previous in signatures[-3:]
                    ]
                )
            )
        if signature is not None:
            signatures.append(signature)
        cycles.append(
            CycleFeature(
                start_s=float(times[left]),
                end_s=float(times[right]),
                frequency_hz=1.0 / period,
                amplitude=amplitude,
                rms=waveform_rms,
                periodicity_strength=periodicity,
            )
        )
    return cycles


def find_quiet_intervals(
    times,
    positions,
    noise_profile,
    hysteresis_multiple=3.0,
    window_seconds=0.5,
):
    """Find sustained low-energy intervals using thresholds above the noise floor."""
    times, positions = prepare_signal(times, positions)
    sample_interval = float(np.median(np.diff(times)))
    window_size = min(
        positions.size,
        max(8, int(round(window_seconds / sample_interval))),
    )
    step = max(1, window_size // 4)
    windows = rolling_window_statistics(times, positions, window_size, step)
    if not windows:
        return []

    rms_threshold = max(
        noise_profile.rms * hysteresis_multiple,
        np.finfo(float).eps,
    )
    amplitude_threshold = max(
        noise_profile.peak_to_peak * hysteresis_multiple,
        np.finfo(float).eps,
    )
    quiet = [
        window[2] <= rms_threshold and window[4] <= amplitude_threshold
        for window in windows
    ]

    intervals = []
    start_index = None
    for index, is_quiet in enumerate(quiet + [False]):
        if is_quiet and start_index is None:
            start_index = index
        elif not is_quiet and start_index is not None:
            intervals.append(
                QuietInterval(
                    start_s=float(times[windows[start_index][0]]),
                    end_s=float(times[windows[index - 1][1]]),
                )
            )
            start_index = None
    return intervals
