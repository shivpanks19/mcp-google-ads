"""Tests for GAQL helpers used by get_campaign_performance (no live Google Ads API)."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import google_ads_server as gas


class TestFormatExecuteGaqlTable(unittest.TestCase):
    def test_pipe_delimited_table(self) -> None:
        rows = [
            {"campaign": {"id": "1", "name": "Alpha"}, "metrics": {"clicks": "10"}},
        ]
        s = gas._format_execute_gaql_table("1234567890", rows)
        self.assertIn("1234567890", s)
        self.assertIn("campaign.id", s)
        self.assertIn("campaign.name", s)
        self.assertIn("Alpha", s)
        self.assertIn("10", s)


class TestFetchCampaignPerformanceTableAndRows(unittest.TestCase):
    def test_error_from_search(self) -> None:
        with patch.object(
            gas,
            "_gaql_search_raw",
            return_value=(None, "Error executing query: boom"),
        ):
            d = asyncio.run(gas.fetch_campaign_performance_table_and_rows("1234567890", 7))
        self.assertFalse(d["ok"])
        self.assertEqual(d["error"], "Error executing query: boom")

    def test_success_with_rows(self) -> None:
        api_rows = [
            {"campaign": {"id": "9", "name": "Z"}, "metrics": {"clicks": "3"}},
        ]
        with patch.object(gas, "_gaql_search_raw", return_value=(api_rows, None)):
            d = asyncio.run(gas.fetch_campaign_performance_table_and_rows("123-456-7890", 14))
        self.assertTrue(d["ok"])
        self.assertEqual(d["formatted_customer_id"], "1234567890")
        self.assertEqual(d["rows"], api_rows)
        self.assertIn("Query Results", d["table"] or "")


class TestShouldPersistCampaignSnapshot(unittest.TestCase):
    def test_explicit_true(self) -> None:
        self.assertTrue(gas._should_persist_campaign_performance_snapshot(True))

    def test_explicit_false_without_env(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("AUTO_PERSIST_CAMPAIGN_PERFORMANCE_SNAPSHOTS", None)
            self.assertFalse(gas._should_persist_campaign_performance_snapshot(False))

    def test_env_one(self) -> None:
        with patch.dict("os.environ", {"AUTO_PERSIST_CAMPAIGN_PERFORMANCE_SNAPSHOTS": "1"}):
            self.assertTrue(gas._should_persist_campaign_performance_snapshot(False))


if __name__ == "__main__":
    unittest.main()
