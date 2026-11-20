"""Shared statistical summaries and CSV output helpers."""

import math
import os

import numpy as np
import pandas as pd

from analysis.loader import NUMERIC_COLUMNS


def _statistics(values, population=False):
    values = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if not len(values):
        return {
            "n": 0,
            "mean": math.nan,
            "std": math.nan,
            "median": math.nan,
            "mode": math.nan,
            "min": math.nan,
            "max": math.nan,
            "p05": math.nan,
            "p95": math.nan,
            "q25": math.nan,
            "q75": math.nan,
            "iqr": math.nan,
            "cv_pct": math.nan,
        }
    mean = float(np.mean(values))
    std = float(np.std(values, ddof=0 if population else 1)) if len(values) > (
        0 if population else 1
    ) else math.nan
    q25, q75 = np.percentile(values, [25, 75])
    unique_values, frequencies = np.unique(values, return_counts=True)
    maximum_frequency = int(np.max(frequencies))
    modes = unique_values[frequencies == maximum_frequency]
    if len(values) == 1 or maximum_frequency > 1:
        mode = (
            float(modes[0])
            if len(modes) == 1
            else "; ".join(f"{value:g}" for value in modes)
        )
    else:
        mode = math.nan
    return {
        "n": len(values),
        "mean": mean,
        "std": std,
        "median": float(np.median(values)),
        "mode": mode,
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "p05": float(np.percentile(values, 5)),
        "p95": float(np.percentile(values, 95)),
        "q25": float(q25),
        "q75": float(q75),
        "iqr": float(q75 - q25),
        "cv_pct": (
            float(std / abs(mean) * 100)
            if math.isfinite(std) and mean != 0
            else math.nan
        ),
    }


def summarize_values(values, population=False):
    """Return descriptive statistics for a single numeric population."""
    return _statistics(values, population=population)


def summarize_groups(
    dataframe, group_columns, population=False, measurement_columns=None
):
    """Summarize every numeric measurement within each requested group."""
    rows = []
    measurement_columns = measurement_columns or NUMERIC_COLUMNS
    for keys, group in dataframe.groupby(list(group_columns), dropna=False, sort=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        identity = dict(zip(group_columns, keys))
        for column in measurement_columns:
            stats = _statistics(group[column], population=population)
            rows.append(
                {
                    **identity,
                    "Measurement": column,
                    **stats,
                }
            )
    return pd.DataFrame(rows)


def save_statistics(dataframe, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    dataframe.to_csv(path, index=False, na_rep="")


def generate_ranges(statistics):
    """Generate editable reference boundaries from a statistical summary."""
    required_columns = {
        "mean",
        "median",
        "std",
        "p05",
        "p95",
        "q25",
        "q75",
        "iqr",
    }
    missing_columns = sorted(required_columns.difference(statistics.columns))
    if missing_columns:
        raise ValueError(
            "Statistics CSV is missing required columns: "
            + ", ".join(missing_columns)
        )

    rows = []
    for _, row in statistics.iterrows():
        mean = pd.to_numeric(pd.Series([row["mean"]]), errors="coerce").iloc[0]
        std = pd.to_numeric(pd.Series([row["std"]]), errors="coerce").iloc[0]

        mean_minus_2sd = mean - 2 * std if math.isfinite(std) else math.nan
        mean_plus_2sd = mean + 2 * std if math.isfinite(std) else math.nan
        mean_minus_3sd = mean - 3 * std if math.isfinite(std) else math.nan
        mean_plus_3sd = mean + 3 * std if math.isfinite(std) else math.nan

        q25 = pd.to_numeric(pd.Series([row["q25"]]), errors="coerce").iloc[0]
        q75 = pd.to_numeric(pd.Series([row["q75"]]), errors="coerce").iloc[0]
        iqr = pd.to_numeric(pd.Series([row["iqr"]]), errors="coerce").iloc[0]
        iqr_available = all(math.isfinite(value) for value in (q25, q75, iqr))

        output = {
            column: row[column]
            for column in statistics.columns
        }
        output.update(
            {
                "range_p05_lower": row["p05"],
                "range_p95_upper": row["p95"],
                "range_2sd_lower": mean_minus_2sd,
                "range_2sd_upper": mean_plus_2sd,
                "range_3sd_lower": mean_minus_3sd,
                "range_3sd_upper": mean_plus_3sd,
                "range_iqr_lower": q25 - 1.5 * iqr if iqr_available else math.nan,
                "range_iqr_upper": q75 + 1.5 * iqr if iqr_available else math.nan,
            }
        )
        rows.append(output)

    return pd.DataFrame(rows)


def save_ranges(statistics, path):
    """Validate and save derived range boundaries to a CSV file."""
    save_statistics(generate_ranges(statistics), path)
