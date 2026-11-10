"""Formatting and calculations for the comparison window's range CSV export."""

import csv
import os

import numpy as np


_PX_TO_MM = 0.1

RANGE_EXPORT_COLUMNS = (
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


def available_phases(ranges):
    """Return unique range labels in phase order for the export prompt."""
    phase_names = []
    seen_phases = set()
    for phase in ranges:
        label = (phase["label"] or "Unlabeled phase").strip()
        key = label.casefold()
        if key not in seen_phases:
            phase_names.append(label)
            seen_phases.add(key)

    canonical_order = {"phase 1": 0, "phase 2": 1, "phase 3": 2}
    phase_names.sort(
        key=lambda label: (
            canonical_order.get(label.casefold(), 3),
            label.casefold(),
        )
    )
    return phase_names


def ranges_for_phase(ranges, selected_phase):
    if selected_phase == "All phases":
        return list(ranges)
    return [
        phase
        for phase in ranges
        if (phase["label"] or "Unlabeled phase").strip().casefold()
        == selected_phase.casefold()
    ]


def tagged_cycle_count(extrema, start_s, end_s):
    """Match the comparison table's count of paired top/bottom extrema."""
    in_range = [
        row
        for row in extrema
        if start_s <= row["time_s"] <= end_s
        and row["label"] in ("top", "bottom")
    ]
    top_count = sum(row["label"] == "top" for row in in_range)
    bottom_count = sum(row["label"] == "bottom" for row in in_range)
    return min(top_count, bottom_count)


def _baseline_position_mm(series, time_s):
    times = series["time_s"]
    mm = series["mm"]
    mm_mask = np.isfinite(times) & np.isfinite(mm)
    if np.any(mm_mask):
        return float(np.interp(time_s, times[mm_mask], mm[mm_mask]))

    px = series["px"]
    px_mask = np.isfinite(times) & np.isfinite(px)
    if np.any(px_mask):
        return float(np.interp(time_s, times[px_mask], px[px_mask]) * _PX_TO_MM)
    return None


def _range_cycle_records(extrema, start_s, end_s, baseline):
    range_extrema = [
        row
        for row in extrema
        if start_s <= row["time_s"] <= end_s
        and row["label"] in ("top", "bottom")
    ]
    tops = [row for row in range_extrema if row["label"] == "top"]
    bottoms = [row for row in range_extrema if row["label"] == "bottom"]
    cycles = []
    for start, end in zip(tops, tops[1:]):
        period = end["time_s"] - start["time_s"]
        if period <= 0:
            continue
        cycle_bottoms = [
            row
            for row in bottoms
            if start["time_s"] <= row["time_s"] <= end["time_s"]
        ]
        if not cycle_bottoms:
            continue

        high_peak = _baseline_position_mm(baseline, start["time_s"])
        low_candidates = [
            value
            for row in cycle_bottoms
            if (value := _baseline_position_mm(baseline, row["time_s"]))
            is not None
        ]
        low_peak = min(low_candidates) if low_candidates else None
        peak_to_peak = (
            high_peak - low_peak
            if high_peak is not None and low_peak is not None
            else None
        )
        cycles.append(
            {
                "start_s": start["time_s"],
                "end_s": end["time_s"],
                "duration_s": period,
                "high_peak_mm": high_peak,
                "low_peak_mm": low_peak,
                "peak_to_peak_mm": peak_to_peak,
                "amplitude_mm": peak_to_peak / 2 if peak_to_peak is not None else None,
                "period_s": period,
                "frequency_hz": 1 / period,
            }
        )

    expected_count = tagged_cycle_count(extrema, start_s, end_s)
    cycle_starts = {cycle["start_s"] for cycle in cycles}
    for start in tops:
        if len(cycles) >= expected_count:
            break
        if start["time_s"] in cycle_starts or start["time_s"] >= end_s:
            continue
        high_peak = _baseline_position_mm(baseline, start["time_s"])
        low_candidates = [
            value
            for row in bottoms
            if start["time_s"] <= row["time_s"] <= end_s
            if (value := _baseline_position_mm(baseline, row["time_s"]))
            is not None
        ]
        low_peak = min(low_candidates) if low_candidates else None
        peak_to_peak = (
            high_peak - low_peak
            if high_peak is not None and low_peak is not None
            else None
        )
        cycles.append(
            {
                "start_s": start["time_s"],
                "end_s": end_s,
                "duration_s": end_s - start["time_s"],
                "high_peak_mm": high_peak,
                "low_peak_mm": low_peak,
                "peak_to_peak_mm": peak_to_peak,
                "amplitude_mm": peak_to_peak / 2 if peak_to_peak is not None else None,
                "period_s": None,
                "frequency_hz": None,
            }
        )
        cycle_starts.add(start["time_s"])
    return cycles


def _mean(cycles, key):
    values = [cycle[key] for cycle in cycles if cycle[key] is not None]
    return float(np.mean(values)) if values else None


def _format_row(row):
    return [
        f"{value:.3f}" if isinstance(value, float) else value
        for value in (row.get(column, "") for column in RANGE_EXPORT_COLUMNS)
    ]


def write_range_export_csv(path, ranges, extrema, baseline, metadata):
    """Write summary and cycle-detail rows, returning the total cycle count."""
    metadata_values = {
        "Client name": metadata.get("Client Name", ""),
        "Device number": metadata.get("Device number", ""),
        "Date": metadata.get("Date", ""),
        "Camera": metadata.get("Camera", ""),
        "FPS": metadata.get("FPS", ""),
        "Treatment profile": metadata.get("Treatment profile", ""),
    }

    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    total_cycles = 0
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(RANGE_EXPORT_COLUMNS)
        for vibration, phase in enumerate(ranges, start=1):
            cycles = _range_cycle_records(
                extrema, phase["start_s"], phase["end_s"], baseline
            )
            total_cycles += len(cycles)
            vibration_name = f"Vibration {vibration}"
            phase_label = phase["label"] or "Unlabeled phase"
            summary = {
                "Vibration": vibration_name,
                "Phase": phase_label,
                "Record Type": "Phase Summary",
                "Cycle #": "Total cycles",
                "Cycle Start (s)": f"START ({phase['start_s']:.3f})",
                "Cycle End (s)": f"END ({phase['end_s']:.3f})",
                "Duration (s)": sum(cycle["duration_s"] for cycle in cycles),
                "High Peak (mm)": _mean(cycles, "high_peak_mm"),
                "Low Peak (mm)": _mean(cycles, "low_peak_mm"),
                "Peak-to-Peak (mm)": _mean(cycles, "peak_to_peak_mm"),
                "Amplitude (mm)": _mean(cycles, "amplitude_mm"),
                "Cycle Period (s)": _mean(cycles, "period_s"),
                "Frequency (Hz)": _mean(cycles, "frequency_hz"),
                "Cycle Count": len(cycles),
                **metadata_values,
            }
            writer.writerow(_format_row(summary))

            for cycle_number, cycle in enumerate(cycles, start=1):
                detail = {
                    "Vibration": vibration_name,
                    "Phase": phase_label,
                    "Record Type": "Cycle Detail",
                    "Cycle #": cycle_number,
                    "Cycle Start (s)": cycle["start_s"],
                    "Cycle End (s)": cycle["end_s"],
                    "Duration (s)": cycle["duration_s"],
                    "High Peak (mm)": cycle["high_peak_mm"],
                    "Low Peak (mm)": cycle["low_peak_mm"],
                    "Peak-to-Peak (mm)": cycle["peak_to_peak_mm"],
                    "Amplitude (mm)": cycle["amplitude_mm"],
                    "Cycle Period (s)": cycle["period_s"],
                    "Frequency (Hz)": cycle["frequency_hz"],
                    "Cycle Count": len(cycles),
                    **metadata_values,
                }
                writer.writerow(_format_row(detail))
    return total_cycles
