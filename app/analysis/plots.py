"""Save summary charts for statistical analysis outputs."""

import os
import re

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

from analysis.loader import NUMERIC_COLUMNS


def _safe_name(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_")


def save_group_boxplots(dataframe, group_columns, output_dir, prefix):
    """Save a box plot for each measurement, grouped by the requested columns."""
    os.makedirs(output_dir, exist_ok=True)
    paths = []
    if len(dataframe) < 1:
        return paths

    labels = dataframe[list(group_columns)].astype(str).agg(" / ".join, axis=1)
    for measurement in NUMERIC_COLUMNS:
        plot_data = dataframe.assign(_group_label=labels)
        grouped = [
            group[measurement].dropna().to_numpy()
            for _, group in plot_data.groupby("_group_label", sort=True)
        ]
        plot_labels = [
            label
            for label, group in plot_data.groupby("_group_label", sort=True)
            if group[measurement].notna().any()
        ]
        grouped = [values for values in grouped if len(values)]
        if not grouped:
            continue

        width = max(8, min(18, len(plot_labels) * 0.6))
        figure, axis = plt.subplots(figsize=(width, 6))
        axis.boxplot(grouped, showmeans=True)
        axis.set_xticks(range(1, len(plot_labels) + 1))
        axis.set_xticklabels(plot_labels)
        axis.set_title(f"{measurement} by " + ", ".join(group_columns))
        axis.set_ylabel(measurement)
        axis.tick_params(axis="x", labelrotation=35)
        figure.tight_layout()
        path = os.path.join(
            output_dir, f"{prefix}_{_safe_name(measurement)}.png"
        )
        figure.savefig(path, dpi=150)
        plt.close(figure)
        paths.append(path)
    return paths


def save_group_histograms(dataframe, group_columns, output_dir, prefix):
    """Save grouped measurement histograms for event/device distributions."""
    os.makedirs(output_dir, exist_ok=True)
    paths = []
    if len(dataframe) < 1:
        return paths

    labels = dataframe[list(group_columns)].astype(str).agg(" / ".join, axis=1)
    plot_data = dataframe.assign(_group_label=labels)
    groups = list(plot_data.groupby("_group_label", sort=True))
    for measurement in NUMERIC_COLUMNS:
        figure, axis = plt.subplots(figsize=(9, 6))
        plotted = 0
        for label, group in groups:
            values = group[measurement].dropna().to_numpy()
            if not len(values):
                continue
            axis.hist(values, bins="auto", alpha=0.45, label=label)
            plotted += 1
        if not plotted:
            plt.close(figure)
            continue
        axis.set_title(f"{measurement} distribution")
        axis.set_xlabel(measurement)
        axis.set_ylabel("Event count")
        if plotted <= 8:
            axis.legend()
        figure.tight_layout()
        path = os.path.join(
            output_dir, f"{prefix}_{_safe_name(measurement)}.png"
        )
        figure.savefig(path, dpi=150)
        plt.close(figure)
        paths.append(path)
    return paths


def save_device_mean_plots(device_summary, output_dir):
    """Plot device-level means for each profile/phase/measurement."""
    os.makedirs(output_dir, exist_ok=True)
    paths = []
    for measurement in NUMERIC_COLUMNS:
        selected = device_summary[device_summary["Measurement"] == measurement]
        if selected.empty:
            continue
        selected = selected.copy()
        selected["_label"] = (
            selected["Treatment profile"].astype(str)
            + " / "
            + selected["Device number"].astype(str)
            + " / "
            + selected["Phase"].astype(str)
        )
        figure, axis = plt.subplots(figsize=(max(8, min(18, len(selected) * 0.6)), 6))
        axis.errorbar(
            range(len(selected)),
            selected["mean"],
            yerr=selected["std"],
            fmt="o",
            capsize=3,
        )
        axis.set_xticks(range(len(selected)), selected["_label"], rotation=35, ha="right")
        axis.set_title(f"Device means: {measurement}")
        axis.set_ylabel(measurement)
        figure.tight_layout()
        path = os.path.join(
            output_dir, f"device_means_{_safe_name(measurement)}.png"
        )
        figure.savefig(path, dpi=150)
        plt.close(figure)
        paths.append(path)
    return paths
