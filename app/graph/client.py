"""Microsoft Graph Excel table append client."""

import time
from urllib.parse import quote

import requests


class GraphRequestError(RuntimeError):
    """Raised when a Graph API request fails."""


_RETRYABLE_STATUSES = {409, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 4
_REQUEST_TIMEOUT_SECONDS = 30


def _table_rows_endpoint(config):
    site_id = quote(config.sharepoint_site_id, safe=",")
    file_path = quote(config.file_path, safe="/")
    table_name = quote(config.table_name, safe="")
    return (
        "https://graph.microsoft.com/v1.0/sites/"
        f"{site_id}/drive/root:/{file_path}:/workbook/tables/"
        f"{table_name}/rows/add"
    )


def append_rows(config, access_token, payload, session=None):
    if session is None:
        session = requests
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    request_payload = {"values": payload["values"]}
    endpoint = _table_rows_endpoint(config)

    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = session.post(
                endpoint,
                headers=headers,
                json=request_payload,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise GraphRequestError(f"Graph API request failed: {exc}") from exc

        if response.status_code not in _RETRYABLE_STATUSES:
            if not response.ok:
                raise GraphRequestError(
                    "Graph API request failed with status "
                    f"{response.status_code}: {response.text}"
                )
            return response.json() if response.content else {}

        if attempt + 1 == _MAX_ATTEMPTS:
            raise GraphRequestError(
                "Graph API request failed after "
                f"{_MAX_ATTEMPTS} attempts with status "
                f"{response.status_code}: {response.text}"
            )

        retry_after = response.headers.get("Retry-After")
        try:
            delay = min(max(float(retry_after), 0.0), 30.0)
        except (TypeError, ValueError):
            delay = min(2**attempt, 30)
        time.sleep(delay)

    raise GraphRequestError("Graph API request ended without a response.")
