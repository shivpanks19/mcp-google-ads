"""AI-assisted PPC manager tools built on live Google Ads data."""

from __future__ import annotations

import json
import os
from typing import Annotated, Any, Dict, List, Optional

from pydantic import Field

import mutate_helpers as mh
from google_ads_server import _gaql_search_raw, fetch_campaign_performance_table_and_rows, format_customer_id, mcp
from optimization_actions import _campaign_metrics_from_rows, classify_campaigns
from ppc_manager_logic import (
    build_action_plan_payload,
    recommend_keyword_actions,
    recommend_search_term_actions,
)


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _days(days: int) -> int:
    return max(1, min(int(days), 365))


def _limit(limit: int) -> int:
    return max(1, min(int(limit), 1000))


def _optional_id_filter(field: str, value: Optional[str]) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return f"\n          AND {field} = {digits}" if digits else ""


def _ad_group_criterion_resource_name(customer_id: str, ad_group_id: str, criterion_id: str) -> str:
    cid = format_customer_id(customer_id)
    agid = "".join(ch for ch in str(ad_group_id) if ch.isdigit())
    critid = "".join(ch for ch in str(criterion_id) if ch.isdigit())
    return f"customers/{cid}/adGroupCriteria/{agid}~{critid}"


def _mutations_disabled_by_env() -> bool:
    return os.environ.get("GOOGLE_ADS_DISABLE_MUTATIONS", "").strip().lower() in ("1", "true", "yes")


def _validate_only_forced() -> bool:
    return os.environ.get("GOOGLE_ADS_MUTATE_VALIDATE_ONLY", "").strip().lower() in ("1", "true", "yes")


def _search_terms_gaql(
    *,
    days: int,
    campaign_id: Optional[str],
    ad_group_id: Optional[str],
    min_clicks: int,
    limit: int,
) -> str:
    return f"""
        SELECT
          campaign.id,
          campaign.name,
          ad_group.id,
          ad_group.name,
          search_term_view.search_term,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions
        FROM search_term_view
        WHERE segments.date DURING LAST_{_days(days)}_DAYS
          AND metrics.clicks >= {max(0, int(min_clicks))}
          {_optional_id_filter("campaign.id", campaign_id)}
          {_optional_id_filter("ad_group.id", ad_group_id)}
        ORDER BY metrics.cost_micros DESC
        LIMIT {_limit(limit)}
    """


def _keyword_performance_gaql(
    *,
    days: int,
    campaign_id: Optional[str],
    ad_group_id: Optional[str],
    status: Optional[str],
    min_clicks: int,
    limit: int,
) -> str:
    status_filter = ""
    if status:
        normalized_status = str(status).strip().upper()
        if normalized_status in {"ENABLED", "PAUSED", "REMOVED"}:
            status_filter = f"\n          AND ad_group_criterion.status = {normalized_status}"
    return f"""
        SELECT
          campaign.id,
          campaign.name,
          ad_group.id,
          ad_group.name,
          ad_group_criterion.criterion_id,
          ad_group_criterion.status,
          ad_group_criterion.keyword.text,
          ad_group_criterion.keyword.match_type,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions
        FROM keyword_view
        WHERE segments.date DURING LAST_{_days(days)}_DAYS
          AND ad_group_criterion.type = KEYWORD
          AND metrics.clicks >= {max(0, int(min_clicks))}
          {_optional_id_filter("campaign.id", campaign_id)}
          {_optional_id_filter("ad_group.id", ad_group_id)}
          {status_filter}
        ORDER BY metrics.cost_micros DESC
        LIMIT {_limit(limit)}
    """


def _campaign_summary(campaigns: List[Dict[str, Any]]) -> Dict[str, Any]:
    enabled = [c for c in campaigns if c.get("status") == "ENABLED"]
    total_spend = round(sum(float(c.get("spend") or 0) for c in enabled), 2)
    total_conversions = round(sum(float(c.get("conversions") or 0) for c in enabled), 2)
    blended_cpa = round(total_spend / total_conversions, 2) if total_conversions > 0 else None
    return {
        "enabled_campaigns_with_spend": len([c for c in enabled if c.get("spend", 0) > 0]),
        "total_spend": total_spend,
        "total_conversions": total_conversions,
        "blended_cpa": blended_cpa,
    }


@mcp.tool()
async def get_search_term_insights(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    days: Annotated[int, Field(description="Lookback window in days")] = 30,
    campaign_id: Annotated[Optional[str], Field(description="Optional campaign id filter")] = None,
    ad_group_id: Annotated[Optional[str], Field(description="Optional ad group id filter")] = None,
    min_waste_spend: Annotated[float, Field(description="Min spend in account currency for negative candidates")] = 100.0,
    min_waste_clicks: Annotated[int, Field(description="Min clicks for negative candidates")] = 10,
    max_waste_conversions: Annotated[float, Field(description="Max conversions for negative candidates")] = 0.0,
    min_expansion_conversions: Annotated[float, Field(description="Min conversions for positive keyword candidates")] = 1.0,
    target_cpa: Annotated[Optional[float], Field(description="Optional target CPA for expansion filtering")] = None,
    limit: Annotated[int, Field(description="Max GAQL rows and max recommendations per bucket")] = 100,
) -> str:
    """
    Read-only search-term mining for PPC managers.

    Returns waste terms to add as negatives, converting queries to promote as
    keywords, and the existing tools to use when applying changes.
    """
    formatted_customer_id = format_customer_id(customer_id)
    query = _search_terms_gaql(
        days=days,
        campaign_id=campaign_id,
        ad_group_id=ad_group_id,
        min_clicks=0,
        limit=limit,
    )
    rows, err = _gaql_search_raw(formatted_customer_id, query)
    if err:
        return _json({"ok": False, "error": err})
    recs = recommend_search_term_actions(
        rows,
        min_waste_spend=min_waste_spend,
        min_waste_clicks=min_waste_clicks,
        max_waste_conversions=max_waste_conversions,
        min_expansion_conversions=min_expansion_conversions,
        target_cpa=target_cpa,
        limit=min(_limit(limit), 50),
    )
    return _json(
        {
            "ok": True,
            "customer_id": formatted_customer_id,
            "lookback_days": _days(days),
            "apply_tools": {
                "negatives": "add_negative_keywords",
                "positive_keywords": "create_keywords",
            },
            **recs,
        }
    )


@mcp.tool()
async def get_keyword_performance(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    days: Annotated[int, Field(description="Lookback window in days")] = 30,
    campaign_id: Annotated[Optional[str], Field(description="Optional campaign id filter")] = None,
    ad_group_id: Annotated[Optional[str], Field(description="Optional ad group id filter")] = None,
    status: Annotated[Optional[str], Field(description="Optional keyword status filter: ENABLED, PAUSED, REMOVED")] = "ENABLED",
    min_waste_spend: Annotated[float, Field(description="Min spend in account currency for waste classification")] = 100.0,
    min_waste_clicks: Annotated[int, Field(description="Min clicks for waste classification")] = 10,
    target_cpa: Annotated[Optional[float], Field(description="Optional target CPA for winner/high-CPA classification")] = None,
    low_ctr_threshold: Annotated[float, Field(description="CTR threshold for low-CTR keyword flags, e.g. 0.01 = 1%")] = 0.01,
    limit: Annotated[int, Field(description="Max GAQL rows and max recommendations per bucket")] = 100,
) -> str:
    """Read-only keyword performance triage for pruning, bid review, and scaling."""
    formatted_customer_id = format_customer_id(customer_id)
    query = _keyword_performance_gaql(
        days=days,
        campaign_id=campaign_id,
        ad_group_id=ad_group_id,
        status=status,
        min_clicks=0,
        limit=limit,
    )
    rows, err = _gaql_search_raw(formatted_customer_id, query)
    if err:
        return _json({"ok": False, "error": err})
    recs = recommend_keyword_actions(
        rows,
        min_waste_spend=min_waste_spend,
        min_waste_clicks=min_waste_clicks,
        target_cpa=target_cpa,
        low_ctr_threshold=low_ctr_threshold,
        limit=min(_limit(limit), 50),
    )
    return _json(
        {
            "ok": True,
            "customer_id": formatted_customer_id,
            "lookback_days": _days(days),
            "apply_tools": {
                "pause_campaign_or_ad_group": "update_campaign_status / update_ad_group_status",
                "pause_or_enable_keyword": "update_keyword_status",
                "add_positive_keywords": "create_keywords",
                "add_negatives": "add_negative_keywords",
            },
            **recs,
        }
    )


@mcp.tool()
async def plan_weekly_performance_actions(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    days: Annotated[int, Field(description="Lookback window in days")] = 7,
    min_spend: Annotated[float, Field(description="Min campaign spend in account currency for campaign rules")] = 500.0,
    min_clicks_to_pause: Annotated[int, Field(description="Min campaign clicks before pause rule applies")] = 30,
    max_cpa_multiplier: Annotated[float, Field(description="Flag campaigns above this multiple of blended CPA")] = 2.0,
    target_cpa: Annotated[Optional[float], Field(description="Optional business target CPA for keyword/query triage")] = None,
    search_term_limit: Annotated[int, Field(description="Max search term rows to scan")] = 100,
    keyword_limit: Annotated[int, Field(description="Max keyword rows to scan")] = 100,
) -> str:
    """
    Read-only weekly PPC action plan.

    This previews campaign pauses/budget changes plus search-term and keyword
    actions. Use `apply_weekly_performance_actions` only after reviewing it.
    """
    formatted_customer_id = format_customer_id(customer_id)
    perf = await fetch_campaign_performance_table_and_rows(formatted_customer_id, _days(days))
    if not perf.get("ok"):
        return _json({"ok": False, "error": perf.get("error")})

    campaigns = _campaign_metrics_from_rows(perf.get("rows") or [])
    camp_summary = _campaign_summary(campaigns)
    buckets = classify_campaigns(
        campaigns,
        camp_summary["blended_cpa"],
        min_spend=min_spend,
        min_clicks_pause=min_clicks_to_pause,
        max_cpa_multiplier=max_cpa_multiplier,
    )

    st_rows, st_err = _gaql_search_raw(
        formatted_customer_id,
        _search_terms_gaql(
            days=days,
            campaign_id=None,
            ad_group_id=None,
            min_clicks=0,
            limit=search_term_limit,
        ),
    )
    if st_err:
        st_recs = {"summary": {"rows_analyzed": 0}, "error": st_err, "negative_candidates": [], "keyword_expansion_candidates": []}
    else:
        st_recs = recommend_search_term_actions(
            st_rows,
            min_waste_spend=max(1.0, min_spend / 5),
            min_waste_clicks=max(3, min_clicks_to_pause // 3),
            max_waste_conversions=0,
            min_expansion_conversions=1,
            target_cpa=target_cpa,
            limit=25,
        )

    kw_rows, kw_err = _gaql_search_raw(
        formatted_customer_id,
        _keyword_performance_gaql(
            days=days,
            campaign_id=None,
            ad_group_id=None,
            status="ENABLED",
            min_clicks=0,
            limit=keyword_limit,
        ),
    )
    if kw_err:
        kw_recs = {"summary": {"rows_analyzed": 0}, "error": kw_err, "pause_candidates": [], "winners": []}
    else:
        kw_recs = recommend_keyword_actions(
            kw_rows,
            min_waste_spend=max(1.0, min_spend / 5),
            min_waste_clicks=max(3, min_clicks_to_pause // 3),
            target_cpa=target_cpa,
            limit=25,
        )

    currency_code = mh.fetch_account_currency_code(formatted_customer_id)
    payload = build_action_plan_payload(
        customer_id=formatted_customer_id,
        days=_days(days),
        currency_code=currency_code,
        campaign_summary={
            **camp_summary,
            "campaign_pause_candidates": buckets["pause"],
            "campaign_reduce_budget_candidates": buckets["reduce_budget"],
            "campaign_increase_budget_candidates": buckets["increase_budget"],
        },
        search_term_recommendations=st_recs,
        keyword_recommendations=kw_recs,
    )
    payload["ok"] = True
    payload["notes"] = [
        "This tool is read-only; review action_items before applying changes.",
        "For direct campaign edits use apply_weekly_performance_actions or the surgical mutate tools.",
    ]
    if st_recs.get("error"):
        payload["search_term_error"] = st_recs["error"]
    if kw_recs.get("error"):
        payload["keyword_error"] = kw_recs["error"]
    return _json(payload)


@mcp.tool()
async def update_keyword_status(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    ad_group_id: Annotated[str, Field(description="Ad group id containing the keyword criterion")],
    criterion_id: Annotated[str, Field(description="Keyword criterion id from get_keyword_performance")],
    status: Annotated[str, Field(description="New keyword status: ENABLED, PAUSED, or REMOVED")],
    validate_only: Annotated[bool, Field(description="Dry-run mutate without applying")] = False,
) -> str:
    """
    Mutate one keyword criterion status.

    Use `get_keyword_performance` first to identify `ad_group_id` and
    `criterion_id`. Honors GOOGLE_ADS_DISABLE_MUTATIONS and
    GOOGLE_ADS_MUTATE_VALIDATE_ONLY.
    """
    if _mutations_disabled_by_env():
        return _json({"ok": False, "error": "Mutations are disabled by GOOGLE_ADS_DISABLE_MUTATIONS."})

    normalized_status = str(status or "").strip().upper()
    if normalized_status not in {"ENABLED", "PAUSED", "REMOVED"}:
        return _json({"ok": False, "error": "status must be ENABLED, PAUSED, or REMOVED"})

    agid = "".join(ch for ch in str(ad_group_id) if ch.isdigit())
    critid = "".join(ch for ch in str(criterion_id) if ch.isdigit())
    if not agid or not critid:
        return _json({"ok": False, "error": "ad_group_id and criterion_id must contain digits"})

    effective_validate_only = bool(validate_only) or _validate_only_forced()
    resource_name = _ad_group_criterion_resource_name(customer_id, agid, critid)
    operation = {
        "updateMask": "status",
        "update": {
            "resourceName": resource_name,
            "status": normalized_status,
        },
    }
    data, err = mh._mutate_raw(
        customer_id,
        "adGroupCriteria",
        [operation],
        validate_only=effective_validate_only,
        partial_failure=False,
    )
    if err:
        return _json({"ok": False, "error": err, "resource_name": resource_name})
    return _json(
        {
            "ok": True,
            "validate_only": effective_validate_only,
            "resource_name": resource_name,
            "status": normalized_status,
            "result": mh.format_mutate_results(data),
        }
    )
