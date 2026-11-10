import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from analysis.loader import load_range_csvs
from analysis.runner import (
    DEVICE_ANALYSIS,
    PHASE_ANALYSIS,
    VARIABILITY_ANALYSIS,
    run_all_analyses,
    run_analysis,
)
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
            row for row in rows if row["Measurement"] == "Amplitude (mm)"
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
            with (output / "acceptance_range_candidates.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                ranges = list(csv.DictReader(handle))
            with (output / "device_level_statistics.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                device_rows = list(csv.DictReader(handle))

        amplitude = next(
            row for row in rows if row["Measurement"] == "Amplitude (mm)"
        )
        device_amplitudes = [
            row for row in device_rows if row["Measurement"] == "Amplitude (mm)"
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

            with (output / "master_dataset.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                master_rows = list(csv.DictReader(handle))
            with (output / "phase_population_statistics.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                phase_rows = list(csv.DictReader(handle))
            report_exists = (output / "input_files.csv").is_file()

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
        self.assertEqual(len(results["csv_files"]), 4)
        self.assertTrue(report_exists)
        expected_plot_folders = {
            PHASE_ANALYSIS: "phase_analysis",
            DEVICE_ANALYSIS: "device_level_analysis",
            VARIABILITY_ANALYSIS: "device_variability_analysis",
        }
        for analysis, folder in expected_plot_folders.items():
            plot_paths = results["analyses"][analysis]["plots"]
            self.assertTrue(plot_paths)
            self.assertTrue(all(Path(path).parent.name == folder for path in plot_paths))
