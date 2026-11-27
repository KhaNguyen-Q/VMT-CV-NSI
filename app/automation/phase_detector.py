"""Public automatic phase-detection entry point."""

from dataclasses import asdict, dataclass, replace
import heapq
import logging
import time

import numpy as np

from .detection_profiles import (
    TreatmentProfile,
    config_values_for_profile,
    default_treatment_profiles,
    resolve_treatment_profile,
)
from .event_regions import detect_candidate_regions
from .phase_state_machine import (
    DetectionState,
    PhaseDetectionConfig,
    PhaseDetectionStateMachine,
)
from .signals_phase_detector import detect_signals_events
from .signal_features import (
    estimate_noise_profile,
    extract_cycles,
    find_energy_decay_interval,
    find_quiet_intervals,
    prepare_signal,
)
from .tagged_cycles import tagged_cycle_count

_logger = logging.getLogger(__name__)
_AUTO_REFINEMENT_MAX_ATTEMPTS = 320
_AUTO_REFINEMENT_MAX_SECONDS = 10.0
_AUTO_REFINEMENT_CORE_ADJUSTMENTS = 24
_TARGET_PHASE1_TAGGED_CYCLES = 11


@dataclass(frozen=True)
class PhaseDetectionResult:
    phase_ranges: list[dict]
    events: list[dict]
    candidate_regions: list[dict]
    noise_profile: dict
    cycle_count: int
    diagnostics: list[dict]
    profile_metadata: dict


def detect_phase_ranges(
    times,
    positions,
    config=None,
    profile=None,
    profiles=None,
    method="FSM",
):
    """Detect complete Phase 1/2/3 ranges in a baseline displacement signal."""
    if method not in ("FSM", "Signals"):
        raise ValueError("Detection method must be 'FSM' or 'Signals'")
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
        if region_index == 1 and region.start_s > times[0]:
            region_cycles = [
                cycle
                for cycle in cycles
                if region.start_s <= cycle.start_s < region.end_s
            ]
        else:
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
        if method == "Signals":
            region_events, region_diagnostics = detect_signals_events(
                times,
                positions,
                region,
                region_cycles,
                noise,
                profile_config,
            )
            detected_events.extend(region_events)
        else:
            machine = PhaseDetectionStateMachine(noise, profile_config)
            evidence = [
                (cycle.end_s, 0, index, "cycle", cycle)
                for index, cycle in enumerate(region_cycles)
            ] + [
                (
                    interval.end_s,
                    1,
                    len(region_cycles) + index,
                    "quiet",
                    interval,
                )
                for index, interval in enumerate(region_quiet_intervals)
            ]
            heapq.heapify(evidence)
            next_evidence_index = len(evidence)
            energy_quiet_intervals = []
            while evidence:
                confirmed_at, _order, _evidence_index, evidence_type, item = (
                    heapq.heappop(evidence)
                )
                if evidence_type == "cycle":
                    previous_state = machine.state
                    machine.process_cycle(item)
                    if (
                        previous_state is DetectionState.PHASE_1
                        and machine.state is DetectionState.PHASE_2
                    ):
                        event = machine.current_event
                        if event is None or event.phase2_start_s is None:
                            continue
                        minimum_rest = max(
                            profile_config.phase3_rest_minimum_seconds,
                            2.0 / event.phase1_frequency_hz,
                        )
                        energy_interval = find_energy_decay_interval(
                            times,
                            positions,
                            noise,
                            event.phase2_start_s,
                            minimum_duration=minimum_rest,
                            terminal_rest_minimum_seconds=(
                                profile_config.terminal_rest_minimum_seconds
                            ),
                        )
                        if (
                            energy_interval is not None
                            and energy_interval.start_s > event.phase2_start_s
                            and energy_interval.end_s > confirmed_at
                        ):
                            energy_quiet_intervals.append(energy_interval)
                            heapq.heappush(
                                evidence,
                                (
                                    energy_interval.end_s,
                                    2,
                                    next_evidence_index,
                                    "quiet",
                                    energy_interval,
                                ),
                            )
                            next_evidence_index += 1
                else:
                    machine.process_quiet_interval(item)

            terminal_quiet_interval = (
                max(
                    region_quiet_intervals + energy_quiet_intervals,
                    key=lambda interval: interval.end_s,
                )
                if region_quiet_intervals or energy_quiet_intervals
                else None
            )
            region_events = machine.finish(
                region.end_s,
                terminal_quiet_interval=terminal_quiet_interval,
            )
            detected_events.extend(region_events)
            region_diagnostics = machine.candidate_diagnostics

        for candidate in region_diagnostics:
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
        "detection_method": method,
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


def detect_phase_ranges_with_auto_refinement(
    times,
    positions,
    extrema,
    config=None,
    profile=None,
    profiles=None,
    method="FSM",
):
    """Refine Auto Detect boundaries against the tagged Phase 1 cycle count."""
    profiles = list(profiles or default_treatment_profiles())
    selected_profile_name = (
        "Auto Detect"
        if profile is None
        else profile
        if isinstance(profile, str)
        else profile.name
    )
    refinement_deadline = (
        time.monotonic() + _AUTO_REFINEMENT_MAX_SECONDS
        if selected_profile_name == "Auto Detect"
        else None
    )
    initial = detect_phase_ranges(
        times,
        positions,
        config=config,
        profile=profile,
        profiles=profiles,
        method=method,
    )
    if selected_profile_name != "Auto Detect":
        return initial

    if not initial.phase_ranges:
        return _with_refinement_metadata(
            initial,
            {
                "status": "no_phase1_ranges",
                "attempted_candidates": 0,
                "tagged_phase1_counts": [],
                "exact_11_count": 0,
                "phase1_count": 0,
                "all_phase1_have_11": False,
            },
        )
    if extrema is None or len(extrema) == 0:
        return _with_refinement_metadata(
            initial,
            {
                "status": "tagged_extrema_unavailable",
                "attempted_candidates": 0,
                "tagged_phase1_counts": [],
                "exact_11_count": 0,
                "phase1_count": 0,
                "all_phase1_have_11": False,
            },
        )

    baseline_profile = _resolved_profile(initial, profiles)
    if method == "FSM":
        initial = _refine_phase1_boundaries(
            initial,
            extrema,
            baseline_profile,
        )
    baseline_counts = _phase1_tagged_counts(initial, extrema)
    if not baseline_counts:
        return _with_refinement_metadata(
            initial,
            _refinement_summary([], 0, "no_phase1_ranges"),
        )
    best = initial
    best_counts = baseline_counts
    best_profile = baseline_profile
    best_score = _refinement_score(initial, baseline_counts, baseline_profile)
    attempts = 0
    if all(count == _TARGET_PHASE1_TAGGED_CYCLES for count in baseline_counts):
        return _with_refinement_metadata(
            initial,
            _refinement_summary(baseline_counts, attempts, "target_met"),
        )

    alternative_profiles = [
        item
        for item in profiles
        if item.name not in ("Auto Detect", baseline_profile.name)
    ]
    profile_candidates = list(alternative_profiles)
    baseline_adjustments = _auto_refinement_candidates(baseline_profile)
    profile_candidates.extend(
        replace(baseline_profile, **overrides)
        for overrides in baseline_adjustments[
            :_AUTO_REFINEMENT_CORE_ADJUSTMENTS
        ]
    )
    alternative_adjustments = [
        _auto_refinement_candidates(item) for item in alternative_profiles
    ]
    for base_profile, adjustments in zip(
        alternative_profiles, alternative_adjustments
    ):
        profile_candidates.extend(
            replace(base_profile, **overrides)
            for overrides in adjustments[:_AUTO_REFINEMENT_CORE_ADJUSTMENTS]
        )

    refinement_bases = [baseline_profile, *alternative_profiles]
    adjustments_by_profile = [
        baseline_adjustments,
        *alternative_adjustments,
    ]
    for adjustment_index in range(
        max((len(adjustments) for adjustments in adjustments_by_profile), default=0)
    ):
        for base_profile, adjustments in zip(
            refinement_bases, adjustments_by_profile
        ):
            if adjustment_index < _AUTO_REFINEMENT_CORE_ADJUSTMENTS:
                continue
            if adjustment_index < len(adjustments):
                profile_candidates.append(
                    replace(base_profile, **adjustments[adjustment_index])
                )

    inferred_category = initial.profile_metadata["expected_amplitude_category"]
    time_limit_reached = False
    for candidate_profile in profile_candidates[:_AUTO_REFINEMENT_MAX_ATTEMPTS]:
        if time.monotonic() >= refinement_deadline:
            time_limit_reached = True
            break
        candidate_profile = replace(
            candidate_profile,
            expected_amplitude_category=inferred_category,
        )
        candidate_profile.validate()
        candidate_profiles = [
            item
            for item in profiles
            if item.name != candidate_profile.name
            and item.expected_amplitude_category.casefold()
            != inferred_category.casefold()
        ]
        candidate_profiles.insert(0, candidate_profile)

        candidate = detect_phase_ranges(
            times,
            positions,
            config=config,
            profile=profile,
            profiles=candidate_profiles,
            method=method,
        )
        if method == "FSM":
            candidate = _refine_phase1_boundaries(
                candidate,
                extrema,
                candidate_profile,
            )
        attempts += 1
        candidate_counts = _phase1_tagged_counts(candidate, extrema)
        if (
            len(candidate.events) != len(initial.events)
            or len(candidate_counts) != len(baseline_counts)
        ):
            continue

        candidate_score = _refinement_score(
            candidate,
            candidate_counts,
            candidate_profile,
            reference_profile=baseline_profile,
        )
        if candidate_score > best_score:
            best = candidate
            best_counts = candidate_counts
            best_profile = candidate_profile
            best_score = candidate_score
        if all(
            count == _TARGET_PHASE1_TAGGED_CYCLES
            for count in candidate_counts
        ):
            best = candidate
            best_counts = candidate_counts
            best_profile = candidate_profile
            break

    time_limit_reached = (
        time_limit_reached or time.monotonic() >= refinement_deadline
    )
    status = (
        "target_met"
        if all(
            count == _TARGET_PHASE1_TAGGED_CYCLES for count in best_counts
        )
        else "improved"
        if best is not initial
        else "best_effort"
    )
    refinement = _refinement_summary(best_counts, attempts, status)
    refinement["time_limit_reached"] = time_limit_reached
    refinement["parameters_used"] = {
        "min_cycles": best_profile.min_cycles,
        "periodicity_threshold": best_profile.periodicity_threshold,
        "frequency_increase_ratio": best_profile.frequency_increase_ratio,
        "sustained_frequency_cycles": best_profile.sustained_frequency_cycles,
    }
    return _with_refinement_metadata(best, refinement)


def _phase1_tagged_counts(result, extrema):
    return [
        tagged_cycle_count(extrema, phase["start_s"], phase["end_s"])
        for phase in result.phase_ranges
        if phase["label"].casefold() == "phase 1"
    ]


def _refine_phase1_boundaries(result, extrema, profile):
    phase_ranges = [dict(phase) for phase in result.phase_ranges]
    events = [dict(event) for event in result.events]
    refinements = []
    phase1_event_index = 0
    adjusted_count = 0

    for range_index, phase1 in enumerate(phase_ranges):
        if phase1["label"].casefold() != "phase 1":
            continue

        event_index = phase1_event_index
        phase1_event_index += 1
        diagnostic = {
            "event": event_index + 1,
            "original_start_s": phase1["start_s"],
            "original_end_s": phase1["end_s"],
            "start_s": phase1["start_s"],
            "end_s": phase1["end_s"],
            "tagged_cycles": tagged_cycle_count(
                extrema,
                phase1["start_s"],
                phase1["end_s"],
            ),
            "adjusted": False,
        }
        refinements.append(diagnostic)

        phase2_index = range_index + 1
        if diagnostic["tagged_cycles"] == _TARGET_PHASE1_TAGGED_CYCLES:
            diagnostic["reason"] = "already_at_target"
            continue
        if (
            event_index >= len(events)
            or phase2_index >= len(phase_ranges)
            or phase_ranges[phase2_index]["label"].casefold() != "phase 2"
        ):
            diagnostic["reason"] = "phase_2_boundary_unavailable"
            continue

        event = events[event_index]
        reference_frequency = event.get(
            "phase1_baseline_frequency_hz",
            event.get("phase1_frequency_hz"),
        )
        if reference_frequency is None or reference_frequency <= 0:
            diagnostic["reason"] = "phase_1_frequency_unavailable"
            continue
        period = 1.0 / reference_frequency
        start_limit = (phase1["start_s"] - period, phase1["start_s"] + period)
        end_limit = (
            phase1["end_s"] - 0.5 * period,
            phase1["end_s"] + 0.15 * period,
        )
        candidates = sorted(
            (
                row
                for row in extrema
                if row["label"] in ("top", "bottom")
            ),
            key=lambda row: row["time_s"],
        )
        candidate_times = np.asarray(
            [row["time_s"] for row in candidates],
            dtype=float,
        )
        start_candidates = [
            row
            for row in candidates
            if start_limit[0] <= row["time_s"] <= start_limit[1]
        ]
        end_candidates = [
            row
            for row in candidates
            if end_limit[0] <= row["time_s"] <= end_limit[1]
        ]
        best_candidate = None

        for start_row in start_candidates:
            start_s = start_row["time_s"]
            for end_row in end_candidates:
                end_s = end_row["time_s"]
                if end_s <= start_s or end_s >= phase_ranges[phase2_index]["end_s"]:
                    continue
                previous_phase3 = (
                    phase_ranges[range_index - 1]
                    if range_index > 0
                    and phase_ranges[range_index - 1]["label"].casefold()
                    == "phase 3"
                    else None
                )
                if (
                    previous_phase3 is not None
                    and start_s <= previous_phase3["start_s"]
                ):
                    continue
                left_index = int(
                    np.searchsorted(candidate_times, start_s, side="left")
                )
                right_index = int(
                    np.searchsorted(candidate_times, end_s, side="right")
                )
                tagged_rows = candidates[left_index:right_index]
                top_count = sum(row["label"] == "top" for row in tagged_rows)
                bottom_count = sum(row["label"] == "bottom" for row in tagged_rows)
                if (
                    min(top_count, bottom_count) != _TARGET_PHASE1_TAGGED_CYCLES
                    or top_count != bottom_count
                    or len(tagged_rows) != _TARGET_PHASE1_TAGGED_CYCLES * 2
                    or any("position_px" not in row for row in tagged_rows)
                    or any(
                        left["label"] == right["label"]
                        for left, right in zip(tagged_rows, tagged_rows[1:])
                    )
                ):
                    continue

                top_times = np.asarray(
                    [row["time_s"] for row in tagged_rows if row["label"] == "top"],
                    dtype=float,
                )
                bottom_times = np.asarray(
                    [
                        row["time_s"]
                        for row in tagged_rows
                        if row["label"] == "bottom"
                    ],
                    dtype=float,
                )
                periods = np.concatenate(
                    (np.diff(top_times), np.diff(bottom_times))
                )
                if periods.size == 0 or np.any(periods <= 0):
                    continue
                frequencies = 1.0 / periods
                frequency_cv = float(
                    np.std(frequencies) / np.mean(frequencies)
                )
                mean_frequency = float(np.median(frequencies))
                if (
                    frequency_cv > profile.frequency_tolerance
                    or abs(mean_frequency - reference_frequency)
                    / reference_frequency
                    > profile.frequency_tolerance
                ):
                    continue

                amplitudes = np.asarray(
                    [
                        abs(
                            right["position_px"] - left["position_px"]
                        )
                        for left, right in zip(tagged_rows, tagged_rows[1:])
                    ],
                    dtype=float,
                )
                mean_amplitude = float(np.mean(amplitudes))
                amplitude_cv = (
                    float(np.std(amplitudes) / mean_amplitude)
                    if mean_amplitude > 0
                    else float("inf")
                )
                if amplitude_cv > profile.amplitude_tolerance:
                    continue

                score = (
                    abs(start_s - phase1["start_s"]) / period
                    + abs(end_s - phase1["end_s"]) / period
                    + frequency_cv
                    + amplitude_cv
                )
                if best_candidate is None or score < best_candidate[0]:
                    best_candidate = (
                        score,
                        start_s,
                        end_s,
                        frequency_cv,
                        amplitude_cv,
                    )

        if best_candidate is None:
            diagnostic["reason"] = "no_stable_11_cycle_window"
            continue

        (
            _score,
            refined_start_s,
            refined_end_s,
            frequency_cv,
            amplitude_cv,
        ) = best_candidate
        phase1["start_s"] = float(refined_start_s)
        phase1["end_s"] = float(refined_end_s)
        phase_ranges[phase2_index]["start_s"] = float(refined_end_s)
        if previous_phase3 is not None:
            previous_phase3["end_s"] = float(refined_start_s)
            events[event_index - 1]["phase3_end_s"] = float(refined_start_s)
        event["vibration_start_s"] = float(refined_start_s)
        event["phase2_start_s"] = float(refined_end_s)
        event["vibration_end_s"] = float(refined_end_s)
        event["total_duration_s"] = float(refined_end_s - refined_start_s)
        diagnostic.update(
            {
                "start_s": float(refined_start_s),
                "end_s": float(refined_end_s),
                "tagged_cycles": _TARGET_PHASE1_TAGGED_CYCLES,
                "frequency_cv": frequency_cv,
                "amplitude_cv": amplitude_cv,
                "adjusted": True,
            }
        )
        diagnostic.pop("reason", None)
        adjusted_count += 1

    if not refinements:
        return result

    metadata = dict(result.profile_metadata)
    metadata["phase1_boundary_refinement"] = {
        "adjusted_event_count": adjusted_count,
        "events": refinements,
    }
    return replace(
        result,
        phase_ranges=phase_ranges,
        events=events,
        profile_metadata=metadata,
    )


def _resolved_profile(result, profiles):
    resolved_name = result.profile_metadata["resolved_profile"]
    profile = next(
        (item for item in profiles if item.name == resolved_name),
        None,
    )
    if profile is not None:
        return profile
    return next(
        item
        for item in default_treatment_profiles()
        if item.name == resolved_name
    )


def _auto_refinement_candidates(profile):
    candidates = []
    seen = set()
    frequency_deltas = (
        -0.025,
        0.025,
        -0.05,
        0.05,
        -0.075,
        0.075,
        -0.1,
        0.1,
        -0.15,
        0.15,
        -0.2,
        0.2,
    )
    cycle_deltas = (-1, 1, -2, 2, -3, 3, -4, 4)
    min_cycle_deltas = (-2, -1, 1, 2, -3, 3, -4, 4)
    periodicity_deltas = (
        -0.1,
        0.1,
        -0.2,
        0.2,
        -0.05,
        0.05,
        -0.15,
        0.15,
        -0.3,
        0.3,
        -0.4,
        0.4,
    )
    adjustments = [
        {"frequency_increase_ratio": profile.frequency_increase_ratio + delta}
        for delta in frequency_deltas[:4]
    ]
    adjustments.extend(
        {"sustained_frequency_cycles": profile.sustained_frequency_cycles + delta}
        for delta in cycle_deltas[:4]
    )
    adjustments.extend(
        {
            "frequency_increase_ratio": profile.frequency_increase_ratio + ratio_delta,
            "sustained_frequency_cycles": (
                profile.sustained_frequency_cycles + cycle_delta
            ),
        }
        for ratio_delta in (-0.05, 0.05)
        for cycle_delta in (-1, 1)
    )
    adjustments.extend(
        {"min_cycles": profile.min_cycles + delta}
        for delta in min_cycle_deltas[:4]
    )
    adjustments.extend(
        {"periodicity_threshold": profile.periodicity_threshold + delta}
        for delta in periodicity_deltas[:4]
    )
    adjustments.extend(
        {
            "min_cycles": profile.min_cycles + cycle_delta,
            "periodicity_threshold": profile.periodicity_threshold + threshold_delta,
        }
        for cycle_delta in (-1, 1)
        for threshold_delta in (-0.1, 0.1)
    )
    adjustments.extend(
        {"frequency_increase_ratio": profile.frequency_increase_ratio + delta}
        for delta in frequency_deltas[4:]
    )
    adjustments.extend(
        {"sustained_frequency_cycles": profile.sustained_frequency_cycles + delta}
        for delta in cycle_deltas[4:]
    )
    adjustments.extend(
        {
            "frequency_increase_ratio": profile.frequency_increase_ratio + ratio_delta,
            "sustained_frequency_cycles": (
                profile.sustained_frequency_cycles + cycle_delta
            ),
        }
        for ratio_delta in frequency_deltas
        for cycle_delta in cycle_deltas
    )
    adjustments.extend(
        {"min_cycles": profile.min_cycles + delta}
        for delta in min_cycle_deltas[4:]
    )
    adjustments.extend(
        {"periodicity_threshold": profile.periodicity_threshold + delta}
        for delta in periodicity_deltas[4:]
    )
    adjustments.extend(
        {
            "min_cycles": profile.min_cycles + cycle_delta,
            "periodicity_threshold": profile.periodicity_threshold + threshold_delta,
        }
        for cycle_delta in min_cycle_deltas
        for threshold_delta in periodicity_deltas
    )
    for candidate in adjustments[:_AUTO_REFINEMENT_MAX_ATTEMPTS]:
        values = (
            candidate.get(
                "frequency_increase_ratio", profile.frequency_increase_ratio
            ),
            candidate.get(
                "sustained_frequency_cycles",
                profile.sustained_frequency_cycles,
            ),
            candidate.get("min_cycles", profile.min_cycles),
            candidate.get(
                "periodicity_threshold", profile.periodicity_threshold
            ),
        )
        if values in seen or values == (
            profile.frequency_increase_ratio,
            profile.sustained_frequency_cycles,
            profile.min_cycles,
            profile.periodicity_threshold,
        ):
            continue
        seen.add(values)
        if (
            values[0] <= 1
            or values[1] < 1
            or values[2] < 2
            or not 0 <= values[3] <= 1
        ):
            continue
        candidates.append(candidate)
    return candidates


def _refinement_score(result, counts, profile, reference_profile=None):
    exact_count = sum(count == _TARGET_PHASE1_TAGGED_CYCLES for count in counts)
    total_distance = sum(
        abs(count - _TARGET_PHASE1_TAGGED_CYCLES) for count in counts
    )
    confidence = result.profile_metadata.get("detection_confidence") or 0.0
    reference_profile = reference_profile or profile
    parameter_changes = sum(
        (
            profile.frequency_increase_ratio
            != reference_profile.frequency_increase_ratio,
            profile.sustained_frequency_cycles
            != reference_profile.sustained_frequency_cycles,
            profile.min_cycles != reference_profile.min_cycles,
            profile.periodicity_threshold
            != reference_profile.periodicity_threshold,
        )
    )
    parameter_distance = (
        abs(
            profile.frequency_increase_ratio
            - reference_profile.frequency_increase_ratio
        )
        + abs(
            profile.sustained_frequency_cycles
            - reference_profile.sustained_frequency_cycles
        )
        + abs(profile.min_cycles - reference_profile.min_cycles)
        + abs(
            profile.periodicity_threshold
            - reference_profile.periodicity_threshold
        )
    )
    return (
        exact_count,
        -total_distance,
        confidence,
        -parameter_changes,
        -parameter_distance,
    )


def _refinement_summary(counts, attempts, status):
    exact_count = sum(count == _TARGET_PHASE1_TAGGED_CYCLES for count in counts)
    return {
        "status": status,
        "attempted_candidates": attempts,
        "tagged_phase1_counts": list(counts),
        "exact_11_count": exact_count,
        "phase1_count": len(counts),
        "all_phase1_have_11": bool(counts)
        and exact_count == len(counts),
    }


def _with_refinement_metadata(result, refinement):
    metadata = dict(result.profile_metadata)
    metadata["auto_refinement"] = refinement
    return replace(result, profile_metadata=metadata)
