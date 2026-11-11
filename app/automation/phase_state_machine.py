"""Deterministic cycle-driven state machine for vibration phase classification."""

from dataclasses import dataclass, field
from enum import Enum, auto

import numpy as np

from .signal_features import CycleFeature, NoiseProfile, QuietInterval


class DetectionState(Enum):
    IDLE = auto()
    CANDIDATE_VIBRATION = auto()
    PHASE_1 = auto()
    PHASE_2 = auto()
    PHASE_3 = auto()


@dataclass(frozen=True)
class PhaseDetectionConfig:
    minimum_candidate_cycles: int = 5
    maximum_frequency_cv: float = 0.20
    maximum_amplitude_cv: float = 0.55
    minimum_periodicity: float = 0.60
    amplitude_noise_multiple: float = 2.0
    frequency_increase_ratio: float = 1.15
    sustained_frequency_cycles: int = 3
    quiet_hysteresis_multiple: float = 1.5
    minimum_rest_seconds: float = 0.5
    phase3_rest_minimum_seconds: float = 0.4
    noise_window_seconds: float = 0.5
    noise_quiet_fraction: float = 0.2
    cycle_smoothing_seconds: float = 0.02
    minimum_peak_distance_seconds: float = 0.02
    quiet_window_seconds: float = 0.25
    terminal_rest_minimum_seconds: float = 0.25

    def __post_init__(self):
        if self.minimum_candidate_cycles < 2:
            raise ValueError("minimum_candidate_cycles must be at least two")
        if self.sustained_frequency_cycles < 1:
            raise ValueError("sustained_frequency_cycles must be positive")
        if self.maximum_frequency_cv <= 0 or self.maximum_amplitude_cv <= 0:
            raise ValueError("cycle stability thresholds must be positive")
        if self.minimum_periodicity < 0 or self.minimum_periodicity > 1:
            raise ValueError("minimum_periodicity must be between zero and one")
        if self.amplitude_noise_multiple <= 0:
            raise ValueError("amplitude_noise_multiple must be positive")
        if self.frequency_increase_ratio <= 1:
            raise ValueError("frequency_increase_ratio must be greater than one")
        if self.quiet_hysteresis_multiple <= 1:
            raise ValueError("quiet_hysteresis_multiple must be greater than one")
        if (
            self.minimum_rest_seconds <= 0
            or self.phase3_rest_minimum_seconds <= 0
            or self.noise_window_seconds <= 0
            or self.cycle_smoothing_seconds <= 0
            or self.minimum_peak_distance_seconds <= 0
            or self.quiet_window_seconds <= 0
            or self.terminal_rest_minimum_seconds <= 0
        ):
            raise ValueError("configured durations must be positive")
        if not 0 < self.noise_quiet_fraction <= 1:
            raise ValueError("noise_quiet_fraction must be in (0, 1]")


@dataclass
class VibrationEvent:
    start_s: float
    phase2_start_s: float | None = None
    phase3_start_s: float | None = None
    end_s: float | None = None
    phase1_frequency_hz: float = 0.0
    phase1_amplitude: float = 0.0
    phase1_cycles: int = 0
    phase1_cycle_features: list[CycleFeature] = field(default_factory=list)
    phase2_cycles: list[CycleFeature] = field(default_factory=list)
    candidate_frequency_cv: float = 0.0
    candidate_amplitude_cv: float = 0.0
    candidate_periodicity_strength: float = 0.0


def _coefficient_of_variation(values):
    mean = float(np.mean(values))
    if mean <= np.finfo(float).eps:
        return float("inf")
    return float(np.std(values) / mean)


class PhaseDetectionStateMachine:
    """Classify cycle evidence and return completed vibration events."""

    def __init__(self, noise_profile: NoiseProfile, config=None):
        self.config = config or PhaseDetectionConfig()
        self.noise_profile = noise_profile
        self.state = DetectionState.IDLE
        self.events = []
        self.current_event = None
        self._candidate_cycles = []
        self._frequency_rise_cycles = []
        self.candidate_diagnostics = []

    def _cycle_is_candidate(self, cycle):
        return (
            cycle.amplitude
            >= self.noise_profile.peak_to_peak
            * self.config.amplitude_noise_multiple
            * 0.5
        )

    def _candidate_qualifies(self, cycles):
        sample = cycles[-self.config.minimum_candidate_cycles :]
        metrics = self._candidate_metrics(sample)
        checks = {
            "minimum_cycles": len(cycles) >= self.config.minimum_candidate_cycles,
            "frequency_stability": (
                metrics["average_frequency_hz"] > 0
                and metrics["frequency_stability"]
                <= self.config.maximum_frequency_cv
            ),
            "amplitude_stability": (
                metrics["amplitude_stability"]
                <= self.config.maximum_amplitude_cv
            ),
            "minimum_snr": (
                metrics["minimum_cycle_snr"]
                >= self.config.amplitude_noise_multiple
            ),
            "periodicity": (
                metrics["periodicity_strength"]
                >= self.config.minimum_periodicity
            ),
        }
        reasons = []
        if not checks["minimum_cycles"]:
            reasons.append("Insufficient cycle count")
        if not checks["frequency_stability"]:
            reasons.append("Frequency instability")
        if not checks["amplitude_stability"]:
            reasons.append("Amplitude instability")
        if not checks["minimum_snr"]:
            reasons.append("Below minimum SNR threshold")
        if not checks["periodicity"]:
            reasons.append("Failed periodicity validation")

        self.candidate_diagnostics.append(
            {
                "candidate_cycles": len(cycles),
                **metrics,
                "qualification_checks": checks,
                "passed": not reasons,
                "rejection_reasons": reasons,
            }
        )
        return len(cycles) >= self.config.minimum_candidate_cycles and not reasons

    def _candidate_metrics(self, cycles):
        frequencies = [cycle.frequency_hz for cycle in cycles]
        amplitudes = [cycle.amplitude for cycle in cycles]
        return {
            "average_frequency_hz": (
                float(np.mean(frequencies)) if frequencies else 0.0
            ),
            "frequency_stability": (
                _coefficient_of_variation(frequencies) if frequencies else float("inf")
            ),
            "average_amplitude": float(np.mean(amplitudes)) if amplitudes else 0.0,
            "amplitude_stability": (
                _coefficient_of_variation(amplitudes) if amplitudes else float("inf")
            ),
            "rms_energy": (
                float(np.mean([cycle.rms for cycle in cycles]) * np.sqrt(2.0))
                if cycles
                else 0.0
            ),
            "signal_to_noise_ratio": (
                float(np.mean(amplitudes) / self.noise_profile.peak_to_peak)
                if amplitudes and self.noise_profile.peak_to_peak > 0
                else float("inf")
            ),
            "minimum_cycle_snr": (
                float(
                    min(amplitudes)
                    / self.noise_profile.peak_to_peak
                )
                if amplitudes and self.noise_profile.peak_to_peak > 0
                else float("inf")
            ),
            "periodicity_strength": (
                float(np.mean([cycle.periodicity_strength for cycle in cycles]))
                if cycles
                else 0.0
            ),
        }

    def _begin_event(self, cycles):
        sample = cycles[-self.config.minimum_candidate_cycles :]
        event = VibrationEvent(
            start_s=sample[0].start_s,
            phase1_frequency_hz=float(np.median([cycle.frequency_hz for cycle in sample])),
            phase1_amplitude=float(np.median([cycle.amplitude for cycle in sample])),
            phase1_cycles=len(sample),
            phase1_cycle_features=list(sample),
            candidate_frequency_cv=_coefficient_of_variation(
                [cycle.frequency_hz for cycle in sample]
            ),
            candidate_amplitude_cv=_coefficient_of_variation(
                [cycle.amplitude for cycle in sample]
            ),
            candidate_periodicity_strength=float(
                np.mean([cycle.periodicity_strength for cycle in sample])
            ),
        )
        if (
            self.current_event is not None
            and self.current_event.phase3_start_s is not None
            and self.current_event.end_s is None
        ):
            self.current_event.end_s = event.start_s
            self.events.append(self.current_event)
        self.current_event = event
        self.state = DetectionState.PHASE_1
        self._candidate_cycles = []
        self._frequency_rise_cycles = []

    def process_cycle(self, cycle: CycleFeature):
        if self.state in (DetectionState.IDLE, DetectionState.PHASE_3):
            if self._cycle_is_candidate(cycle):
                self._candidate_cycles.append(cycle)
                self.state = DetectionState.CANDIDATE_VIBRATION
                self._evaluate_candidate()
            else:
                self._discard_candidate()
                if self.state is DetectionState.CANDIDATE_VIBRATION:
                    self.state = (
                        DetectionState.PHASE_3
                        if self.current_event is not None
                        else DetectionState.IDLE
                    )
            return

        if self.state is DetectionState.CANDIDATE_VIBRATION:
            if self._cycle_is_candidate(cycle):
                self._candidate_cycles.append(cycle)
                self._evaluate_candidate()
            else:
                self._discard_candidate()
                self.state = (
                    DetectionState.PHASE_3
                    if self.current_event is not None
                    else DetectionState.IDLE
                )
            return

        if self.state is DetectionState.PHASE_1:
            baseline_frequency = self.current_event.phase1_frequency_hz
            is_elevated = (
                cycle.frequency_hz
                >= baseline_frequency * self.config.frequency_increase_ratio
            )
            if is_elevated:
                self._frequency_rise_cycles.append(cycle)
            else:
                self.current_event.phase1_cycle_features.extend(
                    self._frequency_rise_cycles
                )
                self._frequency_rise_cycles.clear()
                self.current_event.phase1_cycle_features.append(cycle)
                self.current_event.phase1_cycles = len(
                    self.current_event.phase1_cycle_features
                )

            if len(self._frequency_rise_cycles) >= self.config.sustained_frequency_cycles:
                sustained = self._frequency_rise_cycles[
                    -self.config.sustained_frequency_cycles :
                ]
                self.current_event.phase2_start_s = sustained[0].start_s
                self.current_event.phase2_cycles.extend(sustained)
                self.state = DetectionState.PHASE_2
                self._frequency_rise_cycles.clear()
            return

        if self.state is DetectionState.PHASE_2:
            self.current_event.phase2_cycles.append(cycle)

    def _evaluate_candidate(self):
        if self._candidate_qualifies(self._candidate_cycles):
            self._begin_event(self._candidate_cycles)

    def _discard_candidate(self):
        if (
            self._candidate_cycles
            and len(self._candidate_cycles) < self.config.minimum_candidate_cycles
        ):
            metrics = self._candidate_metrics(self._candidate_cycles)
            self.candidate_diagnostics.append(
                {
                    "candidate_cycles": len(self._candidate_cycles),
                    **metrics,
                    "qualification_checks": {
                        "minimum_cycles": False,
                        "frequency_stability": None,
                        "amplitude_stability": None,
                        "minimum_snr": None,
                        "periodicity": None,
                    },
                    "passed": False,
                    "rejection_reasons": ["Insufficient cycle count"],
                }
            )
        self._candidate_cycles.clear()

    def process_quiet_interval(self, interval: QuietInterval):
        if self.state is not DetectionState.PHASE_2:
            return
        if interval.start_s <= self.current_event.phase2_start_s:
            return
        minimum_rest = max(
            self.config.phase3_rest_minimum_seconds,
            2.0 / self.current_event.phase1_frequency_hz,
        )
        if interval.end_s - interval.start_s < minimum_rest:
            return
        self.current_event.phase3_start_s = interval.start_s
        self.current_event.phase2_cycles = [
            cycle
            for cycle in self.current_event.phase2_cycles
            if cycle.start_s < interval.start_s
        ]
        self.state = DetectionState.PHASE_3
        self._candidate_cycles.clear()

    def finish(self, recording_end_s, terminal_quiet_interval=None):
        if (
            self.state is DetectionState.PHASE_2
            and self.current_event is not None
            and terminal_quiet_interval is not None
            and terminal_quiet_interval.start_s
            > self.current_event.phase2_start_s
            and terminal_quiet_interval.end_s
            >= recording_end_s - self.config.quiet_window_seconds
            and terminal_quiet_interval.end_s - terminal_quiet_interval.start_s
            >= self.config.terminal_rest_minimum_seconds
        ):
            self.current_event.phase3_start_s = terminal_quiet_interval.start_s
            self.current_event.phase2_cycles = [
                cycle
                for cycle in self.current_event.phase2_cycles
                if cycle.start_s < terminal_quiet_interval.start_s
            ]
            self.state = DetectionState.PHASE_3

        if (
            self.current_event is not None
            and self.current_event.phase2_start_s is not None
            and self.current_event.phase3_start_s is not None
            and self.current_event.end_s is None
        ):
            self.current_event.end_s = float(recording_end_s)
            self.events.append(self.current_event)
        if self.state in (
            DetectionState.PHASE_1,
            DetectionState.PHASE_2,
            DetectionState.CANDIDATE_VIBRATION,
        ):
            partial_cycles = (
                self._candidate_cycles
                if self.state is DetectionState.CANDIDATE_VIBRATION
                else self.current_event.phase2_cycles
                if self.state is DetectionState.PHASE_2
                else self.current_event.phase1_cycle_features
            )
            metrics = self._candidate_metrics(partial_cycles)
            self.candidate_diagnostics.append(
                {
                    "candidate_cycles": len(partial_cycles),
                    **metrics,
                    "qualification_checks": {
                        "minimum_cycles": False,
                        "frequency_stability": None,
                        "amplitude_stability": None,
                        "minimum_snr": None,
                        "periodicity": None,
                    },
                    "passed": False,
                    "rejection_reasons": [
                        "Event not finalized before end of recording"
                    ],
                }
            )
        return list(self.events)
