"""Unit tests for campaign mutate helpers and tools (no live API)."""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

import campaign_edit_tools as cet
import mutate_helpers as mh
import optimization_actions as oa


class TestMutateHelpers(unittest.TestCase):
    def test_currency_micros(self) -> None:
        self.assertEqual(mh.currency_to_micros(100.5), 100_500_000)
        self.assertAlmostEqual(mh.micros_to_currency(100_500_000), 100.5)

    def test_resource_names(self) -> None:
        self.assertEqual(
            mh.campaign_resource_name("1234567890", "99"),
            "customers/1234567890/campaigns/99",
        )

    def test_build_negative_keyword_operations(self) -> None:
        ops, skipped, resource = mh.build_negative_keyword_operations(
            "1234567890",
            ["free trial", "jobs"],
            "campaign",
            "22433487261",
            None,
            "PHRASE",
            set(),
        )
        self.assertEqual(resource, "campaignCriteria")
        self.assertEqual(len(ops), 2)
        self.assertEqual(skipped, [])
        self.assertTrue(ops[0]["create"]["negative"])
        self.assertEqual(ops[0]["create"]["keyword"]["matchType"], "PHRASE")

    def test_build_negative_skips_duplicates(self) -> None:
        existing = {"free trial|PHRASE"}
        ops, skipped, _ = mh.build_negative_keyword_operations(
            "1234567890",
            ["free trial"],
            "campaign",
            "1",
            None,
            "PHRASE",
            existing,
        )
        self.assertEqual(len(ops), 0)
        self.assertEqual(skipped, ["free trial"])

    @patch.object(mh, "make_api_request")
    def test_mutate_raw_success(self, mock_req) -> None:
        mock_req.return_value = ({"results": [{"resourceName": "customers/1/campaigns/2"}]}, None)
        data, err = mh._mutate_raw(
            "1234567890",
            "campaigns",
            [{"updateMask": "status", "update": {"resourceName": "customers/1234567890/campaigns/2", "status": "PAUSED"}}],
        )
        self.assertIsNone(err)
        assert data is not None
        self.assertIn("results", data)
        mock_req.assert_called_once()
        url = mock_req.call_args[0][0]
        self.assertIn("campaigns:mutate", url)


class TestClassifyCampaigns(unittest.TestCase):
    def test_pause_zero_conv(self) -> None:
        campaigns = [
            {
                "campaign_id": "1",
                "name": "Waste",
                "status": "ENABLED",
                "spend": 2000,
                "clicks": 50,
                "conversions": 0,
                "cost_micros": 2_000_000_000,
                "cpa": None,
            }
        ]
        buckets = oa.classify_campaigns(campaigns, blended_cpa=500, min_spend=500, min_clicks_pause=30, max_cpa_multiplier=2)
        self.assertEqual(len(buckets["pause"]), 1)

    def test_reduce_high_cpa(self) -> None:
        campaigns = [
            {
                "campaign_id": "2",
                "name": "High CPA",
                "status": "ENABLED",
                "spend": 3000,
                "clicks": 40,
                "conversions": 2,
                "cost_micros": 3_000_000_000,
                "cpa": 1500,
            }
        ]
        buckets = oa.classify_campaigns(campaigns, blended_cpa=500, min_spend=500, min_clicks_pause=30, max_cpa_multiplier=2)
        self.assertEqual(len(buckets["reduce_budget"]), 1)


class TestCampaignEditToolsValidation(unittest.TestCase):
    def test_update_status_invalid(self) -> None:
        out = asyncio.run(cet.update_campaign_status("1234567890", status="REMOVED", campaign_id="1"))
        self.assertIn("status must be", out)

    def test_add_negatives_empty(self) -> None:
        out = asyncio.run(
            cet.add_negative_keywords(
                customer_id="1234567890",
                keywords=[],
                level="campaign",
                campaign_id="1",
            )
        )
        self.assertIn("cannot be empty", out)

    @patch.object(mh, "resolve_campaign")
    @patch.object(mh, "mutate_campaign_update")
    @patch.object(cet, "get_credentials")
    def test_update_campaign_status_ok(self, _creds, mock_mutate, mock_resolve) -> None:
        mock_resolve.return_value = (
            {"id": "99", "name": "Test", "status": "ENABLED"},
            None,
        )
        mock_mutate.return_value = ({"results": []}, None)
        out = asyncio.run(
            cet.update_campaign_status(
                customer_id="1234567890",
                status="PAUSED",
                campaign_id="99",
            )
        )
        data = json.loads(out)
        self.assertEqual(data["status"], "updated")
        self.assertEqual(data["new_status"], "PAUSED")


class TestOptimizationActions(unittest.TestCase):
    def test_campaign_metrics_aggregate(self) -> None:
        rows = [
            {
                "campaign": {"id": "1", "name": "A", "status": "ENABLED"},
                "metrics": {"clicks": "10", "impressions": "100", "costMicros": "1000000", "conversions": 1},
            },
            {
                "campaign": {"id": "1", "name": "A", "status": "ENABLED"},
                "metrics": {"clicks": "5", "impressions": "50", "costMicros": "500000", "conversions": 0.5},
            },
        ]
        agg = oa._campaign_metrics_from_rows(rows)
        self.assertEqual(len(agg), 1)
        self.assertEqual(agg[0]["clicks"], 15)
        self.assertAlmostEqual(agg[0]["conversions"], 1.5)


if __name__ == "__main__":
    unittest.main()
