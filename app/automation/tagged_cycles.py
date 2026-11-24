"""Counts for cycles tagged by alternating top and bottom extrema."""


def tagged_cycle_count(extrema, start_s, end_s):
    """Count paired top/bottom extrema inside an inclusive time range."""
    in_range = [
        row
        for row in extrema
        if start_s <= row["time_s"] <= end_s
        and row["label"] in ("top", "bottom")
    ]
    top_count = sum(row["label"] == "top" for row in in_range)
    bottom_count = sum(row["label"] == "bottom" for row in in_range)
    return min(top_count, bottom_count)
