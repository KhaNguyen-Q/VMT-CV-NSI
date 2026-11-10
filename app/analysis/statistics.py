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
            "min": math.nan,
            "max": math.nan,
            "p05": math.nan,
            "p95": math.nan,
            "iqr": math.nan,
            "cv_pct": math.nan,
        }
    mean = float(np.mean(values))
    std = float(np.std(values, ddof=0 if population else 1)) if len(values) > (
        0 if population else 1
    ) else math.nan
    q25, q75 = np.percentile(values, [25, 75])
    return {
        "n": len(values),
        "mean": mean,
        "std": std,
        "median": float(np.median(values)),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "p05": float(np.percentile(values, 5)),
        "p95": float(np.percentile(values, 95)),
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


def summarize_groups(dataframe, group_columns, population=False):
    """Summarize every numeric measurement within each requested group."""
    rows = []
    for keys, group in dataframe.groupby(list(group_columns), dropna=False, sort=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        identity = dict(zip(group_columns, keys))
        for column in NUMERIC_COLUMNS:
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


def acceptance_ranges(variability):
    """Calculate candidate ranges from per-device measurement means."""
    rows = []
    for _, row in variability.iterrows():
        mean = row["mean"]
        std = row["std"]
        rows.append(
            {
                "Treatment profile": row["Treatment profile"],
                "Phase": row["Phase"],
                "Measurement": row["Measurement"],
                "Devices (n)": row["n"],
                "Mean": mean,
                "Mean - 2 SD": mean - 2 * std if math.isfinite(std) else math.nan,
                "Mean + 2 SD": mean + 2 * std if math.isfinite(std) else math.nan,
                "Mean - 3 SD": mean - 3 * std if math.isfinite(std) else math.nan,
                "Mean + 3 SD": mean + 3 * std if math.isfinite(std) else math.nan,
                "5th Percentile": row["p05"],
                "95th Percentile": row["p95"],
            }
        )
    return pd.DataFrame(rows)
