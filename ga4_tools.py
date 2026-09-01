"""
MCP tools for Google Analytics 4 via the same service account as Google Ads / Sheets / GSC.

Uses analytics.readonly scope — do not call get_credentials() (adwords scope only).
Add the service account email as Viewer on each GA4 property (Admin → Property access).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Annotated, Any, Dict, List, Optional

import requests
from google.oauth2 import service_account
from pydantic import Field

from google_ads_server import GOOGLE_ADS_CREDENTIALS_PATH, _load_credentials_dict_from_env, mcp

logger = logging.getLogger("google_ads_server")

GA4_READONLY_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
GA4_DATA_API = "https://analyticsdata.googleapis.com/v1beta"
GA4_ADMIN_API = "https://analyticsadmin.googleapis.com/v1beta"

_ga4_credentials_cache: Optional[Any] = None

GOOGLE_GA4_PROPERTY_ID = (os.environ.get("GOOGLE_GA4_PROPERTY_ID") or "").strip()


def reset_ga4_caches() -> None:
    global _ga4_credentials_cache
    _ga4_credentials_cache = None


def get_ga4_credentials():
    """Load service account credentials with GA4 read scope."""
    global _ga4_credentials_cache
    if _ga4_credentials_cache is not None:
        return _ga4_credentials_cache

    creds_dict = _load_credentials_dict_from_env()
    if creds_dict is not None:
        if creds_dict.get("type") != "service_account":
            raise ValueError(
                'GOOGLE_ADS_CREDENTIALS_JSON must be a service account key ({"type": "service_account", ...})'
            )
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=[GA4_READONLY_SCOPE],
        )
    elif GOOGLE_ADS_CREDENTIALS_PATH and os.path.exists(GOOGLE_ADS_CREDENTIALS_PATH):
        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_ADS_CREDENTIALS_PATH,
            scopes=[GA4_READONLY_SCOPE],
        )
    else:
        raise ValueError(
            "Set GOOGLE_ADS_CREDENTIALS_JSON or GOOGLE_ADS_CREDENTIALS_PATH for GA4 access"
        )

    _ga4_credentials_cache = credentials
    return credentials


def _ga4_access_token() -> str:
    creds = get_ga4_credentials()
    from google.auth.transport.requests import Request as GARequest

    creds.refresh(GARequest())
    return creds.token


def _json_response(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _normalize_property_id(property_id: str) -> str:
    raw = (property_id or "").strip()
    if not raw:
        raise ValueError("property_id is required")
    if raw.startswith("properties/"):
        return raw.split("/", 1)[1]
    return re.sub(r"\D", "", raw)


def _property_path(property_id: Optional[str]) -> str:
    pid = _normalize_property_id(property_id or GOOGLE_GA4_PROPERTY_ID)
    if not pid:
        raise ValueError("Provide property_id or set GOOGLE_GA4_PROPERTY_ID")
    return pid


def _ga4_request(
    method: str,
    base_url: str,
    path: str,
    *,
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 60.0,
) -> tuple[int, Any]:
    token = _ga4_access_token()
    url = f"{base_url}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    response = requests.request(
        method,
        url,
        headers=headers,
        json=body,
        timeout=timeout,
    )
    if not response.text:
        return response.status_code, {}
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {"raw": response.text[:2000]}


def _parse_report_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    dim_headers = [h.get("name") for h in payload.get("dimensionHeaders", [])]
    metric_headers = [h.get("name") for h in payload.get("metricHeaders", [])]
    rows_out: List[Dict[str, Any]] = []
    for row in payload.get("rows", []):
        item: Dict[str, Any] = {}
        dim_vals = row.get("dimensionValues") or []
        for i, name in enumerate(dim_headers):
            if name and i < len(dim_vals):
                item[name] = dim_vals[i].get("value")
        metrics: Dict[str, Any] = {}
        metric_vals = row.get("metricValues") or []
        for i, name in enumerate(metric_headers):
            if name and i < len(metric_vals):
                metrics[name] = metric_vals[i].get("value")
        item["metrics"] = metrics
        rows_out.append(item)
    return rows_out


def list_ga4_account_summaries() -> Dict[str, Any]:
    status, payload = _ga4_request("GET", GA4_ADMIN_API, "/accountSummaries")
    if status >= 400:
        return {"error": payload, "status": status}
    summaries = payload.get("accountSummaries", []) if isinstance(payload, dict) else []
    properties: List[Dict[str, str]] = []
    for account in summaries:
        for prop in account.get("propertySummaries", []):
            properties.append(
                {
                    "account": account.get("account", ""),
                    "accountDisplayName": account.get("displayName", ""),
                    "property": prop.get("property", ""),
                    "propertyDisplayName": prop.get("displayName", ""),
                    "propertyId": (prop.get("property") or "").replace("properties/", ""),
                }
            )
    return {"status": status, "count": len(properties), "properties": properties}


def execute_ga4_report(
    property_id: str,
    *,
    start_date: str = "28daysAgo",
    end_date: str = "today",
    dimensions: Optional[List[str]] = None,
    metrics: Optional[List[str]] = None,
    limit: int = 100,
    dimension_filter: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pid = _property_path(property_id)
    body: Dict[str, Any] = {
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "limit": max(1, min(int(limit), 100000)),
    }
    if dimensions:
        body["dimensions"] = [{"name": d.strip()} for d in dimensions if d.strip()]
    if metrics:
        body["metrics"] = [{"name": m.strip()} for m in metrics if m.strip()]
    if dimension_filter:
        body["dimensionFilter"] = dimension_filter

    status, payload = _ga4_request(
        "POST",
        GA4_DATA_API,
        f"/properties/{pid}:runReport",
        body=body,
    )
    if status >= 400:
        return {"error": payload, "status": status, "propertyId": pid}
    rows = _parse_report_rows(payload if isinstance(payload, dict) else {})
    return {
        "status": status,
        "propertyId": pid,
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dimensions or [],
        "metrics": metrics or [],
        "rowCount": len(rows),
        "rows": rows,
        "totals": payload.get("totals") if isinstance(payload, dict) else None,
    }


def fetch_ga4_conversion_events(property_id: str) -> Dict[str, Any]:
    pid = _property_path(property_id)
    status, payload = _ga4_request(
        "GET",
        GA4_ADMIN_API,
        f"/properties/{pid}/conversionEvents",
    )
    if status >= 400:
        return {"error": payload, "status": status, "propertyId": pid}
    events = payload.get("conversionEvents", []) if isinstance(payload, dict) else []
    return {
        "status": status,
        "propertyId": pid,
        "count": len(events),
        "conversionEvents": [
            {
                "name": e.get("name"),
                "eventName": e.get("eventName"),
                "createTime": e.get("createTime"),
                "deletable": e.get("deletable"),
                "custom": e.get("custom"),
            }
            for e in events
        ],
    }


@mcp.tool()
async def list_ga4_properties() -> str:
    """
    List GA4 properties accessible to the service account.

    Use before running reports. Add the service account as Viewer on the property
    in GA4 Admin → Property access management.
    """
    try:
        return _json_response(list_ga4_account_summaries())
    except Exception as exc:
        logger.exception("list_ga4_properties failed")
        return _json_response({"error": f"{type(exc).__name__}: {exc}"})


@mcp.tool()
async def run_ga4_report(
    property_id: Annotated[
        Optional[str],
        Field(
            default=None,
            description="GA4 numeric property ID or properties/123456789 (defaults to GOOGLE_GA4_PROPERTY_ID env)",
        ),
    ] = None,
    start_date: Annotated[str, Field(description="YYYY-MM-DD or relative e.g. 28daysAgo")] = "28daysAgo",
    end_date: Annotated[str, Field(description="YYYY-MM-DD or today")] = "today",
    dimensions: Annotated[
        Optional[str],
        Field(default=None, description="Comma-separated dimension names, e.g. landingPage,sessionSource"),
    ] = None,
    metrics: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Comma-separated metric names, e.g. sessions,conversions,engagementRate",
        ),
    ] = None,
    limit: Annotated[int, Field(description="Max rows", ge=1, le=100000)] = 100,
) -> str:
    """
    Run a flexible GA4 Data API report.

    Common dimensions: sessionSource, sessionMedium, sessionCampaignName, landingPage,
    pagePath, deviceCategory, country. Common metrics: sessions, activeUsers, conversions,
    engagementRate, averageSessionDuration, bounceRate.
    """
    try:
        dim_list = [d.strip() for d in (dimensions or "").split(",") if d.strip()] or None
        metric_list = [m.strip() for m in (metrics or "").split(",") if m.strip()] or None
        if not metric_list:
            metric_list = ["sessions", "activeUsers", "conversions"]
        result = execute_ga4_report(
            _property_path(property_id),
            start_date=start_date,
            end_date=end_date,
            dimensions=dim_list,
            metrics=metric_list,
            limit=limit,
        )
        return _json_response(result)
    except Exception as exc:
        logger.exception("run_ga4_report tool failed")
        return _json_response({"error": f"{type(exc).__name__}: {exc}"})


@mcp.tool()
async def get_ga4_landing_page_performance(
    property_id: Annotated[
        Optional[str],
        Field(default=None, description="GA4 property ID (defaults to GOOGLE_GA4_PROPERTY_ID)"),
    ] = None,
    start_date: Annotated[str, Field(description="Start date")] = "28daysAgo",
    end_date: Annotated[str, Field(description="End date")] = "today",
    limit: Annotated[int, Field(description="Max landing pages", ge=1, le=500)] = 25,
) -> str:
    """Landing page sessions, users, conversions, and engagement for LP optimization."""
    try:
        result = execute_ga4_report(
            _property_path(property_id),
            start_date=start_date,
            end_date=end_date,
            dimensions=["landingPage"],
            metrics=["sessions", "activeUsers", "conversions", "engagementRate", "averageSessionDuration"],
            limit=limit,
        )
        return _json_response(result)
    except Exception as exc:
        logger.exception("get_ga4_landing_page_performance failed")
        return _json_response({"error": f"{type(exc).__name__}: {exc}"})


@mcp.tool()
async def get_ga4_traffic_acquisition(
    property_id: Annotated[
        Optional[str],
        Field(default=None, description="GA4 property ID (defaults to GOOGLE_GA4_PROPERTY_ID)"),
    ] = None,
    start_date: Annotated[str, Field(description="Start date")] = "28daysAgo",
    end_date: Annotated[str, Field(description="End date")] = "today",
    limit: Annotated[int, Field(description="Max rows", ge=1, le=500)] = 50,
) -> str:
    """Sessions and conversions by source / medium / campaign."""
    try:
        result = execute_ga4_report(
            _property_path(property_id),
            start_date=start_date,
            end_date=end_date,
            dimensions=["sessionSource", "sessionMedium", "sessionCampaignName"],
            metrics=["sessions", "activeUsers", "conversions", "engagementRate"],
            limit=limit,
        )
        return _json_response(result)
    except Exception as exc:
        logger.exception("get_ga4_traffic_acquisition failed")
        return _json_response({"error": f"{type(exc).__name__}: {exc}"})


@mcp.tool()
async def get_ga4_organic_vs_paid(
    property_id: Annotated[
        Optional[str],
        Field(default=None, description="GA4 property ID (defaults to GOOGLE_GA4_PROPERTY_ID)"),
    ] = None,
    start_date: Annotated[str, Field(description="Start date")] = "28daysAgo",
    end_date: Annotated[str, Field(description="End date")] = "today",
) -> str:
    """Compare organic vs paid (cpc) session and conversion totals."""
    try:
        result = execute_ga4_report(
            _property_path(property_id),
            start_date=start_date,
            end_date=end_date,
            dimensions=["sessionDefaultChannelGroup"],
            metrics=["sessions", "activeUsers", "conversions", "engagementRate"],
            limit=20,
        )
        return _json_response(result)
    except Exception as exc:
        logger.exception("get_ga4_organic_vs_paid failed")
        return _json_response({"error": f"{type(exc).__name__}: {exc}"})


@mcp.tool()
async def list_ga4_conversion_events(
    property_id: Annotated[
        Optional[str],
        Field(default=None, description="GA4 property ID (defaults to GOOGLE_GA4_PROPERTY_ID)"),
    ] = None,
) -> str:
    """List conversion events configured on a GA4 property."""
    try:
        return _json_response(fetch_ga4_conversion_events(_property_path(property_id)))
    except Exception as exc:
        logger.exception("list_ga4_conversion_events failed")
        return _json_response({"error": f"{type(exc).__name__}: {exc}"})
