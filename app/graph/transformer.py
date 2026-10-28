"""Map exported report rows to the configured Excel table schema."""

import math


class DataMappingError(ValueError):
    """Raised when report data cannot be mapped to the Excel table schema."""


_SUPPORTED_TYPES = {"string", "integer", "number", "boolean"}


def _cast_value(value, value_type, row_number, source):
    if value is None or value == "":
        return value

    if value_type == "string":
        return str(value)
    if value_type == "integer":
        if isinstance(value, bool):
            raise DataMappingError(
                f"Row {row_number}, field {source!r}: expected an integer."
            )
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise DataMappingError(
                f"Row {row_number}, field {source!r}: expected an integer."
            ) from exc
    if value_type == "number":
        if isinstance(value, bool):
            raise DataMappingError(
                f"Row {row_number}, field {source!r}: expected a number."
            )
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise DataMappingError(
                f"Row {row_number}, field {source!r}: expected a number."
            ) from exc
        if not math.isfinite(number):
            raise DataMappingError(
                f"Row {row_number}, field {source!r}: number must be finite."
            )
        return number
    if value_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise DataMappingError(
            f"Row {row_number}, field {source!r}: expected true or false."
        )
    raise DataMappingError(f"Unsupported field type: {value_type!r}.")


def build_graph_payload(report, mapping):
    if not isinstance(report, dict):
        raise DataMappingError("Report JSON must be an object.")
    excel_table = report.get("excel_table")
    if not isinstance(excel_table, dict):
        raise DataMappingError("Report JSON is missing its 'excel_table' object.")
    source_columns = excel_table.get("columns")
    graph_payload = excel_table.get("graph_payload")
    if not isinstance(source_columns, list) or not all(
        isinstance(column, str) for column in source_columns
    ):
        raise DataMappingError("'excel_table.columns' must be a list of strings.")
    if len(set(source_columns)) != len(source_columns):
        raise DataMappingError("'excel_table.columns' cannot contain duplicates.")
    if not isinstance(graph_payload, dict):
        raise DataMappingError("'excel_table.graph_payload' must be an object.")
    source_rows = graph_payload.get("values")
    if not isinstance(source_rows, list):
        raise DataMappingError("'excel_table.graph_payload.values' must be a list.")

    mapping_columns = mapping.get("columns") if isinstance(mapping, dict) else None
    if not isinstance(mapping_columns, list) or not mapping_columns:
        raise DataMappingError("Mapping configuration must have a non-empty 'columns' list.")

    source_indexes = {column: index for index, column in enumerate(source_columns)}
    target_columns = []
    selections = []
    for entry in mapping_columns:
        if not isinstance(entry, dict):
            raise DataMappingError("Each mapping column must be a JSON object.")
        source = entry.get("source")
        target = entry.get("target")
        value_type = entry.get("type", "string")
        if not isinstance(source, str) or source not in source_indexes:
            raise DataMappingError(f"Mapped source column {source!r} is not in the report.")
        if not isinstance(target, str) or not target.strip():
            raise DataMappingError(f"Mapping for {source!r} needs a non-empty target.")
        target = target.strip()
        if target in target_columns:
            raise DataMappingError(f"Duplicate target column {target!r} in mapping.")
        if not isinstance(value_type, str) or value_type not in _SUPPORTED_TYPES:
            raise DataMappingError(
                f"Unsupported type {value_type!r} for source column {source!r}."
            )
        target_columns.append(target)
        selections.append((source, source_indexes[source], value_type))

    mapped_rows = []
    for row_number, row in enumerate(source_rows, start=1):
        if not isinstance(row, list) or len(row) != len(source_columns):
            raise DataMappingError(
                f"Report row {row_number} does not match the source column count."
            )
        mapped_rows.append(
            [
                _cast_value(row[index], value_type, row_number, source)
                for source, index, value_type in selections
            ]
        )
    if not mapped_rows:
        raise DataMappingError("The report contains no rows to upload.")

    return {"columns": target_columns, "values": mapped_rows}
