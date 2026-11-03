"""Editable treatment profiles and persistence for phase detection."""

from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import tempfile


@dataclass(frozen=True)
class TreatmentProfile:
    name: str
    expected_amplitude_category: str
    min_snr: float
    min_cycles: int
    frequency_tolerance: float
    amplitude_tolerance: float
    periodicity_threshold: float
    frequency_increase_ratio: float
    sustained_frequency_cycles: int
    quiet_hysteresis_multiple: float

    @classmethod
    def from_dict(cls, values):
        profile = cls(
            name=str(values.get("name", "")).strip(),
            expected_amplitude_category=str(
                values.get("expected_amplitude_category", "")
            ).strip(),
            min_snr=float(values.get("min_snr", 0)),
            min_cycles=int(values.get("min_cycles", 0)),
            frequency_tolerance=float(values.get("frequency_tolerance", 0)),
            amplitude_tolerance=float(values.get("amplitude_tolerance", 0)),
            periodicity_threshold=float(values.get("periodicity_threshold", 0)),
            frequency_increase_ratio=float(
                values.get("frequency_increase_ratio", 0)
            ),
            sustained_frequency_cycles=int(
                values.get("sustained_frequency_cycles", 0)
            ),
            quiet_hysteresis_multiple=float(
                values.get("quiet_hysteresis_multiple", 0)
            ),
        )
        profile.validate()
        return profile

    def validate(self):
        if not self.name:
            raise ValueError("Treatment profile name is required")
        if not self.expected_amplitude_category:
            raise ValueError("Expected amplitude category is required")
        numeric_values = (
            self.min_snr,
            self.frequency_tolerance,
            self.amplitude_tolerance,
            self.periodicity_threshold,
            self.frequency_increase_ratio,
            self.quiet_hysteresis_multiple,
        )
        if not all(math.isfinite(value) for value in numeric_values):
            raise ValueError("Treatment profile parameters must be finite numbers")
        if self.min_snr <= 0:
            raise ValueError("Minimum signal-to-noise ratio must be positive")
        if self.min_cycles < 2:
            raise ValueError("Minimum consecutive cycles must be at least two")
        if self.frequency_tolerance <= 0 or self.amplitude_tolerance <= 0:
            raise ValueError("Frequency and amplitude tolerances must be positive")
        if not 0 <= self.periodicity_threshold <= 1:
            raise ValueError("Periodicity threshold must be between zero and one")
        if self.frequency_increase_ratio <= 1:
            raise ValueError("Frequency increase ratio must be greater than one")
        if self.sustained_frequency_cycles < 1:
            raise ValueError("Sustained transition cycles must be positive")
        if self.quiet_hysteresis_multiple <= 1:
            raise ValueError("Quiet hysteresis multiple must be greater than one")

    def to_dict(self):
        return asdict(self)


def default_treatment_profiles():
    return [
        TreatmentProfile(
            name="Auto Detect",
            expected_amplitude_category="Automatic",
            min_snr=2.0,
            min_cycles=5,
            frequency_tolerance=0.20,
            amplitude_tolerance=0.55,
            periodicity_threshold=0.60,
            frequency_increase_ratio=1.15,
            sustained_frequency_cycles=3,
            quiet_hysteresis_multiple=1.5,
        ),
        TreatmentProfile(
            name="0.4 mm",
            expected_amplitude_category="0.4 mm",
            min_snr=0.75,
            min_cycles=4,
            frequency_tolerance=0.40,
            amplitude_tolerance=0.85,
            periodicity_threshold=0.25,
            frequency_increase_ratio=1.15,
            sustained_frequency_cycles=3,
            quiet_hysteresis_multiple=1.5,
        ),
        TreatmentProfile(
            name="0.7 mm",
            expected_amplitude_category="0.7 mm",
            min_snr=0.90,
            min_cycles=4,
            frequency_tolerance=0.40,
            amplitude_tolerance=0.85,
            periodicity_threshold=0.25,
            frequency_increase_ratio=1.15,
            sustained_frequency_cycles=3,
            quiet_hysteresis_multiple=1.5,
        ),
        TreatmentProfile(
            name="1.1 mm",
            expected_amplitude_category="1.1 mm",
            min_snr=2.0,
            min_cycles=5,
            frequency_tolerance=0.20,
            amplitude_tolerance=0.55,
            periodicity_threshold=0.60,
            frequency_increase_ratio=1.15,
            sustained_frequency_cycles=3,
            quiet_hysteresis_multiple=1.5,
        ),
    ]


def load_treatment_profiles(path):
    """Load saved profiles, or return the built-in profile library."""
    path = Path(path)
    if not path.exists():
        return default_treatment_profiles()

    with path.open("r", encoding="utf-8") as handle:
        values = json.load(handle)
    if not isinstance(values, list):
        raise ValueError("Treatment profiles file must contain a JSON list")
    if not all(isinstance(value, dict) for value in values):
        raise ValueError("Each treatment profile must be a JSON object")
    profiles = [TreatmentProfile.from_dict(value) for value in values]
    if not any(profile.name == "Auto Detect" for profile in profiles):
        raise ValueError("Treatment profiles must include the built-in Auto Detect profile")
    if len({profile.name.casefold() for profile in profiles}) != len(profiles):
        raise ValueError("Treatment profile names must be unique")
    return profiles


def save_treatment_profiles(path, profiles):
    """Validate profiles and atomically save them as JSON."""
    profiles = list(profiles)
    if not profiles or not any(profile.name == "Auto Detect" for profile in profiles):
        raise ValueError("The built-in Auto Detect profile cannot be removed")
    if len({profile.name.casefold() for profile in profiles}) != len(profiles):
        raise ValueError("Treatment profile names must be unique")
    for profile in profiles:
        profile.validate()

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                [profile.to_dict() for profile in profiles],
                handle,
                indent=2,
            )
            handle.write("\n")
        os.replace(temporary_path, path)
    except Exception:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)
        raise


def infer_amplitude_category(noise_profile, cycles):
    """Classify the strongest stable-cycle envelope relative to its noise floor."""
    amplitudes = [cycle.amplitude for cycle in cycles]
    if not amplitudes or noise_profile.peak_to_peak <= 0:
        return "0.4 mm"
    envelope_ratio = (
        sorted(amplitudes)[min(len(amplitudes) - 1, int(0.9 * len(amplitudes)))]
        / noise_profile.peak_to_peak
    )
    if envelope_ratio < 1.45:
        return "0.4 mm"
    if envelope_ratio < 2.05:
        return "0.7 mm"
    return "1.1 mm"


def resolve_treatment_profile(selected_profile, noise_profile, cycles, profiles):
    """Resolve Auto Detect to the closest configured amplitude-category profile."""
    if selected_profile.name != "Auto Detect":
        return selected_profile, selected_profile.expected_amplitude_category

    inferred_category = infer_amplitude_category(noise_profile, cycles)
    matching = [
        profile
        for profile in profiles
        if profile.expected_amplitude_category.casefold()
        == inferred_category.casefold()
    ]
    if matching:
        resolved = matching[0]
    else:
        defaults = default_treatment_profiles()
        resolved = next(
            profile
            for profile in defaults
            if profile.expected_amplitude_category == inferred_category
        )
    return resolved, inferred_category


def config_values_for_profile(profile):
    """Map editable profile fields onto the shared state-machine settings."""
    return {
        "minimum_candidate_cycles": profile.min_cycles,
        "maximum_frequency_cv": profile.frequency_tolerance,
        "maximum_amplitude_cv": profile.amplitude_tolerance,
        "minimum_periodicity": profile.periodicity_threshold,
        "amplitude_noise_multiple": profile.min_snr,
        "frequency_increase_ratio": profile.frequency_increase_ratio,
        "sustained_frequency_cycles": profile.sustained_frequency_cycles,
        "quiet_hysteresis_multiple": profile.quiet_hysteresis_multiple,
    }


def default_profiles_path():
    return Path(__file__).resolve().parents[2] / "data" / "json" / "detection_profiles.json"
