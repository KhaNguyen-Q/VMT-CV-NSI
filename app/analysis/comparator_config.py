"""Generate editable comparator settings from the analysis range tables."""

import csv
import json
import os


RANGE_FILES = {
    "phase_population": "phase_population_ranges.csv",
    "device_level": "device_level_ranges.csv",
    "device_variability": "device_variability_ranges.csv",
}

_REQUIRED_COLUMNS = {
    "Treatment profile",
    "Measurement",
    "range_p05_lower",
    "range_p95_upper",
}


def generate_comparator_config(output_dir, ranges_dir=None):
    """Create one editable comparator configuration grouped by profile."""
    ranges_dir = ranges_dir or output_dir
    measurements_by_analysis = {}
    profiles = set()

    for analysis, filename in RANGE_FILES.items():
        path = os.path.join(ranges_dir, filename)
        with open(path, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            missing_columns = sorted(_REQUIRED_COLUMNS.difference(fieldnames))
            if missing_columns:
                raise ValueError(
                    f"{filename} is missing required columns: "
                    + ", ".join(missing_columns)
                )

            measurements_by_profile = {}
            for row in reader:
                profile = (row.get("Treatment profile") or "").strip()
                measurement = (row.get("Measurement") or "").strip()
                if not profile or not measurement:
                    raise ValueError(
                        f"{filename} contains a row without a treatment profile "
                        "or measurement."
                    )
                profiles.add(profile)
                measurements_by_profile.setdefault(profile, set()).add(measurement)
            measurements_by_analysis[analysis] = measurements_by_profile

    if not profiles:
        raise ValueError("No treatment profiles were found in the range CSV files.")

    treatment_profiles = {}
    for profile in sorted(profiles):
        enabled_analyses = {}
        enabled_measurements = {}
        for analysis in RANGE_FILES:
            measurements = sorted(
                measurements_by_analysis[analysis].get(profile, set())
            )
            enabled_analyses[analysis] = bool(measurements)
            enabled_measurements[analysis] = measurements

        treatment_profiles[profile] = {
            "baseline_ranges": {
                analysis: os.path.relpath(
                    os.path.join(ranges_dir, filename), output_dir
                ).replace(os.sep, "/")
                for analysis, filename in RANGE_FILES.items()
            },
            "enabled_analyses": enabled_analyses,
            "enabled_measurements": enabled_measurements,
            "range_selection_method": "p5_p95",
            "warning_thresholds": None,
            "failure_thresholds": None,
            "scoring_rules": None,
            "analysis_weighting": None,
        }

    config = {
        "schema_version": 1,
        "treatment_profiles": treatment_profiles,
    }
    config_path = os.path.join(output_dir, "comparator_config.json")
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    return config_path
