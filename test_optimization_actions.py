"""Unit tests for optimization_actions orchestration (mocked API)."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import optimization_actions as oa
import mutate_helpers as mh


class TestApplyWeeklyPerformanceActions(unittest.TestCase):
    @patch.object(oa, "_audit_memory", new_callable=AsyncMock)
    @patch.object(oa, "get_credentials")
    @patch.object(mh, "_mutate_raw")
    @patch.object(mh, "fetch_account_currency_code", return_value="INR")
    @patch("optimization_actions.fetch_campaign_performance_table_and_rows", new_callable=AsyncMock)
    def test_pauses_waste_campaign(self, mock_perf, _curr, mock_mutate, _creds, mock_audit) -> None:
        mock_perf.return_value = {
            "ok": True,
            "rows": [
                {
                    "campaign": {"id": "111", "name": "Waste Camp", "status": "ENABLED"},
                    "metrics": {
                        "clicks": "40",
                        "impressions": "400",
                        "costMicros": "600000000",
                        "conversions": 0,
                    },
                }
            ],
        }
        mock_mutate.return_value = ({"results": [{}]}, None)
        mock_audit.return_value = None

        out = asyncio.run(
            oa.apply_weekly_performance_actions(
                customer_id="2696255703",
                days=7,
                pause_zero_conversion_spenders=True,
                min_clicks_to_pause=30,
                min_spend_inr=100,
                save_audit_to_supabase=False,
            )
        )
        self.assertIn("Weekly performance actions", out)
        self.assertIn("pause", out.lower())
        mock_mutate.assert_called()


class TestAnalyzeAndApply(unittest.TestCase):
    @patch.object(oa, "_audit_memory", new_callable=AsyncMock)
    @patch.object(oa, "get_credentials")
    @patch.object(mh, "mutate_campaign_update")
    def test_pause_action(self, mock_mutate, _creds, mock_audit) -> None:
        mock_mutate.return_value = ({"results": []}, None)
        mock_audit.return_value = None
        out = asyncio.run(
            oa.analyze_and_apply_campaign_edits(
                customer_id="2696255703",
                actions=[{"type": "pause", "campaign_id": "111"}],
                save_audit_to_supabase=False,
            )
        )
        self.assertIn("111", out)
        mock_mutate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
