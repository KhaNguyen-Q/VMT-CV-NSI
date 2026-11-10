"""Run statistical analyses and save their tables and visualizations."""

import os

import pandas as pd

from analysis.loader import NUMERIC_COLUMNS
from analysis.plots import (
    save_device_mean_plots,
    save_group_boxplots,
    save_group_histograms,
)
from analysis.statistics import (
    acceptance_ranges,
    save_statistics,
    summarize_groups,
    summarize_values,
)


PHASE_ANALYSIS = "Phase Analysis"
DEVICE_ANALYSIS = "Device-Level Analysis"
VARIABILITY_ANALYSIS = "Device-Variability Analysis"
ANALYSES = (PHASE_ANALYSIS, DEVICE_ANALYSIS, VARIABILITY_ANALYSIS)


def _summary_rows(master_df):
    if "Record Type" not in master_df:
        return master_df
    summaries = master_df[
        master_df["Record Type"].astype(str).str.casefold() == "phase summary"
    ].copy()
    if summaries.empty:
        raise ValueError("The master dataset contains no Phase Summary rows.")
    for column in NUMERIC_COLUMNS:
        summaries[column] = pd.to_numeric(summaries[column], errors="coerce")
    return summaries


def _plots_directory(output_dir, analysis):
    analysis_name = analysis.casefold().replace(" ", "_").replace("-", "_")
    return os.path.join(output_dir, "visualizations", analysis_name)


def _run_device_analysis_outputs(summary, output_dir):
    outputs = {"csv_files": [], "plots": []}
    path = os.path.join(output_dir, "device_level_statistics.csv")
    save_statistics(summary, path)
    outputs["csv_files"].append(path)
    outputs["plots"] = save_device_mean_plots(
        summary, _plots_directory(output_dir, DEVICE_ANALYSIS)
    )
    return outputs


def _run_variability_analysis_outputs(device_summary, output_dir):
    outputs = {"csv_files": [], "plots": []}
    variability_rows = []
    for keys, group in device_summary.groupby(
        ["Treatment profile", "Phase", "Measurement"],
        dropna=False,
        sort=True,
    ):
        variability_rows.append(
            {
                "Treatment profile": keys[0],
                "Phase": keys[1],
                "Measurement": keys[2],
                **summarize_values(group["mean"], population=True),
            }
        )
    variability = pd.DataFrame(variability_rows)
    variability_path = os.path.join(
        output_dir, "device_variability_statistics.csv"
    )
    save_statistics(variability, variability_path)
    ranges_path = os.path.join(output_dir, "acceptance_range_candidates.csv")
    save_statistics(acceptance_ranges(variability), ranges_path)
    outputs["csv_files"].extend([variability_path, ranges_path])
    plot_data = device_summary.pivot_table(
        index=["Treatment profile", "Device number", "Phase"],
        columns="Measurement",
        values="mean",
        aggfunc="first",
    ).reset_index()
    plot_data.columns.name = None
    plots_dir = _plots_directory(output_dir, VARIABILITY_ANALYSIS)
    outputs["plots"] = save_group_boxplots(
        plot_data,
        ("Treatment profile", "Phase"),
        plots_dir,
        "device_variability",
    )
    outputs["plots"].extend(
        save_group_histograms(
            plot_data,
            ("Treatment profile", "Phase"),
            plots_dir,
            "device_variability_distribution",
        )
    )
    return outputs


def _run_analysis_outputs(master_df, analysis, output_dir, device_summary=None):
    data = _summary_rows(master_df)
    outputs = {"csv_files": [], "plots": []}

    if analysis == PHASE_ANALYSIS:
        summary = summarize_groups(data, ("Treatment profile", "Phase"))
        path = os.path.join(output_dir, "phase_population_statistics.csv")
        save_statistics(summary, path)
        outputs["csv_files"].append(path)
        plots_dir = _plots_directory(output_dir, PHASE_ANALYSIS)
        outputs["plots"] = save_group_boxplots(
            data,
            ("Treatment profile", "Phase"),
            plots_dir,
            "phase_population",
        )
        outputs["plots"].extend(
            save_group_histograms(
                data,
                ("Treatment profile", "Phase"),
                plots_dir,
                "phase_distribution",
            )
        )
    elif analysis == DEVICE_ANALYSIS:
        if device_summary is None:
            device_summary = summarize_groups(
                data,
                ("Treatment profile", "Device number", "Phase"),
            )
        outputs = _run_device_analysis_outputs(device_summary, output_dir)
    else:
        device_path = os.path.join(output_dir, "device_level_statistics.csv")
        if device_summary is None:
            device_summary = summarize_groups(
                data,
                ("Treatment profile", "Device number", "Phase"),
            )
            save_statistics(device_summary, device_path)
            outputs["csv_files"].append(device_path)
        elif not os.path.isfile(device_path):
            save_statistics(device_summary, device_path)

        device_results = pd.read_csv(
            device_path,
            dtype={"Device number": str},
        )
        variability_outputs = _run_variability_analysis_outputs(
            device_results, output_dir
        )
        outputs["csv_files"].extend(variability_outputs["csv_files"])
        outputs["plots"].extend(variability_outputs["plots"])
    return outputs


def _validate_analysis(master_df):
    if master_df.empty:
        raise ValueError("The master dataset is empty.")
    _summary_rows(master_df)


def _write_shared_inputs(master_df, output_dir, file_report):
    os.makedirs(output_dir, exist_ok=True)
    master_path = os.path.join(output_dir, "master_dataset.csv")
    master_df.to_csv(master_path, index=False, na_rep="")
    outputs = {"master_dataset": master_path, "csv_files": [], "plots": []}
    if file_report is not None:
        report_path = os.path.join(output_dir, "input_files.csv")
        file_report.to_csv(report_path, index=False)
        outputs["input_report"] = report_path
    return outputs


def run_analysis(master_df, analysis, output_dir, file_report=None):
    """Write the master dataset and one selected analysis."""
    if analysis not in ANALYSES:
        raise ValueError(f"Unknown analysis: {analysis}")
    _validate_analysis(master_df)
    outputs = _write_shared_inputs(master_df, output_dir, file_report)
    analysis_outputs = _run_analysis_outputs(master_df, analysis, output_dir)
    outputs["csv_files"].extend(analysis_outputs["csv_files"])
    outputs["plots"].extend(analysis_outputs["plots"])
    return outputs


def run_all_analyses(master_df, output_dir, file_report=None):
    """Write one shared master dataset and run all three analyses from it."""
    _validate_analysis(master_df)
    outputs = _write_shared_inputs(master_df, output_dir, file_report)
    outputs["analyses"] = {}
    outputs["analyses"][PHASE_ANALYSIS] = _run_analysis_outputs(
        master_df, PHASE_ANALYSIS, output_dir
    )
    device_summary = summarize_groups(
        _summary_rows(master_df),
        ("Treatment profile", "Device number", "Phase"),
    )
    outputs["analyses"][DEVICE_ANALYSIS] = _run_analysis_outputs(
        master_df, DEVICE_ANALYSIS, output_dir, device_summary
    )
    outputs["analyses"][VARIABILITY_ANALYSIS] = _run_analysis_outputs(
        master_df, VARIABILITY_ANALYSIS, output_dir, device_summary
    )
    for analysis_outputs in outputs["analyses"].values():
        outputs["csv_files"].extend(analysis_outputs["csv_files"])
        outputs["plots"].extend(analysis_outputs["plots"])
    return outputs
