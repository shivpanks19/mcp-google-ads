import unittest

from ppc_manager_logic import (
    build_action_plan_payload,
    recommend_keyword_actions,
    recommend_search_term_actions,
)


class PpcManagerLogicTest(unittest.TestCase):
    def test_recommend_search_term_actions_classifies_negatives_and_expansions(self):
        rows = [
            {
                "campaign": {"id": "1", "name": "Brand"},
                "adGroup": {"id": "11", "name": "Core"},
                "searchTermView": {"searchTerm": "free job"},
                "metrics": {"clicks": "20", "impressions": "100", "costMicros": "150000000", "conversions": "0"},
            },
            {
                "campaign": {"id": "1", "name": "Brand"},
                "adGroup": {"id": "11", "name": "Core"},
                "searchTermView": {"searchTerm": "buy crm"},
                "metrics": {"clicks": "8", "impressions": "80", "costMicros": "80000000", "conversions": "2"},
            },
        ]

        result = recommend_search_term_actions(
            rows,
            min_waste_spend=100,
            min_waste_clicks=10,
            max_waste_conversions=0,
            min_expansion_conversions=1,
            target_cpa=100,
        )

        self.assertEqual(result["summary"]["negative_candidate_count"], 1)
        self.assertEqual(result["summary"]["expansion_candidate_count"], 1)
        self.assertEqual(result["negative_candidates"][0]["search_term"], "free job")
        self.assertEqual(result["keyword_expansion_candidates"][0]["search_term"], "buy crm")

    def test_recommend_keyword_actions_finds_waste_winners_and_low_ctr(self):
        rows = [
            {
                "campaign": {"id": "1", "name": "Search"},
                "adGroup": {"id": "2", "name": "A"},
                "adGroupCriterion": {
                    "criterionId": "100",
                    "status": "ENABLED",
                    "keyword": {"text": "bad keyword", "matchType": "PHRASE"},
                },
                "metrics": {"clicks": "25", "impressions": "2000", "costMicros": "200000000", "conversions": "0"},
            },
            {
                "campaign": {"id": "1", "name": "Search"},
                "adGroup": {"id": "2", "name": "A"},
                "adGroupCriterion": {
                    "criterionId": "101",
                    "status": "ENABLED",
                    "keyword": {"text": "good keyword", "matchType": "EXACT"},
                },
                "metrics": {"clicks": "10", "impressions": "100", "costMicros": "50000000", "conversions": "2"},
            },
        ]

        result = recommend_keyword_actions(
            rows,
            min_waste_spend=100,
            min_waste_clicks=10,
            target_cpa=50,
            low_ctr_threshold=0.02,
        )

        self.assertEqual(result["summary"]["pause_candidate_count"], 1)
        self.assertEqual(result["summary"]["winner_count"], 1)
        self.assertEqual(result["summary"]["low_ctr_candidate_count"], 1)
        self.assertEqual(result["pause_candidates"][0]["keyword_text"], "bad keyword")
        self.assertEqual(result["winners"][0]["keyword_text"], "good keyword")

    def test_build_action_plan_payload_names_apply_tools(self):
        payload = build_action_plan_payload(
            customer_id="1234567890",
            days=7,
            currency_code="INR",
            campaign_summary={"total_spend": 1000, "blended_cpa": 250},
            search_term_recommendations={
                "summary": {
                    "rows_analyzed": 2,
                    "negative_candidate_count": 1,
                    "expansion_candidate_count": 1,
                },
                "negative_candidates": [
                    {
                        "campaign_id": "1",
                        "search_term": "free job",
                        "suggested_match_type": "EXACT",
                        "reason": "Spend with no conversions",
                    }
                ],
                "keyword_expansion_candidates": [
                    {
                        "campaign_id": "1",
                        "ad_group_id": "2",
                        "search_term": "buy crm",
                        "suggested_match_type": "PHRASE",
                        "reason": "2 conversions",
                    }
                ],
            },
            keyword_recommendations={
                "summary": {"rows_analyzed": 1, "pause_candidate_count": 1},
                "pause_candidates": [
                    {
                        "campaign_id": "1",
                        "ad_group_id": "2",
                        "criterion_id": "100",
                        "keyword_text": "bad keyword",
                        "reason": "Spend and clicks with no conversions",
                    }
                ],
            },
        )

        self.assertEqual(payload["diagnostics"]["negative_candidates"], 1)
        self.assertEqual(payload["diagnostics"]["keyword_pause_candidates"], 1)
        self.assertEqual(payload["action_items"][0]["tool_to_apply"], "add_negative_keywords")
        self.assertEqual(payload["action_items"][1]["tool_to_apply"], "update_keyword_status")
        self.assertIn("create_keywords", payload["next_best_tools"])
        self.assertIn("update_keyword_status", payload["next_best_tools"])


if __name__ == "__main__":
    unittest.main()
