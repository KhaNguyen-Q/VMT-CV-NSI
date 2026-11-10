import csv
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from windows.range_csv_export import (
    available_phases,
    ranges_for_phase,
    tagged_cycle_count,
    write_range_export_csv,
)


class RangeExportTests(unittest.TestCase):
    def setUp(self):
        self.baseline = {
            "time_s": np.asarray([0, 1, 2, 3, 4], dtype=float),
            "px": np.asarray([30, -10, 50, -30, 70], dtype=float),
            "mm": np.asarray([3, -1, 5, -3, 7], dtype=float),
        }
        self.extrema = [
            {"time_s": 0.0, "label": "top"},
            {"time_s": 1.0, "label": "bottom"},
            {"time_s": 2.0, "label": "top"},
            {"time_s": 3.0, "label": "bottom"},
            {"time_s": 4.0, "label": "top"},
        ]
        self.ranges = [
            {
                "label": "Phase 1",
                "start_s": 0.0,
                "end_s": 4.0,
            }
        ]
        self.metadata = {
            "Client Name": "Test Client",
            "Device number": "D-42",
            "Date": "2026-11-05",
            "Camera": "Camera A",
            "FPS": "60",
            "Treatment profile": "Profile X",
        }

    def test_writes_summary_means_and_cycle_details(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ranges.csv"
            cycle_count = write_range_export_csv(
                path, self.ranges, self.extrema, self.baseline, self.metadata
            )
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(cycle_count, 2)
        self.assertEqual(len(rows), 3)
        summary, first_cycle, second_cycle = rows
        self.assertEqual(summary["Record Type"], "Phase Summary")
        self.assertEqual(summary["Cycle Count"], "2")
        self.assertEqual(summary["Duration (s)"], "4.000")
        self.assertEqual(summary["High Peak (mm)"], "4.000")
        self.assertEqual(summary["Low Peak (mm)"], "-2.000")
        self.assertEqual(summary["Peak-to-Peak (mm)"], "6.000")
        self.assertEqual(summary["Amplitude (mm)"], "3.000")
        self.assertEqual(summary["Cycle Period (s)"], "2.000")
        self.assertEqual(summary["Frequency (Hz)"], "0.500")
        self.assertEqual(summary["Client name"], "Test Client")
        self.assertEqual(summary["Date"], "2026-11-05")
        self.assertEqual(first_cycle["Cycle #"], "1")
        self.assertEqual(first_cycle["High Peak (mm)"], "3.000")
        self.assertEqual(first_cycle["Low Peak (mm)"], "-1.000")
        self.assertEqual(second_cycle["Peak-to-Peak (mm)"], "8.000")
        self.assertEqual(first_cycle["Device number"], "D-42")

    def test_converts_pixel_positions_to_millimeters_when_missing(self):
        baseline = dict(self.baseline)
        baseline["mm"] = np.full(5, np.nan)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ranges.csv"
            write_range_export_csv(
                path, self.ranges, self.extrema, baseline, self.metadata
            )
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(rows[1]["High Peak (mm)"], "3.000")
        self.assertEqual(rows[1]["Low Peak (mm)"], "-1.000")

    def test_exports_the_trailing_tagged_cycle_to_the_range_end(self):
        extrema = [
            {"time_s": 0.0, "label": "top"},
            {"time_s": 1.0, "label": "bottom"},
            {"time_s": 2.0, "label": "top"},
            {"time_s": 3.0, "label": "bottom"},
            {"time_s": 4.0, "label": "top"},
            {"time_s": 5.0, "label": "bottom"},
        ]
        phase = [{"label": "Phase 1", "start_s": 0.0, "end_s": 6.0}]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ranges.csv"
            cycle_count = write_range_export_csv(
                path, phase, extrema, self.baseline, self.metadata
            )
            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        cycle_rows = [row for row in rows if row["Record Type"] == "Cycle Detail"]
        self.assertEqual(tagged_cycle_count(extrema, 0.0, 6.0), 3)
        self.assertEqual(cycle_count, 3)
        self.assertEqual(rows[0]["Cycle Count"], "3")
        self.assertEqual(len(cycle_rows), 3)
        self.assertEqual(cycle_rows[-1]["Cycle Start (s)"], "4.000")
        self.assertEqual(cycle_rows[-1]["Cycle End (s)"], "6.000")
        self.assertEqual(cycle_rows[-1]["Cycle Period (s)"], "")
        self.assertEqual(cycle_rows[-1]["Frequency (Hz)"], "")

    def test_phase_selection_helpers_keep_labels_ordered_and_filter_ranges(self):
        ranges = [
            {"label": "Phase 3"},
            {"label": "Phase 1"},
            {"label": "phase 1"},
            {"label": "Custom"},
        ]

        self.assertEqual(
            available_phases(ranges),
            ["Phase 1", "Phase 3", "Custom"],
        )
        self.assertEqual(ranges_for_phase(ranges, "Phase 1"), ranges[1:3])
        self.assertEqual(ranges_for_phase(ranges, "All phases"), ranges)


if __name__ == "__main__":
    unittest.main()
