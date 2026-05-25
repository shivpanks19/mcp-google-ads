"""Tests for ad copy analysis helpers (no live Google Ads API)."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import analysis_tools as at
import google_ads_server as gas


class TestSummarizeAdCopyAssets(unittest.TestCase):
    def test_parses_primary_view_shape(self) -> None:
        rows = [
            {
                "campaign": {"name": "Brand Search"},
                "adGroup": {"name": "Core"},
                "adGroupAd": {"ad": {"id": "1"}},
                "asset": {"id": "99", "textAsset": {"text": "Free shipping today"}},
                "adGroupAdAssetView": {
                    "fieldType": "HEADLINE",
                    "performanceLabel": "BEST",
                },
                "metrics": {
                    "impressions": "1000",
                    "clicks": "50",
                    "conversions": "2",
                    "costMicros": "5000000",
                    "ctr": "0.05",
                },
            },
            {
                "campaign": {"name": "Brand Search"},
                "adGroup": {"name": "Core"},
                "asset": {"textAsset": {"text": "Free shipping today"}},
                "adGroupAdAssetView": {
                    "fieldType": "HEADLINE",
                    "performanceLabel": "LOW",
                },
                "metrics": {"impressions": "200", "clicks": "5", "conversions": "0"},
            },
        ]
        agg, parsed = at.summarize_ad_copy_assets(rows)
        self.assertEqual(agg["asset_count"], 2)
        self.assertEqual(len(agg["duplicate_lines"]), 1)
        self.assertEqual(agg["duplicate_lines"][0]["count"], 2)
        self.assertEqual(agg["by_performance_label"].get("BEST"), 1)
        self.assertEqual(len(parsed), 2)

    def test_campaign_filter(self) -> None:
        rows = [
            {
                "campaign": {"name": "Alpha"},
                "asset": {"textAsset": {"text": "Head A"}},
                "adGroupAdAssetView": {"fieldType": "HEADLINE", "performanceLabel": "GOOD"},
                "metrics": {"impressions": "10", "clicks": "1"},
            },
            {
                "campaign": {"name": "Beta"},
                "asset": {"textAsset": {"text": "Head B"}},
                "adGroupAdAssetView": {"fieldType": "HEADLINE", "performanceLabel": "GOOD"},
                "metrics": {"impressions": "10", "clicks": "1"},
            },
        ]
        agg, _ = at.summarize_ad_copy_assets(rows, campaign_filter="alpha")
        self.assertEqual(agg["asset_count"], 1)

    def test_over_limit_headline(self) -> None:
        long_headline = "x" * 35
        rows = [
            {
                "campaign": {"name": "C"},
                "asset": {"textAsset": {"text": long_headline}},
                "adGroupAdAssetView": {"fieldType": "HEADLINE", "performanceLabel": "GOOD"},
                "metrics": {"impressions": "100", "clicks": "1"},
            },
        ]
        agg, _ = at.summarize_ad_copy_assets(rows)
        self.assertEqual(len(agg["over_char_limit"]), 1)


class TestFetchAdCopyAssetPerformanceRows(unittest.TestCase):
    def test_uses_fallback_when_primary_empty(self) -> None:
        fallback_rows = [
            {
                "campaign": {"name": "C"},
                "asset": {"type": "TEXT", "textAsset": {"text": "Fallback line"}},
                "assetPerformanceLabel": "GOOD",
                "metrics": {"impressions": "5", "clicks": "1"},
            },
        ]

        def side_effect(_cid: str, query: str):
            if "ad_group_ad_asset_view" in query:
                return [], None
            return fallback_rows, None

        with patch.object(gas, "_gaql_search_raw", side_effect=side_effect):
            d = asyncio.run(gas.fetch_ad_copy_asset_performance_rows("1234567890", 30))
        self.assertTrue(d["ok"])
        self.assertEqual(d["source"], "asset_performance_label_view")
        self.assertEqual(len(d["rows"]), 1)


if __name__ == "__main__":
    unittest.main()
