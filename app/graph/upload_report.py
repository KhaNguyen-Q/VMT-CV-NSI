"""Command-line orchestration for appending an exported report to SharePoint."""

import argparse
import json
import sys
from pathlib import Path

from .auth import GraphAuthenticationError, GraphAuthenticator
from .client import GraphRequestError, append_rows
from .config import GraphConfigurationError, load_graph_config
from .paths import resolve_app_path
from .transformer import DataMappingError, build_graph_payload


def _load_json(path, description):
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{description} is not valid JSON: {exc}") from exc


def upload_report(report_path, config_path=None, mapping_path=None):
    config = load_graph_config(config_path)
    mapping_file = resolve_app_path(mapping_path or "mapping.json")
    report = _load_json(report_path, "Report file")
    mapping = _load_json(mapping_file, "Mapping file")
    payload = build_graph_payload(report, mapping)
    access_token = GraphAuthenticator(config).get_access_token()
    result = append_rows(config, access_token, payload)
    return len(payload["values"]), result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Append an exported NSI report to a SharePoint Excel table."
    )
    parser.add_argument("report", help="Path to the JSON file created by report export.")
    parser.add_argument(
        "--config",
        help="External Graph config JSON (default: config.json beside the app).",
    )
    parser.add_argument(
        "--mapping",
        help="Source-to-Excel column mapping JSON (default: mapping.json beside the app).",
    )
    args = parser.parse_args(argv)

    try:
        row_count, _result = upload_report(args.report, args.config, args.mapping)
    except (
        GraphAuthenticationError,
        GraphConfigurationError,
        GraphRequestError,
        DataMappingError,
        OSError,
        ValueError,
    ) as exc:
        print(f"SharePoint upload failed: {exc}", file=sys.stderr)
        return 1
    print(f"Uploaded {row_count} report rows to the configured Excel table.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
