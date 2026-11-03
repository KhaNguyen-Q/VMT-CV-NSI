"""Public automatic phase-detection entry point."""

from dataclasses import asdict, dataclass, replace
import logging

import numpy as np

from .detection_profiles import (
    TreatmentProfile,
    config_values_for_profile,
    default_treatment_profiles,
    resolve_treatment_profile,
)
from .event_regions import detect_candidate_regions
from .phase_state_machine import PhaseDetectionConfig, PhaseDetectionStateMachine
from .signal_features import (
    estimate_noise_profile,
    extract_cycles,
    find_quiet_intervals,
    prepare_signal,
)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PhaseDetectionResult:
    phase_ranges: list[dict]
    events: list[dict]
    candidate_regions: list[dict]
    noise_profile: dict
    cycle_count: int
    diagnostics: list[dict]
    profile_metadata: dict


def detect_phase_ranges(times, positions, config=None, profile=None, profiles=None):
    """Detect complete Phase 1/2/3 ranges in a baseline displacement signal."""
    base_config = config or PhaseDetectionConfig()
    profiles = list(profiles or default_treatment_profiles())
    if profile is None:
        profile = next(
            item for item in profiles if item.name == "Auto Detect"
        )
    elif isinstance(profile, str):
        profile = next(
            (item for item in profiles if item.name == profile),
            None,
        )
        if profile is None:
            raise ValueError("Selected treatment profile was not found")
    if not isinstance(profile, TreatmentProfile):
        raise TypeError("profile must be a TreatmentProfile or profile name")

    times, positions = prepare_signal(times, positions)
    noise = estimate_noise_profile(
        times,
        positions,
        window_seconds=base_config.noise_window_seconds,
        quiet_fraction=base_config.noise_quiet_fraction,
    )
    cycles = extract_cycles(
        times,
        positions,
        noise,
        smoothing_seconds=base_config.cycle_smoothing_seconds,
        minimum_peak_distance_seconds=base_config.minimum_peak_distance_seconds,
    )

    resolved_profile, inferred_category = resolve_treatment_profile(
        profile,
        noise,
        cycles,
        profiles,
    )
    profile_config = replace(
        base_config,
        **config_values_for_profile(resolved_profile),
    )
    candidate_regions = detect_candidate_regions(
        times,
        positions,
        cycles,
        noise,
        window_seconds=max(
            profile_config.noise_window_seconds,
            profile_config.quiet_window_seconds,
        ),
        merge_gap_seconds=profile_config.minimum_rest_seconds,
        minimum_cycle_snr=profile_config.amplitude_noise_multiple * 0.5,
    )
    quiet_intervals = find_quiet_intervals(
        times,
        positions,
        noise,
        hysteresis_multiple=profile_config.quiet_hysteresis_multiple,
        window_seconds=profile_config.quiet_window_seconds,
    )

    detected_events = []
    candidate_diagnostics = []
    for region_index, region in enumerate(candidate_regions, start=1):
        region_cycles = [
            cycle
            for cycle in cycles
            if cycle.end_s > region.start_s and cycle.start_s < region.end_s
        ]
        region_quiet_intervals = [
            interval
            for interval in quiet_intervals
            if region.start_s <= interval.start_s < region.end_s
        ]
        machine = PhaseDetectionStateMachine(noise, profile_config)
        evidence = [
            (cycle.end_s, 0, cycle) for cycle in region_cycles
        ] + [
            (interval.end_s, 1, interval) for interval in region_quiet_intervals
        ]
        for _confirmed_at, evidence_order, item in sorted(
            evidence, key=lambda entry: (entry[0], entry[1])
        ):
            if evidence_order == 0:
                machine.process_cycle(item)
            else:
                machine.process_quiet_interval(item)

        terminal_quiet_interval = (
            max(region_quiet_intervals, key=lambda interval: interval.end_s)
            if region_quiet_intervals
            else None
        )
        region_events = machine.finish(
            region.end_s,
            terminal_quiet_interval=terminal_quiet_interval,
        )
        detected_events.extend(region_events)
        for candidate in machine.candidate_diagnostics:
            candidate["candidate_region"] = region_index
            candidate_diagnostics.append(candidate)
            _logger.debug(
                "Phase candidate evaluated",
                extra={"phase_candidate": candidate},
            )
        _logger.debug(
            "Whole-waveform candidate region identified",
            extra={
                "candidate_region": {
                    "region": region_index,
                    **region.to_dict(),
                }
            },
        )

    for event, next_event in zip(detected_events, detected_events[1:]):
        if (
            event.phase3_start_s is not None
            and next_event.start_s > event.phase3_start_s
        ):
            event.end_s = next_event.start_s

    ranges = []
    event_summaries = []
    for index, event in enumerate(detected_events, start=1):
        if (
            event.phase2_start_s is None
            or event.phase3_start_s is None
            or event.end_s is None
        ):
            continue
        boundaries = (
            ("Phase 1", event.start_s, event.phase2_start_s),
            ("Phase 2", event.phase2_start_s, event.phase3_start_s),
            ("Phase 3", event.phase3_start_s, event.end_s),
        )
        if not all(end > start for _label, start, end in boundaries):
            continue
        for label, start, end in boundaries:
            ranges.append(
                {
                    "label": label,
                    "start_s": float(start),
                    "end_s": float(end),
                    "expected_cycles": 0,
                    "smooth": False,
                }
            )
        phase2_frequencies = [
            cycle.frequency_hz for cycle in event.phase2_cycles
        ]
        phase2_amplitudes = [cycle.amplitude for cycle in event.phase2_cycles]
        phase1_frequencies = [
            cycle.frequency_hz for cycle in event.phase1_cycle_features
        ]
        phase1_amplitudes = [
            cycle.amplitude for cycle in event.phase1_cycle_features
        ]
        phase3_cycles = [
            cycle
            for cycle in cycles
            if event.phase3_start_s <= cycle.start_s < event.end_s
            and cycle.amplitude
            >= noise.peak_to_peak * profile_config.amplitude_noise_multiple
        ]
        phase2_frequency_cv = (
            float(np.std(phase2_frequencies) / np.mean(phase2_frequencies))
            if phase2_frequencies and np.mean(phase2_frequencies) > 0
            else None
        )
        phase2_amplitude_cv = (
            float(np.std(phase2_amplitudes) / np.mean(phase2_amplitudes))
            if phase2_amplitudes and np.mean(phase2_amplitudes) > 0
            else None
        )
        validation_confidence = float(
            np.mean(
                [
                    max(
                        0.0,
                        1.0
                        - event.candidate_frequency_cv
                        / profile_config.maximum_frequency_cv,
                    ),
                    max(
                        0.0,
                        1.0
                        - event.candidate_amplitude_cv
                        / profile_config.maximum_amplitude_cv,
                    ),
                    event.candidate_periodicity_strength,
                ]
            )
        )
        event_summaries.append(
            {
                "event": index,
                "vibration_start_s": float(event.start_s),
                "phase2_start_s": float(event.phase2_start_s),
                "phase3_start_s": float(event.phase3_start_s),
                "vibration_end_s": float(event.phase3_start_s),
                "phase1_frequency_hz": (
                    float(np.mean(phase1_frequencies))
                    if phase1_frequencies
                    else float(event.phase1_frequency_hz)
                ),
                "phase1_amplitude": (
                    float(np.mean(phase1_amplitudes))
                    if phase1_amplitudes
                    else float(event.phase1_amplitude)
                ),
                "phase1_baseline_frequency_hz": float(
                    event.phase1_frequency_hz
                ),
                "phase1_cycles": int(event.phase1_cycles),
                "phase2_cycles": len(event.phase2_cycles),
                "phase2_frequency_hz": (
                    float(np.mean(phase2_frequencies))
                    if phase2_frequencies
                    else None
                ),
                "phase2_amplitude": (
                    float(np.mean(phase2_amplitudes))
                    if phase2_amplitudes
                    else None
                ),
                "phase2_frequency_cv": phase2_frequency_cv,
                "phase2_amplitude_cv": phase2_amplitude_cv,
                "phase2_periodicity_strength": (
                    float(
                        np.mean(
                            [
                                cycle.periodicity_strength
                                for cycle in event.phase2_cycles
                            ]
                        )
                    )
                    if event.phase2_cycles
                    else None
                ),
                "phase3_cycles": len(phase3_cycles),
                "candidate_frequency_cv": float(event.candidate_frequency_cv),
                "candidate_amplitude_cv": float(event.candidate_amplitude_cv),
                "candidate_periodicity_strength": float(
                    event.candidate_periodicity_strength
                ),
                "validation_confidence": validation_confidence,
                "phase3_end_s": float(event.end_s),
                "total_duration_s": float(event.phase3_start_s - event.start_s),
            }
        )

    selected_profile_is_auto = profile.name == "Auto Detect"
    profile_metadata = {
        "detection_mode": (
            "Automatic amplitude classification"
            if selected_profile_is_auto
            else "User-selected treatment profile"
        ),
        "selected_profile": profile.name,
        "resolved_profile": resolved_profile.name,
        "expected_amplitude_category": inferred_category,
        "parameters_used": asdict(profile_config),
        "detection_confidence": (
            float(np.mean([event["validation_confidence"] for event in event_summaries]))
            if event_summaries
            else None
        ),
    }

    return PhaseDetectionResult(
        phase_ranges=ranges,
        events=event_summaries,
        candidate_regions=[region.to_dict() for region in candidate_regions],
        noise_profile=asdict(noise),
        cycle_count=len(cycles),
        diagnostics=candidate_diagnostics,
        profile_metadata=profile_metadata,
    )
