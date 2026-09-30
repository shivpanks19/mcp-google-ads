"""Unit tests for auction insight GAQL builder (no live API)."""

from __future__ import annotations

import unittest

import auction_insight_tools as ait


class TestAuctionInsightGaql(unittest.TestCase):
    def test_campaign_total(self) -> None:
        q = ait.build_auction_insight_gaql(level="campaign", days=30, granularity="total")
        self.assertIn("FROM campaign", q)
        self.assertIn("segments.auction_insight_domain", q)
        self.assertIn("metrics.auction_insight_search_impression_share", q)
        self.assertIn("LAST_30_DAYS", q)
        self.assertNotIn("auction_insight_view", q)

    def test_monthly_granularity(self) -> None:
        q = ait.build_auction_insight_gaql(
            level="customer", days=90, granularity="monthly"
        )
        self.assertIn("segments.month", q)
        self.assertIn("segments.year", q)
        self.assertIn("FROM customer", q)

    def test_quarterly_granularity(self) -> None:
        q = ait.build_auction_insight_gaql(
            level="campaign", days=180, granularity="quarterly", campaign_id="123"
        )
        self.assertIn("segments.quarter", q)
        self.assertIn("campaign.id = 123", q)

    def test_invalid_days(self) -> None:
        with self.assertRaises(ValueError):
            ait.build_auction_insight_gaql(level="campaign", days=45)

    def test_summarize_rows(self) -> None:
        rows = [
            {
                "segments": {"auctionInsightDomain": "example.com"},
                "metrics": {"auctionInsightSearchImpressionShare": 0.25},
            },
            {
                "segments": {"auctionInsightDomain": "example.com"},
                "metrics": {"auctionInsightSearchImpressionShare": 0.35},
            },
        ]
        summary = ait.summarize_auction_insight_rows(rows)
        self.assertEqual(summary["competitor_count"], 1)
        self.assertAlmostEqual(
            summary["top_competitors"][0]["avg_competitor_impression_share_pct"], 30.0
        )

    def test_unavailable_error_detection(self) -> None:
        err = '{"error":{"details":[{"errors":[{"message":"Unrecognized field","errorCode":{"queryError":"UNRECOGNIZED_FIELD"}}]}]}} auction_insight'
        self.assertTrue(ait._auction_insights_unavailable(err))


if __name__ == "__main__":
    unittest.main()
