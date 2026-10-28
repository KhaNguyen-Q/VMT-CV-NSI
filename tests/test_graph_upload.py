import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.graph.client import append_rows
from app.graph.transformer import DataMappingError, build_graph_payload
from app.windows.NSIReportformat import save_report_json


class GraphTransformerTests(unittest.TestCase):
    def setUp(self):
        self.report = {
            "excel_table": {
                "columns": ["Phase", "Measurement", "Baseline", "Comparison"],
                "graph_payload": {
                    "values": [
                        ["Phase 1", "Amplitude", "1.25", "1.50"],
                        ["Phase 2", "Cycle count", "4", "5"],
                    ]
                },
            }
        }
        self.mapping = {
            "columns": [
                {"source": "Phase", "target": "Test Phase"},
                {
                    "source": "Baseline",
                    "target": "Reference",
                    "type": "number",
                },
                {
                    "source": "Comparison",
                    "target": "Observed",
                    "type": "number",
                },
            ]
        }

    def test_builds_mapped_excel_values_and_casts_numbers(self):
        payload = build_graph_payload(self.report, self.mapping)

        self.assertEqual(payload["columns"], ["Test Phase", "Reference", "Observed"])
        self.assertEqual(
            payload["values"],
            [["Phase 1", 1.25, 1.5], ["Phase 2", 4.0, 5.0]],
        )

    def test_rejects_missing_mapped_source(self):
        self.mapping["columns"][0]["source"] = "Not in report"

        with self.assertRaises(DataMappingError):
            build_graph_payload(self.report, self.mapping)

    def test_rejects_non_numeric_values_when_mapping_requests_number(self):
        self.report["excel_table"]["graph_payload"]["values"][0][2] = "1.2 mm"

        with self.assertRaisesRegex(DataMappingError, "expected a number"):
            build_graph_payload(self.report, self.mapping)

    def test_exported_report_json_maps_to_graph_values(self):
        metadata = {
            "Camera": "Cam 1",
            "FPS": "60",
            "Treatment profile": "A",
            "Device number": "D-1",
            "Client Name": "Client",
        }
        sections = [
            (
                "Phase 1",
                [("Mean amplitude", "1.250 mm", "1.500 mm")],
                [],
                None,
                None,
                None,
            )
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.json"
            save_report_json(
                report_path,
                "baseline.csv",
                "comparison.csv",
                0.1,
                metadata,
                "PASS",
                [],
                "Summary",
                sections,
            )
            with report_path.open("r", encoding="utf-8") as handle:
                report = json.load(handle)
            mapping_path = Path(__file__).resolve().parents[1] / "mapping.example.json"
            with mapping_path.open("r", encoding="utf-8") as handle:
                mapping = json.load(handle)

        payload = build_graph_payload(report, mapping)
        self.assertEqual(len(payload["values"]), 1)
        self.assertEqual(
            payload["values"][0][:4],
            [
                "Phase 1",
                "Mean amplitude",
                "1.250 mm",
                "1.500 mm",
            ],
        )
        self.assertEqual(payload["values"][0][4:], list(metadata.values()))


class GraphClientTests(unittest.TestCase):
    def test_retries_conflict_and_posts_values_to_excel_table(self):
        class Response:
            def __init__(self, status_code, headers=None):
                self.status_code = status_code
                self.headers = headers or {}
                self.text = ""
                self.content = b""
                self.ok = status_code < 400

        class Session:
            def __init__(self):
                self.responses = [Response(409), Response(201)]
                self.calls = []

            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return self.responses.pop(0)

        class Config:
            sharepoint_site_id = "tenant,site,web"
            file_path = "Shared Documents/Reports.xlsx"
            table_name = "Report Table"

        session = Session()
        with patch("app.graph.client.time.sleep") as sleep:
            result = append_rows(
                Config(),
                "test-token",
                {"columns": ["Phase"], "values": [["Phase 1"]]},
                session=session,
            )

        self.assertEqual(result, {})
        self.assertEqual(len(session.calls), 2)
        url, request = session.calls[-1]
        self.assertEqual(
            url,
            "https://graph.microsoft.com/v1.0/sites/tenant,site,web/"
            "drive/root:/Shared%20Documents/Reports.xlsx:/workbook/tables/"
            "Report%20Table/rows/add",
        )
        self.assertEqual(request["json"], {"values": [["Phase 1"]]})
        self.assertEqual(request["headers"]["Authorization"], "Bearer test-token")
        sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
