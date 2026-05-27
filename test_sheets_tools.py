"""Unit tests for Google Sheets tools (no live API unless env is set)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import sheets_tools as st


class TestEmailLeadsAnalysis(unittest.TestCase):
    def setUp(self) -> None:
        self.headers = ["Status", "UTM", "Campaigns", "Month", "NAME"]
        self.rows = [
            [
                "Won",
                "https://example.com/?utm_source=google&utm_medium=cpc&utm_campaign=Search+Ads+EB&utm_term=smart+board",
                "",
                "May 2025",
                "Alice",
            ],
            ["NQ", "no UTM Link", "Hexa Search Ads", "May 2025", "Bob"],
            ["Cold", "", "Fallback Campaign", "June 2025", "Carol"],
            ["NA", "https://example.com/?utm_source=YT&utm_campaign=YT-Demand_Gen", "", "May 2025", "Dan"],
        ]

    def test_normalize_status(self) -> None:
        self.assertEqual(st.normalize_status("won"), "Won")
        self.assertEqual(st.normalize_status("N/A"), "NA")
        self.assertEqual(st.normalize_status(""), "Blank")

    def test_parse_utm(self) -> None:
        params = st._parse_utm_params(
            "https://example.com/?utm_source=google&utm_campaign=Search+Ads+EB&utm_term=teams+room"
        )
        self.assertEqual(params["utm_source"], "google")
        self.assertEqual(params["utm_campaign"], "Search Ads EB")
        self.assertEqual(params["utm_term"], "teams room")

    def test_analyze_rows_with_month_filter(self) -> None:
        report = st.analyze_email_leads_rows(self.headers, self.rows, month_filter="May 2025")
        self.assertEqual(report["totalLeads"], 3)
        self.assertEqual(report["attributed"], 2)
        self.assertEqual(report["overallByStatus"]["Won"], 1)
        campaigns = {r["name"]: r for r in report["byCampaign"]}
        self.assertEqual(campaigns["Search Ads EB"]["Won"], 1)
        self.assertEqual(campaigns["(Unattributed)"]["NQ"], 1)

    def test_search_term_rollup(self) -> None:
        report = st.analyze_email_leads_rows(self.headers, self.rows, month_filter="May 2025")
        terms = {r["name"]: r for r in report["bySearchTerm"]}
        self.assertIn("smart board", terms)
        self.assertEqual(terms["smart board"]["Total"], 1)


class TestSheetRange(unittest.TestCase):
    def test_sheet_range_escapes_quotes(self) -> None:
        self.assertEqual(st._sheet_range_a1("E-mail leads"), "'E-mail leads'!A:ZZ")
        self.assertEqual(st._sheet_range_a1("Bob's Tab", "A1:B2"), "'Bob''s Tab'!A1:B2")


class TestReadSheetValuesMock(unittest.TestCase):
    def setUp(self) -> None:
        st.reset_sheets_caches()

    @patch("sheets_tools.create_sheets_service")
    def test_read_sheet_values(self, mock_create: MagicMock) -> None:
        mock_service = MagicMock()
        mock_create.return_value = mock_service
        mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": [["A", "B"], ["1", "2"]]
        }
        out = st.read_sheet_values("sheet-id", "'Tab'!A:B")
        self.assertEqual(out, [["A", "B"], ["1", "2"]])


@unittest.skipUnless(
    os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID") and os.environ.get("GOOGLE_ADS_CREDENTIALS_JSON"),
    "Live Sheets test requires GOOGLE_SHEETS_SPREADSHEET_ID and GOOGLE_ADS_CREDENTIALS_JSON",
)
class TestLiveSheetsIntegration(unittest.TestCase):
    def setUp(self) -> None:
        st.reset_sheets_caches()

    def test_list_tabs_smoke(self) -> None:
        sid = os.environ["GOOGLE_SHEETS_SPREADSHEET_ID"]
        tabs = st.list_spreadsheet_tabs(sid)
        self.assertIsInstance(tabs, list)
        self.assertTrue(len(tabs) > 0)


class TestWriteHelpers(unittest.TestCase):
    def setUp(self) -> None:
        st.reset_sheets_caches()

    @patch("sheets_tools.create_sheets_service")
    def test_write_sheet_values(self, mock_create: MagicMock) -> None:
        mock_service = MagicMock()
        mock_create.return_value = mock_service
        mock_service.spreadsheets.return_value.values.return_value.update.return_value.execute.return_value = {
            "updatedRange": "'Tab'!A1:B2",
            "updatedRows": 2,
            "updatedColumns": 2,
            "updatedCells": 4,
        }
        out = st.write_sheet_values("sid", "'Tab'!A1", [["H1", "H2"], [1, 2]])
        self.assertEqual(out["updatedCells"], 4)

    @patch("sheets_tools.list_spreadsheet_tabs", return_value=["Existing"])
    @patch("sheets_tools.create_sheets_service")
    def test_create_sheet_tab_skips_existing(self, mock_create: MagicMock, _mock_tabs: MagicMock) -> None:
        created = st.create_sheet_tab("sid", "Existing")
        self.assertFalse(created)
        mock_create.return_value.spreadsheets.return_value.batchUpdate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
