"""Loading and validating external Graph API configuration."""

import json
from dataclasses import dataclass

from .paths import resolve_app_path


class GraphConfigurationError(ValueError):
    """Raised when Graph API configuration is missing or invalid."""


@dataclass(frozen=True)
class GraphConfig:
    tenant_id: str
    client_id: str
    sharepoint_site_id: str
    file_path: str
    table_name: str


def load_graph_config(path=None):
    config_path = resolve_app_path(path or "config.json")
    with config_path.open("r", encoding="utf-8") as handle:
        config_data = json.load(handle)
    if not isinstance(config_data, dict):
        raise GraphConfigurationError("Graph configuration must be a JSON object.")

    required_fields = (
        "tenant_id",
        "client_id",
        "sharepoint_site_id",
        "file_path",
        "table_name",
    )
    values = {}
    for field in required_fields:
        value = config_data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise GraphConfigurationError(
                f"Set a non-empty {field!r} in {config_path}."
            )
        values[field] = value.strip()
        if values[field].startswith("YOUR_"):
            raise GraphConfigurationError(
                f"Replace the placeholder for {field!r} in {config_path}."
            )

    file_path = values["file_path"].replace("\\", "/")
    if file_path.startswith("/") or ".." in file_path.split("/"):
        raise GraphConfigurationError(
            "'file_path' must be a path relative to the SharePoint drive."
        )
    values["file_path"] = file_path
    return GraphConfig(**values)
