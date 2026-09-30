"""
MCP tools: Google Ads Auction Insights (Search) via GAQL.

Uses metrics.auction_insight_search_* with segments.auction_insight_domain on
customer / campaign / ad_group / keyword_view — not auction_insight_view
(which is not a valid GAQL resource).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

Level = Literal["customer", "campaign", "ad_group", "keyword"]
Granularity = Literal["total", "daily", "weekly", "monthly", "quarterly"]

_AUCTION_METRICS = [
    "metrics.auction_insight_search_impression_share",
    "metrics.auction_insight_search_outranking_share",
    "metrics.auction_insight_search_position_above_rate",
    "metrics.auction_insight_search_top_impression_percentage",
    "metrics.auction_insight_search_absolute_top_impression_percentage",
]

_LEVEL_FROM: Dict[Level, str] = {
    "customer": "customer",
    "campaign": "campaign",
    "ad_group": "ad_group",
    "keyword": "keyword_view",
}

_LEVEL_ID_FIELD: Dict[Level, Optional[str]] = {
    "customer": None,
    "campaign": "campaign.id",
    "ad_group": "ad_group.id",
    "keyword": "ad_group_criterion.criterion_id",
}

_LEVEL_NAME_FIELDS: Dict[Level, List[str]] = {
    "customer": [],
    "campaign": ["campaign.id", "campaign.name"],
    "ad_group": ["campaign.name", "ad_group.id", "ad_group.name"],
    "keyword": [
        "campaign.name",
        "ad_group.name",
        "ad_group_criterion.keyword.text",
        "ad_group_criterion.keyword.match_type",
    ],
}

_GRANULARITY_SEGMENTS: Dict[Granularity, List[str]] = {
    "total": [],
    "daily": ["segments.date"],
    "weekly": ["segments.week"],
    "monthly": ["segments.month", "segments.year"],
    "quarterly": ["segments.quarter", "segments.year"],
}

_ALLOWED_DAYS = frozenset({7, 14, 30, 60, 90, 180})


def _digits_only(value: Optional[str]) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    d = "".join(ch for ch in str(value) if ch.isdigit())
    return d or None


def _parse_api_error_message(err_text: str) -> Dict[str, Any]:
    """Extract GoogleAdsFailure hints from JSON error bodies."""
    out: Dict[str, Any] = {"raw": err_text}
    try:
        body = json.loads(err_text)
        details = body.get("error", {}).get("details", [])
        if details:
            errors = details[0].get("errors", [])
            if errors:
                ec = errors[0].get("errorCode", {})
                out["error_code"] = ec
                out["message"] = errors[0].get("message", "")
    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
        pass
    return out


def _auction_insights_unavailable(err_text: str) -> bool:
    """True when API rejects auction-insight metrics (common without allowlisting)."""
    lower = (err_text or "").lower()
    if "auction_insight" in lower and (
        "unrecognized_field" in lower
        or "prohibited" in lower
        or "not publicly available" in lower
        or "permission" in lower
        or "metric_access_denied" in lower
    ):
        return True
    if "bad_resource_type" in lower and "auction_insight" in lower:
        return True
    return False


def build_auction_insight_gaql(
    *,
    level: Level,
    days: int,
    granularity: Granularity = "total",
    campaign_id: Optional[str] = None,
    ad_group_id: Optional[str] = None,
    search_only: bool = True,
    limit: int = 5000,
) -> str:
    """
    Build GAQL for Auction Insights.

    Date filter always uses segments.date DURING LAST_N_DAYS. Time bucketing uses
    segments.month / segments.quarter / etc. when granularity is not ``total``.
    """
    if days not in _ALLOWED_DAYS:
        raise ValueError(
            f"days must be one of {sorted(_ALLOWED_DAYS)}; got {days}. "
            "Use run_gaql for custom BETWEEN ranges."
        )

    from_resource = _LEVEL_FROM[level]
    select_parts: List[str] = list(_LEVEL_NAME_FIELDS[level])
    select_parts.append("segments.auction_insight_domain")
    select_parts.extend(_GRANULARITY_SEGMENTS[granularity])
    select_parts.extend(_AUCTION_METRICS)

    where_parts: List[str] = [f"segments.date DURING LAST_{days}_DAYS"]
    if search_only and level in ("campaign", "ad_group", "keyword"):
        where_parts.append("campaign.advertising_channel_type = 'SEARCH'")
    if level == "keyword":
        where_parts.append("ad_group_criterion.status != 'REMOVED'")
        where_parts.append("ad_group_criterion.type = 'KEYWORD'")

    cid = _digits_only(campaign_id)
    if cid:
        where_parts.append(f"campaign.id = {cid}")
    agid = _digits_only(ad_group_id)
    if agid:
        where_parts.append(f"ad_group.id = {agid}")

    query = f"""
        SELECT
            {", ".join(select_parts)}
        FROM {from_resource}
        WHERE {" AND ".join(where_parts)}
        ORDER BY metrics.auction_insight_search_impression_share DESC
        LIMIT {int(limit)}
    """
    return re.sub(r"\s+", " ", query.strip())


def _nested_get(row: Dict[str, Any], path: str) -> Any:
    cur: Any = row
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _pct(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return round(float(val) * 100, 2)
    except (TypeError, ValueError):
        return None


def summarize_auction_insight_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Roll up competitor domains by average competitor impression share (auction metric).
    """
    by_domain: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        domain = _nested_get(row, "segments.auctionInsightDomain") or _nested_get(
            row, "segments.auction_insight_domain"
        )
        if not domain:
            continue
        domain = str(domain)
        share = _nested_get(row, "metrics.auctionInsightSearchImpressionShare")
        if share is None:
            share = _nested_get(row, "metrics.auction_insight_search_impression_share")
        entry = by_domain.setdefault(
            domain,
            {"domain": domain, "rows": 0, "impression_share_pct_sum": 0.0, "samples": []},
        )
        entry["rows"] += 1
        if share is not None:
            try:
                entry["impression_share_pct_sum"] += float(share) * 100
            except (TypeError, ValueError):
                pass
        if len(entry["samples"]) < 3:
            entry["samples"].append(row)

    ranking: List[Dict[str, Any]] = []
    for domain, entry in by_domain.items():
        avg = (
            entry["impression_share_pct_sum"] / entry["rows"]
            if entry["rows"]
            else 0.0
        )
        ranking.append(
            {
                "competitor_domain": domain,
                "avg_competitor_impression_share_pct": round(avg, 2),
                "row_count": entry["rows"],
            }
        )
    ranking.sort(key=lambda x: x["avg_competitor_impression_share_pct"], reverse=True)
    return {"competitor_count": len(ranking), "top_competitors": ranking[:25]}


def fetch_auction_insights_raw(
    customer_id: str,
    *,
    level: Level = "campaign",
    days: int = 30,
    granularity: Granularity = "total",
    campaign_id: Optional[str] = None,
    ad_group_id: Optional[str] = None,
    search_only: bool = True,
    limit: int = 5000,
) -> Dict[str, Any]:
    from google_ads_server import _gaql_search_raw, format_customer_id

    formatted = format_customer_id(customer_id)
    try:
        query = build_auction_insight_gaql(
            level=level,
            days=days,
            granularity=granularity,
            campaign_id=campaign_id,
            ad_group_id=ad_group_id,
            search_only=search_only,
            limit=limit,
        )
    except ValueError as e:
        return {"ok": False, "error": str(e), "formatted_customer_id": formatted}

    rows, err = _gaql_search_raw(formatted, query)
    if err:
        parsed = _parse_api_error_message(err)
        payload: Dict[str, Any] = {
            "ok": False,
            "error": err,
            "parsed_error": parsed,
            "formatted_customer_id": formatted,
            "query": query,
        }
        if _auction_insights_unavailable(err):
            payload["hint"] = (
                "Auction Insights metrics may not be enabled for this developer token or "
                "account. Google marks auction_insight_search_* as not publicly available for "
                "some API clients. Use metrics.search_impression_share and "
                "metrics.search_rank_lost_impression_share on campaign/keyword_view as a "
                "proxy, or export Auction insights from the Google Ads UI."
            )
        return payload

    return {
        "ok": True,
        "formatted_customer_id": formatted,
        "query": query,
        "rows": rows or [],
        "summary": summarize_auction_insight_rows(rows or []),
    }


def _format_auction_insight_table(formatted_customer_id: str, rows: List[Dict[str, Any]]) -> str:
    from google_ads_server import _format_execute_gaql_table

    if not rows:
        return f"No auction insight rows for account {formatted_customer_id} (threshold or date range)."
    return _format_execute_gaql_table(formatted_customer_id, rows)


async def get_auction_insights(
    customer_id: str = Field(
        description="Google Ads customer ID (10 digits, no dashes). Example: '2696255703'"
    ),
    days: int = Field(
        default=30,
        description="Lookback: 7, 14, 30, 60, 90, or 180 days (LAST_N_DAYS).",
    ),
    level: str = Field(
        default="campaign",
        description=(
            "Scope: customer (account), campaign, ad_group, or keyword (keyword_view). "
            "Default campaign."
        ),
    ),
    granularity: str = Field(
        default="total",
        description=(
            "Time bucket: total (aggregate over period), daily, weekly, monthly, or quarterly. "
            "Use monthly/quarterly for competitor IS planning."
        ),
    ),
    campaign_id: Optional[str] = Field(
        default=None,
        description="Optional numeric campaign.id filter.",
    ),
    ad_group_id: Optional[str] = Field(
        default=None,
        description="Optional numeric ad_group.id filter (ad_group / keyword levels).",
    ),
    search_only: bool = Field(
        default=True,
        description="When True, restrict to Search campaigns (recommended for IFP/edu Search).",
    ),
    output_format: str = Field(
        default="json",
        description="json (includes summary + rows) or table (pipe-delimited text only).",
    ),
    limit: int = Field(
        default=5000,
        description="Max GAQL rows (default 5000).",
    ),
) -> str:
    """
    Fetch **Search Auction Insights** — competitor domains and their auction metrics
    vs your eligible impressions.

    Uses GAQL on ``customer``, ``campaign``, ``ad_group``, or ``keyword_view`` with
    ``segments.auction_insight_domain`` and ``metrics.auction_insight_search_*``.
    Do **not** use ``FROM auction_insight_view`` (invalid resource).

    Metrics (per competitor domain):
    - ``auction_insight_search_impression_share`` — competitor share of your eligible auctions
    - ``auction_insight_search_outranking_share`` — how often you outranked them
    - ``auction_insight_search_position_above_rate`` — they showed above you on same page
    - ``auction_insight_search_top_impression_percentage`` / ``absolute_top_*`` — top placement rates

    **Monthly / quarterly planning:** set ``granularity`` to ``monthly`` or ``quarterly`` with
    ``days=90`` or ``180``.

    If the API returns UNRECOGNIZED_FIELD for auction metrics, your token may lack Auction
    Insights API access; the response includes a fallback hint (rank-lost IS proxy).
    """
    lvl = str(level).strip().lower().replace("-", "_")
    if lvl not in _LEVEL_FROM:
        return (
            f"Error: level must be one of {list(_LEVEL_FROM.keys())}; got {level!r}."
        )
    gran = str(granularity).strip().lower()
    if gran not in _GRANULARITY_SEGMENTS:
        return (
            f"Error: granularity must be one of {list(_GRANULARITY_SEGMENTS.keys())}; "
            f"got {granularity!r}."
        )

    data = fetch_auction_insights_raw(
        customer_id,
        level=lvl,  # type: ignore[arg-type]
        days=days,
        granularity=gran,  # type: ignore[arg-type]
        campaign_id=campaign_id,
        ad_group_id=ad_group_id,
        search_only=search_only,
        limit=limit,
    )
    if not data.get("ok"):
        return json.dumps(
            {k: v for k, v in data.items() if k != "rows"},
            indent=2,
            default=str,
        )

    fmt = str(output_format).strip().lower()
    if fmt == "table":
        return _format_auction_insight_table(
            data["formatted_customer_id"], data.get("rows") or []
        )

    return json.dumps(
        {
            "formatted_customer_id": data["formatted_customer_id"],
            "level": lvl,
            "days": days,
            "granularity": gran,
            "query": data.get("query"),
            "summary": data.get("summary"),
            "row_count": len(data.get("rows") or []),
            "rows": data.get("rows"),
        },
        indent=2,
        default=str,
    )


async def get_auction_insights_competitor_ranking(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    days: int = Field(default=30, description="7, 14, 30, 60, 90, or 180 days"),
    level: str = Field(default="campaign", description="customer | campaign | ad_group | keyword"),
    granularity: str = Field(
        default="monthly",
        description="total | daily | weekly | monthly | quarterly — monthly/quarterly for planning",
    ),
    campaign_id: Optional[str] = Field(default=None, description="Optional campaign.id filter"),
    top_n: int = Field(default=15, description="How many competitor domains to return"),
) -> str:
    """
    Compact competitor leaderboard from Auction Insights (avg competitor impression share).

    Same GAQL as ``get_auction_insights`` but returns only aggregated domains — useful for
    monthly/quarterly target IS vs named competitors when API access is available.
    """
    lvl = str(level).strip().lower().replace("-", "_")
    gran = str(granularity).strip().lower()
    data = fetch_auction_insights_raw(
        customer_id,
        level=lvl if lvl in _LEVEL_FROM else "campaign",  # type: ignore[arg-type]
        days=days,
        granularity=gran if gran in _GRANULARITY_SEGMENTS else "monthly",  # type: ignore[arg-type]
        campaign_id=campaign_id,
        limit=5000,
    )
    if not data.get("ok"):
        return json.dumps(data, indent=2, default=str)

    summary = data.get("summary") or {}
    top = (summary.get("top_competitors") or [])[: max(1, int(top_n))]
    return json.dumps(
        {
            "formatted_customer_id": data["formatted_customer_id"],
            "days": days,
            "level": lvl,
            "granularity": gran,
            "competitor_count": summary.get("competitor_count", 0),
            "top_competitors": top,
            "note": (
                "avg_competitor_impression_share_pct is from metrics.auction_insight_search_"
                "impression_share (competitor share in auctions you were eligible for)."
            ),
        },
        indent=2,
        default=str,
    )
