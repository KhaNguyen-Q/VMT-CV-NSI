"""Run statistical analyses and save their tables and visualizations."""

import os

import pandas as pd

from analysis.comparator_config import generate_comparator_config
from analysis.loader import CYCLE_DETAIL_NUMERIC_COLUMNS, NUMERIC_COLUMNS
from analysis.plots import (
    save_device_mean_plots,
    save_group_boxplots,
    save_group_histograms,
)
from analysis.statistics import (
    save_ranges,
    save_statistics,
    summarize_groups,
    summarize_values,
)


PHASE_ANALYSIS = "Phase Analysis"
DEVICE_ANALYSIS = "Device-Level Analysis"
VARIABILITY_ANALYSIS = "Device-Variability Analysis"
ANALYSES = (PHASE_ANALYSIS, DEVICE_ANALYSIS, VARIABILITY_ANALYSIS)
ALL_VIBRATIONS = "All Vibrations"
STATISTICS_DIRECTORY = "Statisticsoutput"
RANGES_DIRECTORY = "Rangesoutput"
INPUT_DIRECTORY = "Input_files"
VISUALIZATION_DIRECTORY = "visualization"


def _analysis_rows(master_df):
    data = master_df.copy()
    if "Record Type" not in data:
        data["Record Type"] = "Phase Summary"
    if "Vibration" not in data:
        data["Vibration"] = ALL_VIBRATIONS
    if not data["Record Type"].astype(str).str.casefold().eq("phase summary").any():
        raise ValueError("The master dataset contains no Phase Summary rows.")
    for column in CYCLE_DETAIL_NUMERIC_COLUMNS:
        if column not in data:
            data[column] = float("nan")
        data[column] = pd.to_numeric(data[column], errors="coerce")
    return data


def _summary_rows(data):
    return data[
        data["Record Type"].astype(str).str.casefold() == "phase summary"
    ].copy()


def _summarize_records_by_vibration(data, group_columns):
    outputs = []
    for record_type in ("Phase Summary", "Cycle Detail"):
        records = data[
            data["Record Type"].astype(str).str.casefold()
            == record_type.casefold()
        ]
        if records.empty:
            continue

        measurements = (
            NUMERIC_COLUMNS
            if record_type == "Phase Summary"
            else CYCLE_DETAIL_NUMERIC_COLUMNS
        )
        all_vibration_summary = summarize_groups(
            records, group_columns, measurement_columns=measurements
        )
        all_vibration_summary["Vibration"] = ALL_VIBRATIONS
        all_vibration_summary["Record Type"] = record_type
        outputs.append(all_vibration_summary)

        vibration_summary = summarize_groups(
            records,
            (*group_columns, "Vibration"),
            measurement_columns=measurements,
        )
        vibration_summary["Record Type"] = record_type
        outputs.append(vibration_summary)

    return pd.concat(outputs, ignore_index=True)


def _plots_directory(output_dir, analysis, visualization_root=None):
    analysis_name = analysis.casefold().replace(" ", "_").replace("-", "_")
    visualization_root = visualization_root or os.path.join(
        output_dir, "visualizations"
    )
    return os.path.join(visualization_root, analysis_name)


def _run_device_analysis_outputs(
    summary, output_dir, statistics_dir, ranges_dir, visualization_root
):
    outputs = {"csv_files": [], "plots": []}
    path = os.path.join(statistics_dir, "device_level_statistics.csv")
    save_statistics(summary, path)
    outputs["csv_files"].append(path)
    ranges_path = os.path.join(ranges_dir, "device_level_ranges.csv")
    save_ranges(summary, ranges_path)
    outputs["csv_files"].append(ranges_path)
    plot_summary = summary[
        (summary["Record Type"] == "Phase Summary")
        & (summary["Vibration"] == ALL_VIBRATIONS)
    ]
    outputs["plots"] = save_device_mean_plots(
        plot_summary,
        _plots_directory(output_dir, DEVICE_ANALYSIS, visualization_root),
    )
    return outputs


def _run_variability_analysis_outputs(
    device_summary, output_dir, statistics_dir, ranges_dir, visualization_root
):
    outputs = {"csv_files": [], "plots": []}
    variability_rows = []
    for keys, group in device_summary.groupby(
        [
            "Treatment profile",
            "Vibration",
            "Record Type",
            "Phase",
            "Measurement",
        ],
        dropna=False,
        sort=True,
    ):
        variability_rows.append(
            {
                "Treatment profile": keys[0],
                "Vibration": keys[1],
                "Record Type": keys[2],
                "Phase": keys[3],
                "Measurement": keys[4],
                **summarize_values(group["mean"], population=True),
            }
        )
    variability = pd.DataFrame(variability_rows)
    variability_path = os.path.join(
        statistics_dir, "device_variability_statistics.csv"
    )
    save_statistics(variability, variability_path)
    ranges_path = os.path.join(ranges_dir, "device_variability_ranges.csv")
    save_ranges(variability, ranges_path)
    outputs["csv_files"].extend([variability_path, ranges_path])
    plot_summary = device_summary[
        (device_summary["Record Type"] == "Phase Summary")
        & (device_summary["Vibration"] == ALL_VIBRATIONS)
    ]
    plot_data = plot_summary.pivot_table(
        index=["Treatment profile", "Device number", "Phase"],
        columns="Measurement",
        values="mean",
        aggfunc="first",
    ).reset_index()
    plot_data.columns.name = None
    plots_dir = _plots_directory(
        output_dir, VARIABILITY_ANALYSIS, visualization_root
    )
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


def _run_analysis_outputs(
    master_df,
    analysis,
    output_dir,
    device_summary=None,
    statistics_dir=None,
    ranges_dir=None,
    visualization_root=None,
):
    statistics_dir = statistics_dir or output_dir
    ranges_dir = ranges_dir or output_dir
    data = _analysis_rows(master_df)
    summaries = _summary_rows(data)
    outputs = {"csv_files": [], "plots": []}

    if analysis == PHASE_ANALYSIS:
        summary = _summarize_records_by_vibration(
            data, ("Treatment profile", "Phase")
        )
        path = os.path.join(statistics_dir, "phase_population_statistics.csv")
        save_statistics(summary, path)
        outputs["csv_files"].append(path)
        ranges_path = os.path.join(ranges_dir, "phase_population_ranges.csv")
        save_ranges(summary, ranges_path)
        outputs["csv_files"].append(ranges_path)
        plots_dir = _plots_directory(
            output_dir, PHASE_ANALYSIS, visualization_root
        )
        outputs["plots"] = save_group_boxplots(
            summaries,
            ("Treatment profile", "Phase"),
            plots_dir,
            "phase_population",
        )
        outputs["plots"].extend(
            save_group_histograms(
                summaries,
                ("Treatment profile", "Phase"),
                plots_dir,
                "phase_distribution",
            )
        )
    elif analysis == DEVICE_ANALYSIS:
        if device_summary is None:
            device_summary = _summarize_records_by_vibration(
                data,
                ("Treatment profile", "Device number", "Phase"),
            )
        outputs = _run_device_analysis_outputs(
            device_summary,
            output_dir,
            statistics_dir,
            ranges_dir,
            visualization_root,
        )
    else:
        device_path = os.path.join(
            statistics_dir, "device_level_statistics.csv"
        )
        if device_summary is None:
            device_summary = _summarize_records_by_vibration(
                data, ("Treatment profile", "Device number", "Phase")
            )
            save_statistics(device_summary, device_path)
            outputs["csv_files"].append(device_path)
            device_ranges_path = os.path.join(
                ranges_dir, "device_level_ranges.csv"
            )
            save_ranges(device_summary, device_ranges_path)
            outputs["csv_files"].append(device_ranges_path)
        elif not os.path.isfile(device_path):
            save_statistics(device_summary, device_path)
            device_ranges_path = os.path.join(
                ranges_dir, "device_level_ranges.csv"
            )
            save_ranges(device_summary, device_ranges_path)
            outputs["csv_files"].extend([device_path, device_ranges_path])

        variability_outputs = _run_variability_analysis_outputs(
            device_summary,
            output_dir,
            statistics_dir,
            ranges_dir,
            visualization_root,
        )
        outputs["csv_files"].extend(variability_outputs["csv_files"])
        outputs["plots"].extend(variability_outputs["plots"])
    return outputs


def _validate_analysis(master_df):
    if master_df.empty:
        raise ValueError("The master dataset is empty.")
    _analysis_rows(master_df)


def _write_shared_inputs(master_df, file_report, input_dir):
    os.makedirs(input_dir, exist_ok=True)
    master_path = os.path.join(input_dir, "master_dataset.csv")
    master_df.to_csv(master_path, index=False, na_rep="")
    outputs = {"master_dataset": master_path, "csv_files": [], "plots": []}
    if file_report is not None:
        report_path = os.path.join(input_dir, "input_files.csv")
        file_report.to_csv(report_path, index=False)
        outputs["input_report"] = report_path
    return outputs


def run_analysis(master_df, analysis, output_dir, file_report=None):
    """Write the master dataset and one selected analysis."""
    if analysis not in ANALYSES:
        raise ValueError(f"Unknown analysis: {analysis}")
    _validate_analysis(master_df)
    outputs = _write_shared_inputs(
        master_df, file_report, input_dir=output_dir
    )
    analysis_outputs = _run_analysis_outputs(master_df, analysis, output_dir)
    outputs["csv_files"].extend(analysis_outputs["csv_files"])
    outputs["plots"].extend(analysis_outputs["plots"])
    return outputs


def run_all_analyses(master_df, output_dir, file_report=None):
    """Write one shared master dataset and run all three analyses from it."""
    _validate_analysis(master_df)
    statistics_dir = os.path.join(output_dir, STATISTICS_DIRECTORY)
    ranges_dir = os.path.join(output_dir, RANGES_DIRECTORY)
    input_dir = os.path.join(output_dir, INPUT_DIRECTORY)
    visualization_root = os.path.join(output_dir, VISUALIZATION_DIRECTORY)
    for directory in (
        statistics_dir,
        ranges_dir,
        input_dir,
        visualization_root,
    ):
        os.makedirs(directory, exist_ok=True)
    outputs = _write_shared_inputs(
        master_df, file_report, input_dir=input_dir
    )
    outputs["directories"] = {
        "statistics": statistics_dir,
        "ranges": ranges_dir,
        "inputs": input_dir,
        "visualizations": visualization_root,
    }
    outputs["analyses"] = {}
    outputs["analyses"][PHASE_ANALYSIS] = _run_analysis_outputs(
        master_df,
        PHASE_ANALYSIS,
        output_dir,
        statistics_dir=statistics_dir,
        ranges_dir=ranges_dir,
        visualization_root=visualization_root,
    )
    device_summary = _summarize_records_by_vibration(
        _analysis_rows(master_df),
        ("Treatment profile", "Device number", "Phase"),
    )
    outputs["analyses"][DEVICE_ANALYSIS] = _run_analysis_outputs(
        master_df,
        DEVICE_ANALYSIS,
        output_dir,
        device_summary,
        statistics_dir,
        ranges_dir,
        visualization_root,
    )
    outputs["analyses"][VARIABILITY_ANALYSIS] = _run_analysis_outputs(
        master_df,
        VARIABILITY_ANALYSIS,
        output_dir,
        device_summary,
        statistics_dir,
        ranges_dir,
        visualization_root,
    )
    for analysis_outputs in outputs["analyses"].values():
        outputs["csv_files"].extend(analysis_outputs["csv_files"])
        outputs["plots"].extend(analysis_outputs["plots"])
    outputs["comparator_config"] = generate_comparator_config(
        output_dir, ranges_dir=ranges_dir
    )
    return outputs
