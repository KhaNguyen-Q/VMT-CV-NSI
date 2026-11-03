"""Whole-recording scan for candidate vibration regions."""

from dataclasses import dataclass

import numpy as np

from .signal_features import (
    NoiseProfile,
    rolling_window_statistics,
)


@dataclass(frozen=True)
class CandidateRegion:
    start_s: float
    end_s: float
    active_windows: int
    peak_density_hz: float
    mean_frequency_hz: float
    mean_periodicity: float
    mean_signal_to_noise_ratio: float
    maximum_rms_noise_ratio: float
    maximum_amplitude_noise_ratio: float

    def to_dict(self):
        return {
            "start_s": self.start_s,
            "end_s": self.end_s,
            "duration_s": self.end_s - self.start_s,
            "active_windows": self.active_windows,
            "peak_density_hz": self.peak_density_hz,
            "mean_frequency_hz": self.mean_frequency_hz,
            "mean_periodicity": self.mean_periodicity,
            "mean_signal_to_noise_ratio": self.mean_signal_to_noise_ratio,
            "maximum_rms_noise_ratio": self.maximum_rms_noise_ratio,
            "maximum_amplitude_noise_ratio": self.maximum_amplitude_noise_ratio,
        }


def detect_candidate_regions(
    times,
    positions,
    cycles,
    noise_profile: NoiseProfile,
    *,
    window_seconds=0.5,
    merge_gap_seconds=0.5,
    minimum_cycle_snr=0.5,
):
    """Scan the complete waveform and group noise-relative oscillation windows."""
    times = np.asarray(times, dtype=float)
    positions = np.asarray(positions, dtype=float)
    sample_interval = float(np.median(np.diff(times)))
    window_size = min(
        positions.size,
        max(8, int(round(window_seconds / sample_interval))),
    )
    step = max(1, window_size // 4)
    windows = rolling_window_statistics(times, positions, window_size, step)
    if not windows:
        return []

    cycle_starts = np.asarray([cycle.start_s for cycle in cycles])
    cycle_ends = np.asarray([cycle.end_s for cycle in cycles])
    epsilon = np.finfo(float).eps
    noise_rms = max(noise_profile.rms, epsilon)
    noise_amplitude = max(noise_profile.peak_to_peak, epsilon)
    energy_threshold = max(1.5, 1.0 + minimum_cycle_snr * 0.5)
    amplitude_threshold = 1.5
    observations = []

    for start_index, end_index, rms, _std, peak_to_peak in windows:
        start_s = float(times[start_index])
        end_s = float(times[end_index])
        duration = max(end_s - start_s, sample_interval)
        first_cycle = int(np.searchsorted(cycle_ends, start_s, side="right"))
        after_last_cycle = int(np.searchsorted(cycle_starts, end_s, side="left"))
        window_cycles = cycles[first_cycle:after_last_cycle]
        qualifying_cycles = [
            cycle
            for cycle in window_cycles
            if cycle.amplitude / noise_amplitude >= minimum_cycle_snr
            and cycle.frequency_hz > 0
            and cycle.end_s - cycle.start_s <= window_seconds * 2.0
        ]
        peak_density = len(qualifying_cycles) / duration
        mean_frequency = (
            float(np.mean([cycle.frequency_hz for cycle in qualifying_cycles]))
            if qualifying_cycles
            else 0.0
        )
        mean_periodicity = (
            float(
                np.mean(
                    [cycle.periodicity_strength for cycle in qualifying_cycles]
                )
            )
            if qualifying_cycles
            else 0.0
        )
        mean_snr = (
            float(
                np.mean(
                    [cycle.amplitude / noise_amplitude for cycle in qualifying_cycles]
                )
            )
            if qualifying_cycles
            else 0.0
        )
        rms_ratio = rms / noise_rms
        amplitude_ratio = peak_to_peak / noise_amplitude
        energy_evidence = (
            rms_ratio >= energy_threshold
            and amplitude_ratio >= amplitude_threshold
        )
        oscillation_evidence = (
            bool(qualifying_cycles)
            and mean_frequency > 0
            and mean_snr >= max(minimum_cycle_snr, 1.0)
            and mean_periodicity >= 0.05
        )
        observations.append(
            {
                "start_s": start_s,
                "end_s": end_s,
                "active": energy_evidence or oscillation_evidence,
                "peak_density_hz": peak_density,
                "mean_frequency_hz": mean_frequency,
                "mean_periodicity": mean_periodicity,
                "mean_signal_to_noise_ratio": mean_snr,
                "rms_noise_ratio": rms_ratio,
                "amplitude_noise_ratio": amplitude_ratio,
            }
        )

    groups = []
    current = []
    for observation in observations:
        if not observation["active"]:
            continue
        if current and observation["start_s"] > (
            current[-1]["end_s"] + merge_gap_seconds
        ):
            groups.append(current)
            current = []
        current.append(observation)
    if current:
        groups.append(current)

    if not groups:
        return []

    active_bounds = [(group[0]["start_s"], group[-1]["end_s"]) for group in groups]
    region_bounds = []
    for index, (active_start, active_end) in enumerate(active_bounds):
        start_s = float(times[0]) if index == 0 else region_bounds[-1][1]
        if index == len(active_bounds) - 1:
            end_s = float(times[-1])
        else:
            next_active_start = active_bounds[index + 1][0]
            gap_cycles = [
                cycle
                for cycle in cycles
                if active_end <= cycle.start_s < next_active_start
                and cycle.amplitude / noise_amplitude >= minimum_cycle_snr
                and cycle.frequency_hz > 0
                and cycle.end_s - cycle.start_s <= window_seconds * 2.0
                and cycle.periodicity_strength >= 0.05
            ]
            end_s = (
                max(cycle.start_s for cycle in gap_cycles)
                if gap_cycles
                else (active_end + next_active_start) / 2.0
            )
        region_bounds.append((start_s, end_s))

    regions = []
    for group, (start_s, end_s) in zip(groups, region_bounds):
        regions.append(
            CandidateRegion(
                start_s=start_s,
                end_s=end_s,
                active_windows=len(group),
                peak_density_hz=float(
                    np.mean([item["peak_density_hz"] for item in group])
                ),
                mean_frequency_hz=float(
                    np.mean([item["mean_frequency_hz"] for item in group])
                ),
                mean_periodicity=float(
                    np.mean([item["mean_periodicity"] for item in group])
                ),
                mean_signal_to_noise_ratio=float(
                    np.mean(
                        [item["mean_signal_to_noise_ratio"] for item in group]
                    )
                ),
                maximum_rms_noise_ratio=float(
                    max(item["rms_noise_ratio"] for item in group)
                ),
                maximum_amplitude_noise_ratio=float(
                    max(item["amplitude_noise_ratio"] for item in group)
                ),
            )
        )
    return regions
