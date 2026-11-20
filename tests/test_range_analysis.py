import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from analysis.loader import load_range_csvs
from analysis.runner import (
    DEVICE_ANALYSIS,
    PHASE_ANALYSIS,
    VARIABILITY_ANALYSIS,
    run_all_analyses,
    run_analysis,
)
from analysis.statistics import generate_ranges, summarize_values
from windows.range_csv_export import RANGE_EXPORT_COLUMNS


class RangeAnalysisTests(unittest.TestCase):
    def _write_csv(self, path, rows):
        columns = (
            "Vibration",
            "Phase",
            "Record Type",
            "Cycle #",
            "Cycle Start (s)",
            "Cycle End (s)",
            "Duration (s)",
            "High Peak (mm)",
            "Low Peak (mm)",
            "Peak-to-Peak (mm)",
            "Amplitude (mm)",
            "Cycle Period (s)",
            "Frequency (Hz)",
            "Cycle Count",
            "Client name",
            "Device number",
            "Date",
            "Camera",
            "FPS",
            "Treatment profile",
        )
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def _summary(self, device, vibration, amplitude, profile="Profile A"):
        return {
            "Vibration": f"Vibration {vibration}",
            "Phase": "Phase 1",
            "Record Type": "Phase Summary",
            "Duration (s)": "2",
            "High Peak (mm)": str(amplitude),
            "Low Peak (mm)": str(-amplitude),
            "Peak-to-Peak (mm)": str(2 * amplitude),
            "Amplitude (mm)": str(amplitude),
            "Cycle Period (s)": "0.5",
            "Frequency (Hz)": "2",
            "Cycle Count": "4",
            "Device number": device,
            "Treatment profile": profile,
        }

    def test_loader_preserves_summary_and_cycle_detail_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "device.csv"
            self._write_csv(
                path,
                [
                    self._summary("D1", 1, 2),
                    {
                        **self._summary("D1", 1, 2),
                        "Record Type": "Cycle Detail",
                    },
                ],
            )
            master, report = load_range_csvs([path])

        self.assertEqual(len(master), 2)
        self.assertEqual(master.iloc[0]["Amplitude (mm)"], "2")
        self.assertEqual(master.iloc[1]["Record Type"], "Cycle Detail")
        self.assertEqual(report.iloc[0]["Cycle-detail rows loaded"], 1)
        self.assertEqual(report.iloc[0]["Summary rows loaded"], 1)

    def test_loader_rejects_files_with_incompatible_headers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "wrong.csv"
            path.write_text("baseline,compare\nA,B\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "No valid Phase Summary rows"):
                load_range_csvs([path])

    def test_phase_analysis_outputs_population_statistics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.csv"
            self._write_csv(
                path,
                [
                    self._summary("D1", 1, 2),
                    self._summary("D2", 1, 4),
                ],
            )
            master, report = load_range_csvs([path])
            output = Path(temp_dir) / "phase"
            result = run_analysis(master, PHASE_ANALYSIS, output, report)
            with (output / "phase_population_statistics.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                rows = list(csv.DictReader(handle))
            master_exists = (output / "master_dataset.csv").is_file()
            report_exists = (output / "input_files.csv").is_file()

        amplitude = next(
            row for row in rows if row["Measurement"] == "Amplitude (mm)"
        )
        self.assertEqual(amplitude["n"], "2")
        self.assertEqual(amplitude["mean"], "3.0")
        self.assertTrue(result["plots"])
        self.assertTrue(master_exists)
        self.assertTrue(report_exists)

    def test_profile_punctuation_variants_are_combined_in_all_analysis_outputs(self):
        canonical_profile = "C2-(Left, Middle, Right)"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.csv"
            self._write_csv(
                path,
                [
                    self._summary("D1", 1, 2, canonical_profile),
                    self._summary("D2", 1, 4, "C2 (Left, Middle, Right)"),
                ],
            )
            master, report = load_range_csvs([path])
            output = Path(temp_dir) / "analysis"
            run_all_analyses(master, output, report)
            output_rows = {}
            for name in (
                "device_variability_statistics.csv",
                "device_level_statistics.csv",
                "phase_population_statistics.csv",
            ):
                with (output / name).open(newline="", encoding="utf-8") as handle:
                    output_rows[name] = list(csv.DictReader(handle))

        for rows in output_rows.values():
            self.assertEqual(
                {row["Treatment profile"] for row in rows},
                {canonical_profile},
            )
        phase_amplitude = next(
            row
            for row in output_rows["phase_population_statistics.csv"]
            if row["Measurement"] == "Amplitude (mm)"
        )
        self.assertEqual(phase_amplitude["n"], "2")

    def test_device_analysis_groups_events_by_device_and_phase(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.csv"
            self._write_csv(
                path,
                [
                    self._summary("D1", 1, 2),
                    self._summary("D1", 2, 4),
                    self._summary("D2", 1, 6),
                ],
            )
            master, _ = load_range_csvs([path])
            output = Path(temp_dir) / "device"
            run_analysis(master, DEVICE_ANALYSIS, output)
            with (output / "device_level_statistics.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                rows = list(csv.DictReader(handle))

        amplitude_rows = [
            row
            for row in rows
            if row["Measurement"] == "Amplitude (mm)"
            and row["Vibration"] == "All Vibrations"
            and row["Record Type"] == "Phase Summary"
        ]
        self.assertEqual(len(amplitude_rows), 2)
        device_one = next(row for row in amplitude_rows if row["Device number"] == "D1")
        self.assertEqual(device_one["mean"], "3.0")

    def test_variability_compares_per_device_means(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.csv"
            self._write_csv(
                path,
                [
                    self._summary("D1", 1, 2),
                    self._summary("D1", 2, 4),
                    self._summary("D1", 3, 6),
                    self._summary("D2", 1, 10),
                ],
            )
            master, _ = load_range_csvs([path])
            output = Path(temp_dir) / "variability"
            run_analysis(master, VARIABILITY_ANALYSIS, output)
            with (output / "device_variability_statistics.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                rows = list(csv.DictReader(handle))
            with (output / "device_variability_ranges.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                ranges = list(csv.DictReader(handle))
            with (output / "device_level_statistics.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                device_rows = list(csv.DictReader(handle))

        amplitude = next(
            row
            for row in rows
            if row["Measurement"] == "Amplitude (mm)"
            and row["Vibration"] == "All Vibrations"
            and row["Record Type"] == "Phase Summary"
        )
        device_amplitudes = [
            row
            for row in device_rows
            if row["Measurement"] == "Amplitude (mm)"
            and row["Vibration"] == "All Vibrations"
            and row["Record Type"] == "Phase Summary"
        ]
        # D1 contributes its event mean (4), while D2 contributes its mean (10).
        self.assertEqual(len(device_amplitudes), 2)
        self.assertEqual(
            next(row for row in device_amplitudes if row["Device number"] == "D1")[
                "mean"
            ],
            "4.0",
        )
        self.assertEqual(
            next(row for row in device_amplitudes if row["Device number"] == "D2")[
                "mean"
            ],
            "10.0",
        )
        self.assertEqual(amplitude["n"], "2")
        self.assertEqual(amplitude["mean"], "7.0")
        self.assertEqual(amplitude["std"], "3.0")
        self.assertTrue(
            any(row["Measurement"] == "Amplitude (mm)" for row in ranges)
        )

        vibration_three = next(
            row
            for row in rows
            if row["Measurement"] == "Amplitude (mm)"
            and row["Vibration"] == "Vibration 3"
            and row["Record Type"] == "Phase Summary"
        )
        self.assertEqual(vibration_three["n"], "1")
        self.assertEqual(vibration_three["mean"], "6.0")

    def test_cycle_detail_rows_add_vibration_specific_statistics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.csv"
            summary = self._summary("D1", 1, 2)
            detail = {
                **summary,
                "Record Type": "Cycle Detail",
                "Cycle #": "1",
                "Cycle Start (s)": "0.000",
                "Cycle End (s)": "0.500",
                "Duration (s)": "0.500",
                "High Peak (mm)": "3.000",
                "Low Peak (mm)": "-3.000",
                "Peak-to-Peak (mm)": "6.000",
                "Amplitude (mm)": "3.000",
                "Cycle Period (s)": "0.500",
                "Frequency (Hz)": "2.000",
            }
            second_detail = {
                **detail,
                "Cycle #": "2",
                "Cycle Start (s)": "0.500",
                "Cycle End (s)": "1.000",
            }
            self._write_csv(path, [summary, detail, second_detail])
            master, _ = load_range_csvs([path])
            output = Path(temp_dir) / "cycle-statistics"
            run_all_analyses(master, output)

            with (output / "phase_population_statistics.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                phase_rows = list(csv.DictReader(handle))
            with (output / "device_level_statistics.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                device_rows = list(csv.DictReader(handle))

        cycle_peak = next(
            row
            for row in phase_rows
            if row["Record Type"] == "Cycle Detail"
            and row["Vibration"] == "Vibration 1"
            and row["Measurement"] == "High Peak (mm)"
        )
        self.assertEqual(cycle_peak["n"], "2")
        self.assertEqual(cycle_peak["mean"], "3.0")
        cycle_starts = [
            row
            for row in phase_rows
            if row["Record Type"] == "Cycle Detail"
            and row["Vibration"] == "Vibration 1"
            and row["Measurement"] == "Cycle Start (s)"
        ]
        cycle_ends = [
            row
            for row in phase_rows
            if row["Record Type"] == "Cycle Detail"
            and row["Vibration"] == "Vibration 1"
            and row["Measurement"] == "Cycle End (s)"
        ]
        self.assertEqual(cycle_starts[0]["n"], "2")
        self.assertEqual(cycle_starts[0]["mean"], "0.25")
        self.assertEqual(cycle_ends[0]["n"], "2")
        self.assertEqual(cycle_ends[0]["mean"], "0.75")
        self.assertTrue(
            any(
                row["Record Type"] == "Cycle Detail"
                and row["Vibration"] == "Vibration 1"
                and row["Measurement"] == "High Peak (mm)"
                for row in device_rows
            )
        )

    def test_generic_range_generation_calculates_all_boundaries(self):
        statistics = pd.DataFrame(
            [
                {
                    "Treatment profile": "Profile A",
                    "Phase": "Phase 1",
                    "Measurement": "Amplitude (mm)",
                    "mean": 5.0,
                    "median": 5.0,
                    "std": 3.0,
                    "p05": 1.0,
                    "p95": 9.0,
                    "q25": 2.0,
                    "q75": 8.0,
                    "iqr": 6.0,
                }
            ]
        )

        ranges = generate_ranges(statistics)
        row = ranges.iloc[0]

        self.assertEqual(row["Treatment profile"], "Profile A")
        self.assertEqual(row["Phase"], "Phase 1")
        self.assertEqual(row["Measurement"], "Amplitude (mm)")
        self.assertEqual(row["range_p05_lower"], 1.0)
        self.assertEqual(row["range_p95_upper"], 9.0)
        self.assertEqual(row["range_2sd_lower"], -1.0)
        self.assertEqual(row["range_2sd_upper"], 11.0)
        self.assertEqual(row["range_3sd_lower"], -4.0)
        self.assertEqual(row["range_3sd_upper"], 14.0)
        self.assertEqual(row["range_iqr_lower"], -7.0)
        self.assertEqual(row["range_iqr_upper"], 17.0)
        self.assertIn("mean", ranges.columns)
        self.assertNotIn("Mean", ranges.columns)

    def test_statistics_include_mode_and_preserve_tied_modes(self):
        tied_mode = summarize_values(pd.Series([1.0, 1.0, 2.0, 2.0, 3.0]))
        no_mode = summarize_values(pd.Series([1.0, 2.0, 3.0]))

        self.assertEqual(tied_mode["mode"], "1; 2")
        self.assertTrue(pd.isna(no_mode["mode"]))

    def test_all_analyses_share_one_master_with_cycle_details(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.csv"
            summary = self._summary("D1", 1, 2)
            detail = {
                **summary,
                "Record Type": "Cycle Detail",
                "Cycle #": "1",
                "Cycle Start (s)": "0.000",
                "Cycle End (s)": "0.500",
                "Duration (s)": "0.500",
                "High Peak (mm)": "2.000",
                "Low Peak (mm)": "-2.000",
                "Peak-to-Peak (mm)": "4.000",
                "Amplitude (mm)": "2.000",
                "Cycle Period (s)": "0.500",
                "Frequency (Hz)": "2.000",
            }
            self._write_csv(path, [summary, detail])
            master, report = load_range_csvs([path])
            output = Path(temp_dir) / "named-analysis"
            results = run_all_analyses(master, output, report)
            with Path(results["comparator_config"]).open(
                encoding="utf-8"
            ) as handle:
                comparator_config = json.load(handle)

            with (output / "master_dataset.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                master_rows = list(csv.DictReader(handle))
            with (output / "phase_population_statistics.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                phase_rows = list(csv.DictReader(handle))
            report_exists = (output / "input_files.csv").is_file()
            range_files_exist = (
                (output / "phase_population_ranges.csv").is_file(),
                (output / "device_level_ranges.csv").is_file(),
                (output / "device_variability_ranges.csv").is_file(),
            )
            mode_columns_exist = []
            for name in (
                "phase_population_statistics.csv",
                "phase_population_ranges.csv",
                "device_level_statistics.csv",
                "device_level_ranges.csv",
                "device_variability_statistics.csv",
                "device_variability_ranges.csv",
            ):
                with (output / name).open(
                    newline="", encoding="utf-8"
                ) as handle:
                    mode_columns_exist.append(
                        "mode" in csv.DictReader(handle).fieldnames
                    )

        self.assertEqual(len(master_rows), 2)
        self.assertEqual(
            list(master_rows[0]),
            [*RANGE_EXPORT_COLUMNS, "Source File"],
        )
        self.assertEqual(master_rows[1]["Record Type"], "Cycle Detail")
        self.assertEqual(master_rows[1]["Cycle Start (s)"], "0.000")
        self.assertEqual(master_rows[1]["Source File"], "events.csv")
        self.assertEqual(
            next(
                row for row in phase_rows
                if row["Measurement"] == "Amplitude (mm)"
            )["n"],
            "1",
        )
        self.assertEqual(set(results["analyses"]), {
            PHASE_ANALYSIS,
            DEVICE_ANALYSIS,
            VARIABILITY_ANALYSIS,
        })
        self.assertEqual(len(results["csv_files"]), 6)
        self.assertTrue(report_exists)
        self.assertTrue(all(range_files_exist))
        self.assertTrue(all(mode_columns_exist))
        profile_config = comparator_config["treatment_profiles"]["Profile A"]
        self.assertEqual(profile_config["range_selection_method"], "p5_p95")
        self.assertEqual(profile_config["warning_thresholds"], None)
        self.assertEqual(profile_config["failure_thresholds"], None)
        self.assertEqual(profile_config["scoring_rules"], None)
        self.assertEqual(profile_config["analysis_weighting"], None)
        self.assertIn(
            "Cycle Start (s)",
            profile_config["enabled_measurements"]["phase_population"],
        )
        expected_plot_folders = {
            PHASE_ANALYSIS: "phase_analysis",
            DEVICE_ANALYSIS: "device_level_analysis",
            VARIABILITY_ANALYSIS: "device_variability_analysis",
        }
        for analysis, folder in expected_plot_folders.items():
            plot_paths = results["analyses"][analysis]["plots"]
            self.assertTrue(plot_paths)
            self.assertTrue(all(Path(path).parent.name == folder for path in plot_paths))
