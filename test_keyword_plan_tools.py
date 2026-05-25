"""Unit tests for Keyword Planner helpers (no live Google Ads API)."""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

import keyword_plan_tools as kpt


class TestBuildKeywordIdeasRequestBody(unittest.TestCase):
    def test_keyword_seed_body(self) -> None:
        body, err = kpt._build_keyword_ideas_request_body(
            seed_keywords=["teams room", "yealink"],
            page_url=None,
            site_url=None,
            geo_resource_names=["geoTargetConstants/2356"],
            language_resource="languageConstants/1000",
            network="GOOGLE_SEARCH",
            include_adult_keywords=False,
            limit=50,
        )
        self.assertIsNone(err)
        assert body is not None
        self.assertEqual(body["keywordSeed"]["keywords"], ["teams room", "yealink"])
        self.assertEqual(body["geoTargetConstants"], ["geoTargetConstants/2356"])
        self.assertEqual(body["language"], "languageConstants/1000")
        self.assertEqual(body["pageSize"], 50)

    def test_url_seed_body(self) -> None:
        body, err = kpt._build_keyword_ideas_request_body(
            seed_keywords=None,
            page_url="https://example.com/av",
            site_url=None,
            geo_resource_names=["geoTargetConstants/2356"],
            language_resource="languageConstants/1000",
            network="GOOGLE_SEARCH",
            include_adult_keywords=False,
            limit=100,
        )
        self.assertIsNone(err)
        assert body is not None
        self.assertEqual(body["urlSeed"]["url"], "https://example.com/av")

    def test_site_seed_body(self) -> None:
        body, err = kpt._build_keyword_ideas_request_body(
            seed_keywords=None,
            page_url=None,
            site_url="example.com",
            geo_resource_names=["geoTargetConstants/2356"],
            language_resource="languageConstants/1000",
            network="GOOGLE_SEARCH",
            include_adult_keywords=False,
            limit=100,
        )
        self.assertIsNone(err)
        assert body is not None
        self.assertEqual(body["siteSeed"]["site"], "example.com")

    def test_requires_at_least_one_seed(self) -> None:
        body, err = kpt._build_keyword_ideas_request_body(
            seed_keywords=None,
            page_url=None,
            site_url=None,
            geo_resource_names=["geoTargetConstants/2356"],
            language_resource="languageConstants/1000",
            network="GOOGLE_SEARCH",
            include_adult_keywords=False,
            limit=100,
        )
        self.assertIsNone(body)
        self.assertIn("At least one seed", err or "")


class TestGeoLanguageResolution(unittest.TestCase):
    def setUp(self) -> None:
        kpt.reset_keyword_plan_caches()

    def test_language_english(self) -> None:
        self.assertEqual(kpt._resolve_language_constant("English"), "languageConstants/1000")

    def test_language_passthrough(self) -> None:
        self.assertEqual(
            kpt._resolve_language_constant("languageConstants/1023"),
            "languageConstants/1023",
        )

    def test_unknown_language_raises(self) -> None:
        with self.assertRaises(ValueError):
            kpt._resolve_language_constant("Klingon")

    def test_geo_passthrough(self) -> None:
        resolved, err = kpt._resolve_geo_targets(
            "1234567890",
            ["geoTargetConstants/2356"],
            country_code="IN",
        )
        self.assertIsNone(err)
        self.assertEqual(resolved, ["geoTargetConstants/2356"])

    @patch.object(kpt, "_suggest_geo_target_constants_raw")
    def test_geo_name_resolution(self, mock_suggest) -> None:
        kpt.reset_keyword_plan_caches()
        mock_suggest.return_value = (
            {
                "geoTargetConstantSuggestions": [
                    {
                        "searchTerm": "Mumbai",
                        "reach": 23300000,
                        "geoTargetConstant": {
                            "resourceName": "geoTargetConstants/1007785",
                            "canonicalName": "Mumbai,Maharashtra,India",
                            "targetType": "City",
                            "countryCode": "IN",
                        },
                    }
                ]
            },
            None,
        )
        resolved, err = kpt._resolve_geo_targets("1234567890", ["Mumbai"], country_code="IN")
        self.assertIsNone(err)
        self.assertEqual(resolved, ["geoTargetConstants/1007785"])
        mock_suggest.assert_called_once()


class TestNormalizeKeywordIdeaRow(unittest.TestCase):
    def test_single_avg_monthly_searches(self) -> None:
        row = kpt._normalize_keyword_idea_row(
            {
                "text": "microsoft teams room",
                "keywordIdeaMetrics": {
                    "avgMonthlySearches": 1200,
                    "competition": "MEDIUM",
                    "competitionIndex": 45,
                    "lowTopOfPageBidMicros": 5000000,
                    "highTopOfPageBidMicros": 15000000,
                },
            }
        )
        self.assertEqual(row["keyword"], "microsoft teams room")
        self.assertEqual(row["avg_monthly_searches"], "1200")
        self.assertEqual(row["competition"], "MEDIUM")
        self.assertEqual(row["competition_index"], 45)

    def test_range_avg_monthly_searches(self) -> None:
        row = kpt._normalize_keyword_idea_row(
            {
                "text": "yealink mvc",
                "keywordIdeaMetrics": {
                    "avgMonthlySearchesRange": {"min": 100, "max": 1000},
                    "competition": "LOW",
                },
            }
        )
        self.assertEqual(row["avg_monthly_searches"], "100-1000")

    def test_historical_metrics_field_names(self) -> None:
        row = kpt._normalize_keyword_idea_row(
            {
                "text": "video conferencing",
                "keywordMetrics": {
                    "avgMonthlySearches": 500,
                    "competition": "HIGH",
                    "monthlySearchVolumes": [
                        {"year": 2025, "month": {"name": "JANUARY"}, "monthlySearches": 480}
                    ],
                },
            }
        )
        self.assertEqual(row["avg_monthly_searches"], "500")
        self.assertEqual(len(row["monthly_search_volumes"]), 1)
        self.assertEqual(row["monthly_search_volumes"][0]["month"], "JANUARY")


class TestFormatting(unittest.TestCase):
    def test_table_output(self) -> None:
        rows = [
            {
                "keyword": "teams room setup",
                "avg_monthly_searches": "880",
                "competition": "MEDIUM",
                "competition_index": 50,
                "low_top_of_page_bid_micros": 2000000,
                "high_top_of_page_bid_micros": 8000000,
                "monthly_search_volumes": [],
                "close_variants": [],
            }
        ]
        out = kpt._format_output(
            "1234567890",
            rows,
            "table",
            title="Keyword ideas",
            currency_code="INR",
        )
        self.assertIn("keyword", out)
        self.assertIn("teams room setup", out)
        self.assertIn("INR 2.00", out)
        self.assertIn("INR 8.00", out)

    def test_json_output(self) -> None:
        rows = [
            {
                "keyword": "yealink dealer",
                "avg_monthly_searches": "90",
                "competition": "LOW",
                "competition_index": 10,
                "low_top_of_page_bid_micros": None,
                "high_top_of_page_bid_micros": None,
                "monthly_search_volumes": [],
                "close_variants": [],
            }
        ]
        out = kpt._format_output(
            "1234567890",
            rows,
            "json",
            title="Keyword ideas",
            currency_code="INR",
        )
        data = json.loads(out)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["keyword"], "yealink dealer")

    def test_csv_output(self) -> None:
        rows = [
            {
                "keyword": "digital signage",
                "avg_monthly_searches": "500",
                "competition": "HIGH",
                "competition_index": 80,
                "low_top_of_page_bid_micros": 1000000,
                "high_top_of_page_bid_micros": 3000000,
                "monthly_search_volumes": [],
                "close_variants": [],
            }
        ]
        out = kpt._format_output(
            "1234567890",
            rows,
            "csv",
            title="Keyword ideas",
            currency_code="INR",
        )
        self.assertIn("keyword,avg_monthly_searches", out)
        self.assertIn("digital signage", out)


class TestValidationAsyncTools(unittest.TestCase):
    def test_generate_keyword_ideas_missing_seed(self) -> None:
        out = asyncio.run(
            kpt.generate_keyword_ideas(
                customer_id="1234567890",
                seed_keywords=None,
                page_url=None,
                site_url=None,
            )
        )
        self.assertIn("At least one seed", out)

    def test_get_keyword_metrics_empty_list(self) -> None:
        out = asyncio.run(
            kpt.get_keyword_metrics(
                customer_id="1234567890",
                keywords=[],
            )
        )
        self.assertIn("cannot be empty", out)


if __name__ == "__main__":
    unittest.main()
