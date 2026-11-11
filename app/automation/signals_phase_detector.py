"""Trend-based phase transitions using shared cycle and noise features."""

import numpy as np

from .phase_state_machine import (
    PhaseDetectionConfig,
    PhaseDetectionStateMachine,
    VibrationEvent,
)
from .signal_features import (
    NoiseProfile,
    find_energy_decay_interval,
    rolling_window_statistics,
)


_TREND_WINDOW_CYCLES = 9
_TREND_WINDOW_STEP = 1
_SUSTAINED_TREND_WINDOWS = 3
_STABILITY_WINDOW_CYCLES = 5
_PHASE3_WINDOW_SECONDS = 0.3
_PHASE3_DECAY_RATIO = 0.2
_TREND_FREQUENCY_RATIO = 1.1


def _linear_slope(times, values):
    if len(values) < 2:
        return 0.0
    centered_times = np.asarray(times, dtype=float)
    centered_times -= centered_times[0]
    return float(np.polyfit(centered_times, values, 1)[0])


def _coefficient_of_variation(values):
    mean = float(np.mean(values))
    if mean <= np.finfo(float).eps:
        return float("inf")
    return float(np.std(values) / mean)


def _window_features(cycles):
    frequencies = np.asarray([cycle.frequency_hz for cycle in cycles], dtype=float)
    amplitudes = np.asarray([cycle.amplitude for cycle in cycles], dtype=float)
    energies = np.asarray([cycle.rms for cycle in cycles], dtype=float)
    times = np.asarray(
        [(cycle.start_s + cycle.end_s) / 2.0 for cycle in cycles], dtype=float
    )
    return {
        "frequency_mean": float(np.mean(frequencies)),
        "frequency_cv": _coefficient_of_variation(frequencies),
        "frequency_slope": _linear_slope(times, frequencies),
        "amplitude_mean": float(np.mean(amplitudes)),
        "amplitude_cv": _coefficient_of_variation(amplitudes),
        "amplitude_slope": _linear_slope(times, amplitudes),
        "energy_mean": float(np.mean(energies)),
        "energy_slope": _linear_slope(times, energies),
        "periodicity": float(
            np.mean([cycle.periodicity_strength for cycle in cycles])
        ),
    }


def _find_phase3_start(
    times,
    positions,
    noise_profile,
    phase2_cycles,
    phase2_start_s,
    terminal_rest_minimum_seconds,
):
    if not phase2_cycles:
        return None
    interval = find_energy_decay_interval(
        times,
        positions,
        noise_profile,
        phase2_start_s,
        window_seconds=_PHASE3_WINDOW_SECONDS,
        decay_ratio=_PHASE3_DECAY_RATIO,
        minimum_duration=_PHASE3_WINDOW_SECONDS,
        terminal_rest_minimum_seconds=terminal_rest_minimum_seconds,
    )
    return interval.start_s if interval is not None else None


def detect_signals_events(
    times,
    positions,
    region,
    region_cycles,
    noise_profile: NoiseProfile,
    config: PhaseDetectionConfig,
):
    """Detect repeated events within a broad candidate region."""
    events = []
    diagnostics = []
    remaining_cycles = region_cycles
    while remaining_cycles:
        event, event_diagnostics = _detect_signals_event(
            times,
            positions,
            region,
            remaining_cycles,
            noise_profile,
            config,
        )
        diagnostics.extend(event_diagnostics)
        if event is None:
            break
        events.append(event)
        next_start = event.phase3_start_s + min(
            _PHASE3_WINDOW_SECONDS,
            config.minimum_rest_seconds / 2.0,
        )
        next_index = next(
            (
                index
                for index, cycle in enumerate(remaining_cycles)
                if cycle.start_s >= next_start
            ),
            None,
        )
        if next_index is None:
            break
        remaining_cycles = remaining_cycles[next_index:]
    return events, diagnostics


def _detect_signals_event(
    times,
    positions,
    region,
    region_cycles,
    noise_profile: NoiseProfile,
    config: PhaseDetectionConfig,
):
    """Detect one event using stable Phase 1 cycles, frequency trend, and energy decay."""
    candidate_machine = PhaseDetectionStateMachine(noise_profile, config)
    seed_cycle_index = None
    for index, cycle in enumerate(region_cycles):
        candidate_machine.process_cycle(cycle)
        if candidate_machine.current_event is not None:
            seed_cycle_index = index
            break

    if seed_cycle_index is None:
        return None, candidate_machine.candidate_diagnostics

    event = candidate_machine.current_event
    phase1_cycles = list(event.phase1_cycle_features)
    baseline_frequency = float(event.phase1_frequency_hz)
    if baseline_frequency <= 0:
        return None, candidate_machine.candidate_diagnostics

    eligible_cycles = [
        cycle
        for cycle in region_cycles[seed_cycle_index:]
        if cycle.amplitude
        >= noise_profile.peak_to_peak
        * config.amplitude_noise_multiple
        * 0.5
    ]
    phase1_count = len(phase1_cycles)
    stability_windows = []
    for end_index in range(
        _STABILITY_WINDOW_CYCLES,
        len(eligible_cycles) + 1,
    ):
        start_index = end_index - _STABILITY_WINDOW_CYCLES
        features = _window_features(eligible_cycles[start_index:end_index])
        stability_windows.append(
            {
                "start_index": start_index,
                "end_index": end_index,
                "stable": (
                    features["frequency_cv"] <= config.maximum_frequency_cv
                    and features["amplitude_cv"] <= config.maximum_amplitude_cv
                    and features["energy_mean"] >= noise_profile.rms
                ),
            }
        )
    rolling_windows = []
    for end_index in range(
        _TREND_WINDOW_CYCLES,
        len(eligible_cycles) + 1,
        _TREND_WINDOW_STEP,
    ):
        start_index = end_index - _TREND_WINDOW_CYCLES
        sample = eligible_cycles[start_index:end_index]
        features = _window_features(sample)
        duration = max(
            sample[-1].end_s - sample[0].start_s,
            np.finfo(float).eps,
        )
        frequency_gain = (
            features["frequency_slope"] * duration / baseline_frequency
        )
        frequency_score = float(
            np.mean(
                [
                    np.clip(frequency_gain / 0.05, 0.0, 1.0),
                    np.clip(
                        (features["frequency_mean"] / baseline_frequency - 1.0)
                        / (_TREND_FREQUENCY_RATIO - 1.0),
                        0.0,
                        1.0,
                    ),
                ]
            )
        )
        energy_score = float(
            np.clip(
                features["energy_slope"] * duration
                / max(features["energy_mean"] * 0.1, np.finfo(float).eps),
                0.0,
                1.0,
            )
        )
        amplitude_score = float(
            np.clip(
                features["amplitude_slope"] * duration
                / max(features["amplitude_mean"] * 0.1, np.finfo(float).eps),
                0.0,
                1.0,
            )
        )
        trend_score = float(
            0.5 * frequency_score
            + 0.25 * energy_score
            + 0.15 * amplitude_score
            + 0.1 * features["periodicity"]
        )
        rising = (
            features["frequency_mean"]
            >= baseline_frequency * _TREND_FREQUENCY_RATIO
            and features["frequency_slope"] > 0
            and frequency_gain >= 0.05
        )
        rolling_windows.append(
            {
                **features,
                "start_index": start_index,
                "end_index": end_index,
                "frequency_gain": frequency_gain,
                "frequency_ratio": (
                    features["frequency_mean"] / baseline_frequency
                ),
                "transition_score": trend_score,
                "rising": rising,
            }
        )

    rising_run = []
    confirmed_window = None
    for window in rolling_windows:
        if window["rising"]:
            rising_run.append(window)
            if len(rising_run) >= _SUSTAINED_TREND_WINDOWS:
                confirmed_window = rising_run[0]
                break
        else:
            rising_run.clear()

    diagnostics = []
    if confirmed_window is None:
        diagnostics.extend(candidate_machine.candidate_diagnostics)
        if rolling_windows:
            last_window = rolling_windows[-1]
            diagnostics.append(
                _trend_diagnostic(
                    eligible_cycles,
                    phase1_count,
                    last_window,
                    noise_profile,
                    config,
                    passed=False,
                    reasons=["No sustained upward frequency trend"],
                    stable_windows=_longest_stable_run(stability_windows),
                    phase1_cycles=phase1_cycles,
                )
            )
        return None, diagnostics

    phase2_start_index = min(
        confirmed_window["start_index"] + _TREND_WINDOW_CYCLES // 2,
        len(eligible_cycles) - 1,
    )
    stable_phase1_windows = _longest_stable_run(
        [
            window
            for window in stability_windows
            if window["end_index"] <= phase2_start_index
        ]
    )
    phase2_start_s = eligible_cycles[phase2_start_index].start_s
    phase1_cycles = [
        cycle for cycle in eligible_cycles if cycle.start_s < phase2_start_s
    ]
    phase2_cycles = eligible_cycles[phase2_start_index:]
    phase3_start_s = _find_phase3_start(
        times,
        positions,
        noise_profile,
        phase2_cycles,
        phase2_start_s,
        config.terminal_rest_minimum_seconds,
    )
    if phase3_start_s is None or phase3_start_s <= phase2_start_s:
        diagnostics.append(
            _trend_diagnostic(
                eligible_cycles,
                phase1_count,
                rolling_windows[-1],
                noise_profile,
                config,
                passed=False,
                reasons=["No sustained Phase 3 energy decay"],
                stable_windows=stable_phase1_windows,
                phase1_cycles=phase1_cycles,
            )
        )
        return None, diagnostics

    event.phase2_start_s = float(phase2_start_s)
    event.phase2_cycles = [
        cycle for cycle in phase2_cycles if cycle.start_s < phase3_start_s
    ]
    event.phase3_start_s = float(phase3_start_s)
    event.end_s = float(region.end_s)
    event.phase1_cycles = len(phase1_cycles)
    event.phase1_cycle_features = phase1_cycles
    event.candidate_frequency_cv = _coefficient_of_variation(
        [cycle.frequency_hz for cycle in phase1_cycles]
    )
    event.candidate_amplitude_cv = _coefficient_of_variation(
        [cycle.amplitude for cycle in phase1_cycles]
    )
    event.candidate_periodicity_strength = float(
        np.mean([cycle.periodicity_strength for cycle in phase1_cycles])
    )
    diagnostics.append(
        _trend_diagnostic(
            eligible_cycles,
            phase1_count,
            confirmed_window,
            noise_profile,
            config,
            passed=True,
            reasons=[],
            stable_windows=stable_phase1_windows,
            phase1_cycles=phase1_cycles,
        )
    )
    return event, diagnostics


def _longest_stable_run(windows):
    longest = 0
    current = 0
    for window in windows:
        current = current + 1 if window["stable"] else 0
        longest = max(longest, current)
    return longest


def _trend_diagnostic(
    cycles,
    phase1_count,
    features,
    noise_profile,
    config,
    *,
    passed,
    reasons,
    stable_windows,
    phase1_cycles,
):
    window_size = min(_TREND_WINDOW_CYCLES, len(cycles))
    qualification_checks = {
        "minimum_cycles": phase1_count >= 2,
        "frequency_stability": features["frequency_cv"],
        "amplitude_stability": features["amplitude_cv"],
        "minimum_snr": True,
        "periodicity": features["periodicity"],
        "stable_phase1_windows": stable_windows > 0,
        "sustained_frequency_trend": passed,
        "phase3_energy_decay": passed,
    }
    return {
        "candidate_cycles": max(phase1_count, window_size),
        "average_frequency_hz": features["frequency_mean"],
        "frequency_stability": features["frequency_cv"],
        "average_amplitude": features["amplitude_mean"],
        "amplitude_stability": features["amplitude_cv"],
        "rms_energy": features["energy_mean"],
        "signal_to_noise_ratio": (
            features["amplitude_mean"] / noise_profile.peak_to_peak
            if noise_profile.peak_to_peak > 0
            else float("inf")
        ),
        "minimum_cycle_snr": (
            min(cycle.amplitude for cycle in cycles)
            / noise_profile.peak_to_peak
            if cycles and noise_profile.peak_to_peak > 0
            else float("inf")
        ),
        "periodicity_strength": features["periodicity"],
        "qualification_checks": qualification_checks,
        "passed": passed,
        "rejection_reasons": reasons,
        "frequency_slope": features["frequency_slope"],
        "frequency_cv": features["frequency_cv"],
        "rolling_frequency_ratio": features["frequency_ratio"],
        "amplitude_slope": features["amplitude_slope"],
        "amplitude_cv": features["amplitude_cv"],
        "energy_slope": features["energy_slope"],
        "transition_score": features["transition_score"],
        "phase_confidence": features["transition_score"],
        "phase1_confidence": _phase1_confidence(phase1_cycles, config),
        "phase2_confidence": features["transition_score"],
        "phase3_confidence": 1.0 if passed else 0.0,
        "stable_phase1_windows": stable_windows,
    }


def _phase1_confidence(cycles, config):
    if not cycles:
        return 0.0
    frequency_cv = _coefficient_of_variation(
        [cycle.frequency_hz for cycle in cycles]
    )
    amplitude_cv = _coefficient_of_variation(
        [cycle.amplitude for cycle in cycles]
    )
    return float(
        np.mean(
            [
                np.clip(
                    1.0
                    - frequency_cv
                    / max(config.maximum_frequency_cv, np.finfo(float).eps),
                    0.0,
                    1.0,
                ),
                np.clip(
                    1.0
                    - amplitude_cv
                    / max(config.maximum_amplitude_cv, np.finfo(float).eps),
                    0.0,
                    1.0,
                ),
            ]
        )
    )
