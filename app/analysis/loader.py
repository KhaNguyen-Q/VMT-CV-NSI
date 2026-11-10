"""Load and validate CSV files produced by the range export."""

import csv
import math
import os

import pandas as pd

from windows.range_csv_export import RANGE_EXPORT_COLUMNS


NUMERIC_COLUMNS = (
    "Duration (s)",
    "High Peak (mm)",
    "Low Peak (mm)",
    "Peak-to-Peak (mm)",
    "Amplitude (mm)",
    "Cycle Period (s)",
    "Frequency (Hz)",
    "Cycle Count",
)
REQUIRED_COLUMNS = (
    "Vibration",
    "Phase",
    "Record Type",
    "Device number",
    "Treatment profile",
    *NUMERIC_COLUMNS,
)


def load_range_csvs(paths):
    """Return all valid range-export rows and a per-file ingestion report."""
    records = []
    file_reports = []

    for path in paths:
        path = os.fspath(path)
        report = {
            "File": os.path.basename(path),
            "Summary rows loaded": 0,
            "Cycle-detail rows loaded": 0,
            "Rows skipped": 0,
            "Numeric values missing or invalid": 0,
            "Status": "Loaded",
        }
        try:
            with open(path, newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                fieldnames = reader.fieldnames or []
                missing_columns = [
                    column for column in REQUIRED_COLUMNS if column not in fieldnames
                ]
                if missing_columns:
                    report["Status"] = (
                        "Skipped: missing columns " + ", ".join(missing_columns)
                    )
                    file_reports.append(report)
                    continue

                for row in reader:
                    record_type = (row.get("Record Type") or "").strip()
                    is_summary = record_type.casefold() == "phase summary"
                    is_detail = record_type.casefold() == "cycle detail"
                    if not is_summary and not is_detail:
                        report["Rows skipped"] += 1
                        continue

                    normalized = {
                        column: (row.get(column) or "").strip()
                        for column in RANGE_EXPORT_COLUMNS
                    }
                    if not all(
                        normalized[column]
                        for column in (
                            "Vibration",
                            "Phase",
                            "Device number",
                            "Treatment profile",
                        )
                    ):
                        report["Rows skipped"] += 1
                        continue

                    if is_summary:
                        for column in NUMERIC_COLUMNS:
                            value = normalized[column]
                            try:
                                number = float(value) if value else math.nan
                                valid = math.isfinite(number)
                            except ValueError:
                                valid = False
                            if not valid:
                                report["Numeric values missing or invalid"] += 1
                        report["Summary rows loaded"] += 1
                    else:
                        report["Cycle-detail rows loaded"] += 1

                    normalized["Source File"] = os.path.basename(path)
                    records.append(normalized)
        except (OSError, csv.Error, UnicodeError) as exc:
            report["Status"] = f"Failed: {exc}"
        file_reports.append(report)

    if not any(row["Record Type"].casefold() == "phase summary" for row in records):
        raise ValueError(
            "No valid Phase Summary rows were found. Check the selected files "
            "and their exported range CSV format."
        )

    return (
        pd.DataFrame(records, columns=(*RANGE_EXPORT_COLUMNS, "Source File")),
        pd.DataFrame(file_reports),
    )
