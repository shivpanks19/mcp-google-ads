"""
MCP tools for Google Ads Keyword Planner (KeywordPlanIdeaService / GeoTargetConstantService).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from typing import Annotated, Any, Dict, List, Optional, Tuple

from pydantic import Field

from google_ads_server import (
    API_VERSION,
    format_customer_id,
    get_credentials,
    make_api_request,
    mcp,
)

logger = logging.getLogger("google_ads_server")

# In-process caches for geo/language resolution (session lifetime).
_geo_target_cache: Dict[Tuple[str, str, str], str] = {}
_language_cache: Dict[str, str] = {}

_LANGUAGE_NAME_TO_ID: Dict[str, str] = {
    "english": "languageConstants/1000",
    "hindi": "languageConstants/1023",
    "spanish": "languageConstants/1003",
    "french": "languageConstants/1002",
    "german": "languageConstants/1001",
    "portuguese": "languageConstants/1014",
    "arabic": "languageConstants/1019",
    "chinese simplified": "languageConstants/1017",
    "chinese traditional": "languageConstants/1018",
    "japanese": "languageConstants/1005",
}

# Well-known geo targets (fast path; still validated via suggest API when needed).
_KNOWN_GEO_TARGETS: Dict[str, str] = {
    "india": "geoTargetConstants/2356",
}

TABLE_COLUMNS = [
    "keyword",
    "avg_monthly_searches",
    "competition",
    "competition_index",
    "low_cpc",
    "high_cpc",
]


def _is_resource_name(value: str, prefix: str) -> bool:
    return bool(re.match(rf"^{re.escape(prefix)}/\d+$", (value or "").strip()))


def _parse_api_error(error_text: str) -> str:
    if not error_text:
        return "Unknown API error"
    if "USER_PERMISSION_DENIED" in error_text:
        return (
            "USER_PERMISSION_DENIED: credentials cannot access Keyword Planner for this account. "
            "Ensure the OAuth user or service account has Standard access in Google Ads."
        )
    if "DEVELOPER_TOKEN_NOT_APPROVED" in error_text:
        return (
            "DEVELOPER_TOKEN_NOT_APPROVED: Keyword Planner methods require Basic or Standard "
            "developer token access (Explorer/test tokens cannot call generateKeywordIdeas)."
        )
    try:
        payload = json.loads(error_text)
        msg = payload.get("error", {}).get("message")
        details = payload.get("error", {}).get("details") or []
        for detail in details:
            for err in detail.get("errors") or []:
                code = err.get("errorCode") or {}
                for k, v in code.items():
                    if v:
                        extra = f" ({k}: {v})"
                        return (err.get("message") or msg or error_text) + extra
        if msg:
            return msg
    except json.JSONDecodeError:
        pass
    return error_text


def _resolve_language_constant(language: str) -> str:
    lang = (language or "English").strip()
    if _is_resource_name(lang, "languageConstants"):
        return lang
    key = lang.lower()
    if key in _language_cache:
        return _language_cache[key]
    if key in _LANGUAGE_NAME_TO_ID:
        _language_cache[key] = _LANGUAGE_NAME_TO_ID[key]
        return _LANGUAGE_NAME_TO_ID[key]
    # Allow bare numeric IDs like "1000"
    if lang.isdigit():
        resolved = f"languageConstants/{lang}"
        _language_cache[key] = resolved
        return resolved
    raise ValueError(
        f"Unknown language '{language}'. Pass a friendly name (e.g. English), "
        f"a numeric ID (1000), or a resource name (languageConstants/1000)."
    )


def _suggest_geo_target_constants_raw(
    customer_id: str,
    location_names: List[str],
    country_code: str = "IN",
    locale: str = "en",
    creds=None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    # GeoTargetConstantService is not customer-scoped (see geo_target_constant_service.proto).
    _ = format_customer_id(customer_id)
    url = f"https://googleads.googleapis.com/{API_VERSION}/geoTargetConstants:suggest"
    payload = {
        "locale": locale,
        "countryCode": country_code,
        "locationNames": {"names": location_names[:25]},
    }
    return make_api_request(url, method="POST", payload=payload, creds=creds)


def _normalize_geo_suggestion(item: Dict[str, Any]) -> Dict[str, Any]:
    geo = item.get("geoTargetConstant") or item.get("geo_target_constant") or {}
    return {
        "search_term": item.get("searchTerm") or item.get("search_term") or "",
        "canonical_name": geo.get("canonicalName") or geo.get("canonical_name") or "",
        "resource_name": geo.get("resourceName") or geo.get("resource_name") or "",
        "target_type": geo.get("targetType") or geo.get("target_type") or "",
        "country_code": geo.get("countryCode") or geo.get("country_code") or "",
        "reach": item.get("reach"),
    }


def _resolve_geo_targets(
    customer_id: str,
    geo_targets: List[str],
    country_code: str = "IN",
    creds=None,
) -> Tuple[List[str], Optional[str]]:
    resolved: List[str] = []
    to_lookup: List[str] = []

    for name in geo_targets or ["India"]:
        name = (name or "").strip()
        if not name:
            continue
        if _is_resource_name(name, "geoTargetConstants"):
            resolved.append(name)
            continue
        cache_key = (format_customer_id(customer_id), country_code.upper(), name.lower())
        if cache_key in _geo_target_cache:
            resolved.append(_geo_target_cache[cache_key])
            continue
        known = _KNOWN_GEO_TARGETS.get(name.lower())
        if known:
            _geo_target_cache[cache_key] = known
            resolved.append(known)
            continue
        to_lookup.append(name)

    if not to_lookup:
        if not resolved:
            return [], "No valid geo targets provided."
        return resolved, None

    data, error = _suggest_geo_target_constants_raw(
        customer_id, to_lookup, country_code=country_code, creds=creds
    )
    if error:
        return [], _parse_api_error(error)

    suggestions = (data or {}).get("geoTargetConstantSuggestions") or (
        (data or {}).get("geo_target_constant_suggestions") or []
    )
    by_term: Dict[str, Dict[str, Any]] = {}
    for item in suggestions:
        norm = _normalize_geo_suggestion(item)
        term = (norm.get("search_term") or "").lower()
        if term and term not in by_term:
            by_term[term] = norm

    for name in to_lookup:
        cache_key = (format_customer_id(customer_id), country_code.upper(), name.lower())
        match = by_term.get(name.lower())
        if not match:
            # Fall back to first suggestion whose canonical name contains the query
            for term, norm in by_term.items():
                if name.lower() in (norm.get("canonical_name") or "").lower():
                    match = norm
                    break
        if not match and suggestions:
            match = _normalize_geo_suggestion(suggestions[0])
        if not match or not match.get("resource_name"):
            return [], f"Could not resolve geo target '{name}' (country_code={country_code})."
        _geo_target_cache[cache_key] = match["resource_name"]
        resolved.append(match["resource_name"])

    return resolved, None


def _parse_avg_monthly_searches(metrics: Optional[Dict[str, Any]]) -> str:
    if not metrics:
        return ""
    direct = metrics.get("avgMonthlySearches", metrics.get("avg_monthly_searches"))
    if direct is not None and direct != "":
        return str(direct)
    rng = metrics.get("avgMonthlySearchesRange") or metrics.get("avg_monthly_searches_range")
    if isinstance(rng, dict):
        lo = rng.get("min") if rng.get("min") is not None else rng.get("minValue")
        hi = rng.get("max") if rng.get("max") is not None else rng.get("maxValue")
        if lo is not None and hi is not None:
            return f"{lo}-{hi}"
        if lo is not None:
            return str(lo)
        if hi is not None:
            return str(hi)
    return ""


def _normalize_monthly_search_volumes(metrics: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not metrics:
        return []
    volumes = metrics.get("monthlySearchVolumes") or metrics.get("monthly_search_volumes") or []
    out: List[Dict[str, Any]] = []
    for v in volumes:
        month = v.get("month")
        if isinstance(month, dict):
            month_name = month.get("name") or month.get("month")
        else:
            month_name = month
        out.append(
            {
                "year": v.get("year"),
                "month": month_name,
                "monthly_searches": v.get("monthlySearches", v.get("monthly_searches")),
            }
        )
    return out


def _normalize_keyword_idea_row(result: Dict[str, Any]) -> Dict[str, Any]:
    metrics = result.get("keywordIdeaMetrics") or result.get("keyword_idea_metrics") or {}
    if not metrics and result.get("keywordMetrics"):
        metrics = result["keywordMetrics"]
    return {
        "keyword": result.get("text") or "",
        "avg_monthly_searches": _parse_avg_monthly_searches(metrics),
        "competition": metrics.get("competition") or "",
        "competition_index": metrics.get("competitionIndex", metrics.get("competition_index")),
        "low_top_of_page_bid_micros": metrics.get(
            "lowTopOfPageBidMicros", metrics.get("low_top_of_page_bid_micros")
        ),
        "high_top_of_page_bid_micros": metrics.get(
            "highTopOfPageBidMicros", metrics.get("high_top_of_page_bid_micros")
        ),
        "monthly_search_volumes": _normalize_monthly_search_volumes(metrics),
        "close_variants": result.get("closeVariants") or result.get("close_variants") or [],
    }


def _build_keyword_ideas_request_body(
    *,
    seed_keywords: Optional[List[str]],
    page_url: Optional[str],
    site_url: Optional[str],
    geo_resource_names: List[str],
    language_resource: str,
    network: str,
    include_adult_keywords: bool,
    limit: int,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    keywords = [k.strip() for k in (seed_keywords or []) if k and k.strip()]
    page_url = (page_url or "").strip() or None
    site_url = (site_url or "").strip() or None

    if not keywords and not page_url and not site_url:
        return None, "At least one seed is required: seed_keywords, page_url, or site_url."

    body: Dict[str, Any] = {
        "language": language_resource,
        "geoTargetConstants": geo_resource_names,
        "keywordPlanNetwork": network,
        "includeAdultKeywords": include_adult_keywords,
        "pageSize": min(max(int(limit), 1), 1000),
    }

    if site_url and not keywords and not page_url:
        body["siteSeed"] = {"site": site_url}
    elif page_url and not keywords:
        body["urlSeed"] = {"url": page_url}
    elif keywords and page_url:
        body["keywordAndUrlSeed"] = {"keywords": keywords, "url": page_url}
    elif keywords:
        body["keywordSeed"] = {"keywords": keywords}
    else:
        return None, "Could not determine seed type from provided inputs."

    return body, None


def _generate_keyword_ideas_raw(
    customer_id: str,
    body: Dict[str, Any],
    creds=None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    formatted_customer_id = format_customer_id(customer_id)
    url = (
        f"https://googleads.googleapis.com/{API_VERSION}/"
        f"customers/{formatted_customer_id}:generateKeywordIdeas"
    )
    return make_api_request(url, method="POST", payload=body, creds=creds)


def _generate_keyword_historical_metrics_raw(
    customer_id: str,
    body: Dict[str, Any],
    creds=None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    formatted_customer_id = format_customer_id(customer_id)
    url = (
        f"https://googleads.googleapis.com/{API_VERSION}/"
        f"customers/{formatted_customer_id}:generateKeywordHistoricalMetrics"
    )
    return make_api_request(url, method="POST", payload=body, creds=creds)


def _fetch_account_currency_code(customer_id: str, creds=None) -> str:
    formatted_customer_id = format_customer_id(customer_id)
    url = (
        f"https://googleads.googleapis.com/{API_VERSION}/"
        f"customers/{formatted_customer_id}/googleAds:search"
    )
    payload = {
        "query": "SELECT customer.currency_code FROM customer LIMIT 1",
    }
    data, error = make_api_request(url, method="POST", payload=payload, creds=creds)
    if error or not data:
        return "USD"
    results = data.get("results") or []
    if not results:
        return "USD"
    customer = results[0].get("customer") or {}
    return customer.get("currencyCode") or customer.get("currency_code") or "USD"


def _micros_to_display(micros: Any, currency_code: str) -> str:
    if micros is None or micros == "":
        return ""
    try:
        value = int(micros) / 1_000_000
    except (TypeError, ValueError):
        return str(micros)
    return f"{currency_code} {value:.2f}"


def _rows_with_cpc_display(rows: List[Dict[str, Any]], currency_code: str) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        item = dict(row)
        item["low_cpc"] = _micros_to_display(item.pop("low_top_of_page_bid_micros", None), currency_code)
        item["high_cpc"] = _micros_to_display(item.pop("high_top_of_page_bid_micros", None), currency_code)
        out.append(item)
    return out


def _format_keyword_rows_table(
    customer_id: str,
    rows: List[Dict[str, Any]],
    *,
    title: str,
    currency_code: str,
) -> str:
    display_rows = _rows_with_cpc_display(rows, currency_code)
    if not display_rows:
        return f"{title} for Account {format_customer_id(customer_id)}: no results."

    widths = {col: len(col) for col in TABLE_COLUMNS}
    for row in display_rows:
        for col in TABLE_COLUMNS:
            widths[col] = max(widths[col], len(str(row.get(col, ""))))

    header = " | ".join(f"{col:{widths[col]}}" for col in TABLE_COLUMNS)
    lines = [
        f"{title} for Account {format_customer_id(customer_id)} (currency: {currency_code}):",
        "-" * len(header),
        header,
        "-" * len(header),
    ]
    for row in display_rows:
        lines.append(
            " | ".join(f"{str(row.get(col, '')):{widths[col]}}" for col in TABLE_COLUMNS)
        )
    return "\n".join(lines)


def _format_keyword_rows_csv(rows: List[Dict[str, Any]], currency_code: str) -> str:
    display_rows = _rows_with_cpc_display(rows, currency_code)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=TABLE_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in display_rows:
        writer.writerow({col: row.get(col, "") for col in TABLE_COLUMNS})
    return buf.getvalue().strip()


def _format_output(
    customer_id: str,
    rows: List[Dict[str, Any]],
    fmt: str,
    *,
    title: str,
    currency_code: str,
) -> str:
    fmt_l = (fmt or "table").lower()
    if fmt_l == "json":
        payload = {
            "customer_id": format_customer_id(customer_id),
            "currency_code": currency_code,
            "count": len(rows),
            "results": _rows_with_cpc_display(rows, currency_code),
        }
        return json.dumps(payload, indent=2, default=str)
    if fmt_l == "csv":
        return _format_keyword_rows_csv(rows, currency_code)
    return _format_keyword_rows_table(customer_id, rows, title=title, currency_code=currency_code)


def _extract_keyword_idea_results(data: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    raw_results = data.get("results") or []
    rows = [_normalize_keyword_idea_row(r) for r in raw_results]
    return rows[: min(max(int(limit), 1), 1000)]


def _extract_historical_metric_results(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_results = data.get("results") or []
    return [_normalize_keyword_idea_row(r) for r in raw_results]


def _format_geo_suggestions_table(customer_id: str, suggestions: List[Dict[str, Any]]) -> str:
    if not suggestions:
        return f"No geo target suggestions for Account {format_customer_id(customer_id)}."
    cols = ["search_term", "canonical_name", "resource_name", "target_type", "reach"]
    widths = {c: len(c) for c in cols}
    for s in suggestions:
        for c in cols:
            widths[c] = max(widths[c], len(str(s.get(c, ""))))
    header = " | ".join(f"{c:{widths[c]}}" for c in cols)
    lines = [
        f"Geo target suggestions for Account {format_customer_id(customer_id)}:",
        "-" * len(header),
        header,
        "-" * len(header),
    ]
    for s in suggestions:
        lines.append(" | ".join(f"{str(s.get(c, '')):{widths[c]}}" for c in cols))
    return "\n".join(lines)


def reset_keyword_plan_caches() -> None:
    """Clear in-process geo/language caches (for tests)."""
    _geo_target_cache.clear()
    _language_cache.clear()


@mcp.tool()
async def suggest_geo_targets(
    customer_id: Annotated[str, Field(description="Google Ads customer ID (10 digits, no dashes)")],
    location_names: Annotated[
        List[str], Field(description="Location names to resolve, e.g. ['Mumbai', 'Maharashtra', 'India']")
    ],
    country_code: Annotated[
        str, Field(description="Restrict suggestions to this ISO country code (default India)")
    ] = "IN",
    format: Annotated[str, Field(description="Output format: table, json, or csv")] = "table",
) -> str:
    """
    Resolve human-friendly location names to geoTargetConstants resource names.

    Use before generate_keyword_ideas or get_keyword_metrics to confirm targeting IDs.
    """
    try:
        if not location_names:
            return "location_names is required and cannot be empty."

        creds = get_credentials()
        data, error = _suggest_geo_target_constants_raw(
            customer_id,
            location_names,
            country_code=country_code,
            creds=creds,
        )
        if error:
            return f"Error suggesting geo targets: {_parse_api_error(error)}"

        suggestions_raw = (data or {}).get("geoTargetConstantSuggestions") or []
        suggestions = [_normalize_geo_suggestion(s) for s in suggestions_raw]

        # Warm cache for exact search terms
        fid = format_customer_id(customer_id)
        cc = country_code.upper()
        for s in suggestions:
            term = (s.get("search_term") or "").lower()
            rn = s.get("resource_name")
            if term and rn:
                _geo_target_cache[(fid, cc, term)] = rn

        fmt = (format or "table").lower()
        if fmt == "json":
            return json.dumps(
                {
                    "customer_id": fid,
                    "country_code": cc,
                    "suggestions": suggestions,
                },
                indent=2,
                default=str,
            )
        if fmt == "csv":
            buf = io.StringIO()
            cols = ["search_term", "canonical_name", "resource_name", "target_type", "reach"]
            writer = csv.DictWriter(buf, fieldnames=cols)
            writer.writeheader()
            for s in suggestions:
                writer.writerow({c: s.get(c, "") for c in cols})
            return buf.getvalue().strip()
        return _format_geo_suggestions_table(customer_id, suggestions)
    except Exception as e:
        logger.exception("suggest_geo_targets failed")
        return f"Error suggesting geo targets: {e}"


@mcp.tool()
async def generate_keyword_ideas(
    customer_id: Annotated[str, Field(description="Google Ads customer ID (10 digits, no dashes)")],
    seed_keywords: Annotated[
        Optional[List[str]],
        Field(description="Seed keywords/phrases, e.g. ['microsoft teams room mumbai', 'yealink dealer']"),
    ] = None,
    page_url: Annotated[Optional[str], Field(description="URL seed for a specific page")] = None,
    site_url: Annotated[Optional[str], Field(description="Site seed (domain), e.g. example.com")] = None,
    geo_targets: Annotated[
        Optional[List[str]],
        Field(description="Location names or geoTargetConstants resource names; default ['India']"),
    ] = None,
    language: Annotated[
        str, Field(description="Language name, ID, or languageConstants/…")
    ] = "English",
    network: Annotated[
        str, Field(description="GOOGLE_SEARCH or GOOGLE_SEARCH_AND_PARTNERS")
    ] = "GOOGLE_SEARCH",
    include_adult_keywords: Annotated[
        bool, Field(description="Include adult keywords in results")
    ] = False,
    limit: Annotated[int, Field(description="Max keyword ideas to return (1-1000)")] = 100,
    format: Annotated[str, Field(description="Output format: table, json, or csv")] = "table",
) -> str:
    """
    Keyword Planner-style discovery from seed keywords, a page URL, or a site domain.

    Returns search volume, competition, and top-of-page bid ranges for each idea.
    """
    try:
        creds = get_credentials()
        geo_list = geo_targets if geo_targets is not None else ["India"]
        geo_resources, geo_err = _resolve_geo_targets(
            customer_id, geo_list, country_code="IN", creds=creds
        )
        if geo_err:
            return f"Error resolving geo targets: {geo_err}"

        try:
            language_resource = _resolve_language_constant(language)
        except ValueError as e:
            return str(e)

        net = (network or "GOOGLE_SEARCH").upper()
        if net not in ("GOOGLE_SEARCH", "GOOGLE_SEARCH_AND_PARTNERS"):
            return "network must be GOOGLE_SEARCH or GOOGLE_SEARCH_AND_PARTNERS."

        body, body_err = _build_keyword_ideas_request_body(
            seed_keywords=seed_keywords,
            page_url=page_url,
            site_url=site_url,
            geo_resource_names=geo_resources,
            language_resource=language_resource,
            network=net,
            include_adult_keywords=include_adult_keywords,
            limit=limit,
        )
        if body_err:
            return body_err

        data, error = _generate_keyword_ideas_raw(customer_id, body, creds=creds)
        if error:
            return f"Error generating keyword ideas: {_parse_api_error(error)}"

        rows = _extract_keyword_idea_results(data or {}, limit)
        currency_code = _fetch_account_currency_code(customer_id, creds=creds)
        return _format_output(
            customer_id,
            rows,
            format,
            title="Keyword ideas",
            currency_code=currency_code,
        )
    except Exception as e:
        logger.exception("generate_keyword_ideas failed")
        return f"Error generating keyword ideas: {e}"


@mcp.tool()
async def get_keyword_metrics(
    customer_id: Annotated[str, Field(description="Google Ads customer ID (10 digits, no dashes)")],
    keywords: Annotated[
        List[str], Field(description="Keywords to fetch historical metrics for (max 1000)")
    ],
    geo_targets: Annotated[
        Optional[List[str]],
        Field(description="Location names or geoTargetConstants resource names; default ['India']"),
    ] = None,
    language: Annotated[
        str, Field(description="Language name, ID, or languageConstants/…")
    ] = "English",
    network: Annotated[
        str, Field(description="GOOGLE_SEARCH or GOOGLE_SEARCH_AND_PARTNERS")
    ] = "GOOGLE_SEARCH",
    format: Annotated[str, Field(description="Output format: table, json, or csv")] = "table",
) -> str:
    """
    Historical Keyword Planner metrics for a known keyword list.

    Equivalent to Planner “Get search volume and forecasts” for explicit keywords.
    """
    try:
        cleaned = [k.strip() for k in (keywords or []) if k and k.strip()]
        if not cleaned:
            return "keywords is required and cannot be empty."
        if len(cleaned) > 1000:
            return "keywords supports at most 1000 entries."

        creds = get_credentials()
        geo_list = geo_targets if geo_targets is not None else ["India"]
        geo_resources, geo_err = _resolve_geo_targets(
            customer_id, geo_list, country_code="IN", creds=creds
        )
        if geo_err:
            return f"Error resolving geo targets: {geo_err}"

        try:
            language_resource = _resolve_language_constant(language)
        except ValueError as e:
            return str(e)

        net = (network or "GOOGLE_SEARCH").upper()
        if net not in ("GOOGLE_SEARCH", "GOOGLE_SEARCH_AND_PARTNERS"):
            return "network must be GOOGLE_SEARCH or GOOGLE_SEARCH_AND_PARTNERS."

        body = {
            "keywords": cleaned,
            "geoTargetConstants": geo_resources,
            "language": language_resource,
            "keywordPlanNetwork": net,
        }

        data, error = _generate_keyword_historical_metrics_raw(customer_id, body, creds=creds)
        if error:
            return f"Error fetching keyword metrics: {_parse_api_error(error)}"

        rows = _extract_historical_metric_results(data or {})
        currency_code = _fetch_account_currency_code(customer_id, creds=creds)
        return _format_output(
            customer_id,
            rows,
            format,
            title="Keyword historical metrics",
            currency_code=currency_code,
        )
    except Exception as e:
        logger.exception("get_keyword_metrics failed")
        return f"Error fetching keyword metrics: {e}"
