"""Statistical comparison helpers for comparator configurations."""

import csv
import math
import os

import pandas as pd

try:
    from analysis.comparator_config import get_profile_config
    from analysis.loader import load_range_csvs
    from analysis.treatment_profiles import canonical_treatment_profile
except ModuleNotFoundError:
    from app.analysis.comparator_config import get_profile_config
    from app.analysis.loader import load_range_csvs
    from app.analysis.treatment_profiles import canonical_treatment_profile


RANGE_COLUMNS = {
    "p5_p95": ("range_p05_lower", "range_p95_upper"),
    "2sd": ("range_2sd_lower", "range_2sd_upper"),
    "3sd": ("range_3sd_lower", "range_3sd_upper"),
    "iqr": ("range_iqr_lower", "range_iqr_upper"),
}


def detect_comparison_csv_format(path):
    """Return ``range_export`` for labeled exports, otherwise ``tracking``."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        fieldnames = csv.DictReader(handle).fieldnames or []
    range_signature = {
        "Treatment profile",
        "Phase",
        "Record Type",
    }
    return (
        "range_export"
        if range_signature.issubset(fieldnames)
        else "tracking"
    )


def available_comparison_profiles(path):
    """Read treatment profiles from a validated exported-range CSV."""
    if detect_comparison_csv_format(path) != "range_export":
        return []
    dataframe, _file_report = load_range_csvs([path])
    summary = dataframe[
        dataframe["Record Type"].astype(str).str.casefold() == "phase summary"
    ]
    return sorted(summary["Treatment profile"].dropna().unique().tolist())


def load_range_comparison_csv(path, treatment_profile):
    """Load exported range rows for one selected treatment profile."""
    if detect_comparison_csv_format(path) != "range_export":
        raise ValueError("Selected CSV is not an exported range CSV.")
    dataframe, _file_report = load_range_csvs([path])
    profile = canonical_treatment_profile(treatment_profile)
    selected = dataframe[
        dataframe["Treatment profile"].map(canonical_treatment_profile)
        == profile
    ].copy()
    if not selected["Record Type"].astype(str).str.casefold().eq(
        "phase summary"
    ).any():
        raise ValueError(
            f'The comparison CSV has no Phase Summary rows for treatment '
            f'profile "{profile}".'
        )
    return selected


def resolve_range_paths(config_path, profile_config):
    loaded = profile_config[0]
    profile = profile_config[1]
    baseline_ranges = profile.get("baseline_ranges", {})
    resolved = {}
    for analysis, relative_path in baseline_ranges.items():
        if not relative_path:
            continue
        path = os.path.normpath(
            os.path.join(loaded["directory"], str(relative_path).replace("/", os.sep))
        )
        if not os.path.isfile(path):
            raise ValueError(
                f'Baseline range file is missing for analysis "{analysis}":\n{path}'
            )
        resolved[analysis] = path
    return resolved


def get_range_row(config_path, treatment_profile, analysis, measurement, phase=None,
                  record_type="Phase Summary", vibration="All Vibrations",
                  range_selection_method=None):
    """Return one baseline range row and its selected lower/upper bounds."""
    loaded, profile = get_profile_config(config_path, treatment_profile)
    if not profile.get("enabled_analyses", {}).get(analysis, False):
        raise ValueError(
            f'Analysis "{analysis}" is not enabled for treatment profile '
            f'"{treatment_profile}".'
        )

    ranges = resolve_range_paths(config_path, (loaded, profile))
    range_path = ranges.get(analysis)
    if range_path is None:
        raise ValueError(f'No range file is configured for analysis "{analysis}".')

    dataframe = pd.read_csv(range_path)
    required = {
        "Treatment profile",
        "Measurement",
        "Record Type",
        "Vibration",
    }
    missing = sorted(required.difference(dataframe.columns))
    if missing:
        raise ValueError(
            f"Range CSV is missing required columns: {', '.join(missing)}"
        )

    profile_key = treatment_profile.strip().casefold()
    matching = dataframe[
        dataframe["Treatment profile"].astype(str).str.strip().str.casefold()
        == profile_key
    ]
    matching = matching[
        matching["Measurement"].astype(str).str.strip() == measurement.strip()
    ]
    if phase is not None and "Phase" in matching.columns:
        matching = matching[
            matching["Phase"].astype(str).str.strip().str.casefold()
            == str(phase).strip().casefold()
        ]
    matching = matching[
        matching["Record Type"].astype(str).str.strip().str.casefold()
        == record_type.strip().casefold()
    ]
    matching = matching[
        matching["Vibration"].astype(str).str.strip().str.casefold()
        == vibration.strip().casefold()
    ]
    if matching.empty:
        raise ValueError(
            f"No baseline range found for {measurement}, {phase or 'all phases'}, "
            f"{record_type}, {vibration}."
        )
    if len(matching) > 1:
        raise ValueError(
            f"Multiple baseline range rows match {measurement} "
            f"for {phase or 'all phases'}."
        )

    row = matching.iloc[0]
    method = (
        range_selection_method
        or profile.get("range_selection_method")
        or "p5_p95"
    )
    method = method.strip().lower()
    if method not in RANGE_COLUMNS:
        raise ValueError(f'Unknown range-selection method "{method}".')
    lower_column, upper_column = RANGE_COLUMNS[method]
    for column in (lower_column, upper_column):
        if column not in dataframe.columns:
            raise ValueError(f'Range CSV is missing column "{column}".')
    lower = pd.to_numeric(pd.Series([row[lower_column]]), errors="coerce").iloc[0]
    upper = pd.to_numeric(pd.Series([row[upper_column]]), errors="coerce").iloc[0]
    if not math.isfinite(float(lower)) or not math.isfinite(float(upper)):
        raise ValueError("Selected baseline bounds are missing or invalid.")

    return {
        "method": method,
        "lower": float(lower),
        "upper": float(upper),
        "range_csv": range_path,
        "row": row.to_dict(),
    }


def compare_observed_value(config_path, treatment_profile, analysis, measurement,
                           observed_value, phase=None, record_type="Phase Summary",
                           vibration="All Vibrations", range_selection_method=None):
    """Compare one observed numeric value against a baseline range."""
    observed = pd.to_numeric(pd.Series([observed_value]), errors="coerce").iloc[0]
    if not math.isfinite(float(observed)):
        return {
            "status": "INCONCLUSIVE",
            "observed": None,
            "lower": None,
            "upper": None,
        }
    baseline = get_range_row(
        config_path,
        treatment_profile,
        analysis,
        measurement,
        phase=phase,
        record_type=record_type,
        vibration=vibration,
        range_selection_method=range_selection_method,
    )
    observed = float(observed)
    return {
        "status": "PASS" if baseline["lower"] <= observed <= baseline["upper"] else "FAIL",
        "observed": observed,
        "lower": baseline["lower"],
        "upper": baseline["upper"],
        "method": baseline["method"],
        "range_csv": baseline["range_csv"],
    }
