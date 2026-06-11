"""Pure helpers for PPC manager recommendations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _obj(row: Dict[str, Any], *names: str) -> Dict[str, Any]:
    for name in names:
        value = row.get(name)
        if isinstance(value, dict):
            return value
    return {}


def _str(value: Any) -> str:
    return "" if value is None else str(value)


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def micros_to_currency(micros: Any) -> float:
    return round(_int(micros) / 1_000_000, 2)


def normalize_search_term_row(row: Dict[str, Any]) -> Dict[str, Any]:
    campaign = _obj(row, "campaign")
    ad_group = _obj(row, "adGroup", "ad_group")
    term_view = _obj(row, "searchTermView", "search_term_view")
    metrics = _obj(row, "metrics")
    cost_micros = _int(metrics.get("costMicros", metrics.get("cost_micros")))
    clicks = _int(metrics.get("clicks"))
    impressions = _int(metrics.get("impressions"))
    conversions = _float(metrics.get("conversions"))
    return {
        "campaign_id": _str(campaign.get("id")),
        "campaign_name": _str(campaign.get("name")),
        "ad_group_id": _str(ad_group.get("id")),
        "ad_group_name": _str(ad_group.get("name")),
        "search_term": _str(term_view.get("searchTerm", term_view.get("search_term"))).strip(),
        "impressions": impressions,
        "clicks": clicks,
        "cost_micros": cost_micros,
        "cost": micros_to_currency(cost_micros),
        "conversions": conversions,
        "ctr": round(clicks / impressions, 4) if impressions else 0.0,
        "cpc": round(micros_to_currency(cost_micros) / clicks, 2) if clicks else None,
        "cpa": round(micros_to_currency(cost_micros) / conversions, 2) if conversions > 0 else None,
    }


def recommend_search_term_actions(
    rows: List[Dict[str, Any]],
    *,
    min_waste_spend: float,
    min_waste_clicks: int,
    max_waste_conversions: float,
    min_expansion_conversions: float,
    target_cpa: Optional[float] = None,
    limit: int = 25,
) -> Dict[str, Any]:
    normalized = [r for r in (normalize_search_term_row(row) for row in rows) if r["search_term"]]
    negative_candidates: List[Dict[str, Any]] = []
    expansion_candidates: List[Dict[str, Any]] = []
    review_candidates: List[Dict[str, Any]] = []

    for row in normalized:
        spend = row["cost"]
        conversions = row["conversions"]
        clicks = row["clicks"]
        cpa = row["cpa"]
        if spend >= min_waste_spend and clicks >= min_waste_clicks and conversions <= max_waste_conversions:
            negative_candidates.append(
                {
                    **row,
                    "recommended_action": "add_campaign_negative_keyword",
                    "suggested_match_type": "EXACT",
                    "reason": (
                        f"Spend {spend:.2f} with {clicks} clicks and "
                        f"{conversions:g} conversions"
                    ),
                }
            )
        elif conversions >= min_expansion_conversions and (
            target_cpa is None or cpa is None or cpa <= target_cpa
        ):
            expansion_candidates.append(
                {
                    **row,
                    "recommended_action": "add_positive_keyword",
                    "suggested_match_type": "PHRASE",
                    "reason": (
                        f"{conversions:g} conversions"
                        + (f" at CPA {cpa:.2f}" if cpa is not None else "")
                    ),
                }
            )
        elif spend > 0:
            review_candidates.append({**row, "recommended_action": "review"})

    negative_candidates.sort(key=lambda r: (r["cost"], r["clicks"]), reverse=True)
    expansion_candidates.sort(key=lambda r: (r["conversions"], -(r["cpa"] or 0)), reverse=True)
    review_candidates.sort(key=lambda r: r["cost"], reverse=True)

    total_spend = round(sum(r["cost"] for r in normalized), 2)
    total_conversions = round(sum(r["conversions"] for r in normalized), 2)
    return {
        "summary": {
            "rows_analyzed": len(normalized),
            "total_spend": total_spend,
            "total_conversions": total_conversions,
            "negative_candidate_count": len(negative_candidates),
            "expansion_candidate_count": len(expansion_candidates),
        },
        "negative_candidates": negative_candidates[:limit],
        "keyword_expansion_candidates": expansion_candidates[:limit],
        "review_candidates": review_candidates[:limit],
    }


def normalize_keyword_row(row: Dict[str, Any]) -> Dict[str, Any]:
    campaign = _obj(row, "campaign")
    ad_group = _obj(row, "adGroup", "ad_group")
    criterion = _obj(row, "adGroupCriterion", "ad_group_criterion")
    keyword = _obj(criterion, "keyword")
    metrics = _obj(row, "metrics")
    cost_micros = _int(metrics.get("costMicros", metrics.get("cost_micros")))
    clicks = _int(metrics.get("clicks"))
    impressions = _int(metrics.get("impressions"))
    conversions = _float(metrics.get("conversions"))
    return {
        "campaign_id": _str(campaign.get("id")),
        "campaign_name": _str(campaign.get("name")),
        "ad_group_id": _str(ad_group.get("id")),
        "ad_group_name": _str(ad_group.get("name")),
        "criterion_id": _str(criterion.get("criterionId", criterion.get("criterion_id"))),
        "keyword_text": _str(keyword.get("text")).strip(),
        "match_type": _str(keyword.get("matchType", keyword.get("match_type"))),
        "status": _str(criterion.get("status")),
        "impressions": impressions,
        "clicks": clicks,
        "cost_micros": cost_micros,
        "cost": micros_to_currency(cost_micros),
        "conversions": conversions,
        "ctr": round(clicks / impressions, 4) if impressions else 0.0,
        "cpc": round(micros_to_currency(cost_micros) / clicks, 2) if clicks else None,
        "cpa": round(micros_to_currency(cost_micros) / conversions, 2) if conversions > 0 else None,
    }


def recommend_keyword_actions(
    rows: List[Dict[str, Any]],
    *,
    min_waste_spend: float,
    min_waste_clicks: int,
    target_cpa: Optional[float] = None,
    low_ctr_threshold: float = 0.01,
    limit: int = 25,
) -> Dict[str, Any]:
    normalized = [r for r in (normalize_keyword_row(row) for row in rows) if r["keyword_text"]]
    pause_candidates: List[Dict[str, Any]] = []
    bid_down_candidates: List[Dict[str, Any]] = []
    winners: List[Dict[str, Any]] = []
    low_ctr_candidates: List[Dict[str, Any]] = []

    for row in normalized:
        if row["status"] not in ("ENABLED", ""):
            continue
        spend = row["cost"]
        clicks = row["clicks"]
        conversions = row["conversions"]
        cpa = row["cpa"]
        if spend >= min_waste_spend and clicks >= min_waste_clicks and conversions == 0:
            pause_candidates.append(
                {**row, "recommended_action": "pause_keyword_or_add_negative", "reason": "Spend and clicks with no conversions"}
            )
        elif target_cpa is not None and cpa is not None and cpa > target_cpa * 1.5:
            bid_down_candidates.append(
                {**row, "recommended_action": "reduce_bid_or_tighten_match", "reason": f"CPA {cpa:.2f} is > 1.5x target CPA {target_cpa:.2f}"}
            )
        elif conversions > 0 and (target_cpa is None or cpa is None or cpa <= target_cpa):
            winners.append({**row, "recommended_action": "protect_or_scale", "reason": "Converting within target"})

        if row["impressions"] >= 100 and row["ctr"] < low_ctr_threshold:
            low_ctr_candidates.append(
                {**row, "recommended_action": "rewrite_ad_or_tighten_keyword", "reason": f"CTR below {low_ctr_threshold:.2%}"}
            )

    pause_candidates.sort(key=lambda r: (r["cost"], r["clicks"]), reverse=True)
    bid_down_candidates.sort(key=lambda r: r["cpa"] or 0, reverse=True)
    winners.sort(key=lambda r: (r["conversions"], -(r["cpa"] or 0)), reverse=True)
    low_ctr_candidates.sort(key=lambda r: r["impressions"], reverse=True)

    return {
        "summary": {
            "rows_analyzed": len(normalized),
            "pause_candidate_count": len(pause_candidates),
            "bid_down_candidate_count": len(bid_down_candidates),
            "winner_count": len(winners),
            "low_ctr_candidate_count": len(low_ctr_candidates),
        },
        "pause_candidates": pause_candidates[:limit],
        "bid_down_candidates": bid_down_candidates[:limit],
        "winners": winners[:limit],
        "low_ctr_candidates": low_ctr_candidates[:limit],
    }


def build_action_plan_payload(
    *,
    customer_id: str,
    days: int,
    currency_code: str,
    campaign_summary: Dict[str, Any],
    search_term_recommendations: Dict[str, Any],
    keyword_recommendations: Dict[str, Any],
) -> Dict[str, Any]:
    search_summary = search_term_recommendations.get("summary", {})
    keyword_summary = keyword_recommendations.get("summary", {})
    action_items: List[Dict[str, Any]] = []

    for row in search_term_recommendations.get("negative_candidates", [])[:10]:
        action_items.append(
            {
                "priority": "high",
                "category": "search_terms",
                "action": "add_negative_keyword",
                "tool_to_apply": "add_negative_keywords",
                "campaign_id": row.get("campaign_id"),
                "keyword": row.get("search_term"),
                "match_type": row.get("suggested_match_type", "EXACT"),
                "why": row.get("reason"),
            }
        )

    for row in keyword_recommendations.get("pause_candidates", [])[:10]:
        action_items.append(
            {
                "priority": "high",
                "category": "keywords",
                "action": "pause_or_review_keyword",
                "tool_to_apply": "update_keyword_status",
                "campaign_id": row.get("campaign_id"),
                "ad_group_id": row.get("ad_group_id"),
                "criterion_id": row.get("criterion_id"),
                "keyword": row.get("keyword_text"),
                "why": row.get("reason"),
            }
        )

    for row in search_term_recommendations.get("keyword_expansion_candidates", [])[:10]:
        action_items.append(
            {
                "priority": "medium",
                "category": "search_terms",
                "action": "add_positive_keyword",
                "tool_to_apply": "create_keywords",
                "campaign_id": row.get("campaign_id"),
                "ad_group_id": row.get("ad_group_id"),
                "keyword": row.get("search_term"),
                "match_type": row.get("suggested_match_type", "PHRASE"),
                "why": row.get("reason"),
            }
        )

    return {
        "customer_id": customer_id,
        "lookback_days": days,
        "currency_code": currency_code,
        "campaign_summary": campaign_summary,
        "diagnostics": {
            "search_term_rows_analyzed": search_summary.get("rows_analyzed", 0),
            "keyword_rows_analyzed": keyword_summary.get("rows_analyzed", 0),
            "negative_candidates": search_summary.get("negative_candidate_count", 0),
            "keyword_pause_candidates": keyword_summary.get("pause_candidate_count", 0),
            "keyword_expansion_candidates": search_summary.get("expansion_candidate_count", 0),
        },
        "action_items": action_items,
        "next_best_tools": [
            "get_campaign_settings",
            "add_negative_keywords",
            "create_keywords",
            "update_keyword_status",
            "analyze_ad_copy",
            "apply_weekly_performance_actions",
        ],
    }
