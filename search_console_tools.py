"""
MCP tools for Google Search Console via the same service account as Google Ads / Sheets.

Uses webmasters.readonly scope — do not call get_credentials() (adwords scope only).
Add the service account email as a user on each GSC property.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from typing import Annotated, Any, Dict, List, Optional
from urllib.parse import quote

import requests
from google.oauth2 import service_account
from pydantic import Field

from google_ads_server import GOOGLE_ADS_CREDENTIALS_PATH, _load_credentials_dict_from_env, mcp

logger = logging.getLogger("google_ads_server")

GSC_READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GSC_FULL_SCOPE = "https://www.googleapis.com/auth/webmasters"
GSC_BASE_URL = "https://searchconsole.googleapis.com"

_gsc_credentials_cache: Optional[Any] = None


def reset_gsc_caches() -> None:
    global _gsc_credentials_cache
    _gsc_credentials_cache = None


def _gsc_scope() -> str:
    mode = (os.environ.get("GSC_SCOPE") or "readonly").strip().lower()
    return GSC_FULL_SCOPE if mode == "full" else GSC_READONLY_SCOPE


def get_gsc_credentials():
    """Load service account credentials with Search Console scope."""
    global _gsc_credentials_cache
    if _gsc_credentials_cache is not None:
        return _gsc_credentials_cache

    scopes = [_gsc_scope()]
    creds_dict = _load_credentials_dict_from_env()
    if creds_dict is not None:
        if creds_dict.get("type") != "service_account":
            raise ValueError(
                'GOOGLE_ADS_CREDENTIALS_JSON must be a service account key ({"type": "service_account", ...})'
            )
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=scopes,
        )
    elif GOOGLE_ADS_CREDENTIALS_PATH and os.path.exists(GOOGLE_ADS_CREDENTIALS_PATH):
        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_ADS_CREDENTIALS_PATH,
            scopes=scopes,
        )
    else:
        raise ValueError(
            "Set GOOGLE_ADS_CREDENTIALS_JSON or GOOGLE_ADS_CREDENTIALS_PATH for Search Console access"
        )

    _gsc_credentials_cache = credentials
    return credentials


def _gsc_access_token() -> str:
    creds = get_gsc_credentials()
    from google.auth.transport.requests import Request as GARequest

    creds.refresh(GARequest())
    return creds.token


def _json_response(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _default_dates(days: int = 28) -> tuple[str, str]:
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def _gsc_request(
    method: str,
    path: str,
    *,
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 60.0,
) -> tuple[int, Any]:
    token = _gsc_access_token()
    url = f"{GSC_BASE_URL}{path}"
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


def list_gsc_sites() -> Dict[str, Any]:
    status, payload = _gsc_request("GET", "/webmasters/v3/sites")
    if status >= 400:
        return {"error": payload, "status": status}
    entries = payload.get("siteEntry", []) if isinstance(payload, dict) else []
    return {
        "status": status,
        "count": len(entries),
        "sites": [
            {
                "siteUrl": item.get("siteUrl"),
                "permissionLevel": item.get("permissionLevel"),
            }
            for item in entries
        ],
    }


def query_gsc_search_analytics(
    site_url: str,
    *,
    dimensions: List[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    row_limit: int = 100,
    dimension_filter_groups: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    start, end = _default_dates()
    body: Dict[str, Any] = {
        "startDate": start_date or start,
        "endDate": end_date or end,
        "dimensions": dimensions,
        "rowLimit": max(1, min(int(row_limit), 25000)),
    }
    if dimension_filter_groups:
        body["dimensionFilterGroups"] = dimension_filter_groups

    encoded = quote(site_url, safe="")
    status, payload = _gsc_request(
        "POST",
        f"/webmasters/v3/sites/{encoded}/searchAnalytics/query",
        body=body,
    )
    if status >= 400:
        return {
            "error": payload,
            "status": status,
            "siteUrl": site_url,
            "dimensions": dimensions,
        }

    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    return {
        "status": status,
        "siteUrl": site_url,
        "startDate": body["startDate"],
        "endDate": body["endDate"],
        "dimensions": dimensions,
        "rowCount": len(rows),
        "rows": rows,
    }


def inspect_gsc_url(site_url: str, inspection_url: str) -> Dict[str, Any]:
    status, payload = _gsc_request(
        "POST",
        "/v1/urlInspection/index:inspect",
        body={"inspectionUrl": inspection_url, "siteUrl": site_url},
    )
    if status >= 400:
        return {
            "error": payload,
            "status": status,
            "siteUrl": site_url,
            "inspectionUrl": inspection_url,
        }
    result = payload.get("inspectionResult", {}) if isinstance(payload, dict) else {}
    index_status = result.get("indexStatusResult", {})
    return {
        "status": status,
        "siteUrl": site_url,
        "inspectionUrl": inspection_url,
        "verdict": index_status.get("verdict"),
        "coverageState": index_status.get("coverageState"),
        "indexingState": index_status.get("indexingState"),
        "lastCrawlTime": index_status.get("lastCrawlTime"),
        "pageFetchState": index_status.get("pageFetchState"),
        "robotsTxtState": index_status.get("robotsTxtState"),
        "inspectionResult": result,
    }


def list_gsc_sitemaps(site_url: str) -> Dict[str, Any]:
    encoded = quote(site_url, safe="")
    status, payload = _gsc_request("GET", f"/webmasters/v3/sites/{encoded}/sitemaps")
    if status >= 400:
        return {"error": payload, "status": status, "siteUrl": site_url}
    sitemaps = payload.get("sitemap", []) if isinstance(payload, dict) else []
    return {
        "status": status,
        "siteUrl": site_url,
        "count": len(sitemaps),
        "sitemaps": sitemaps,
    }


@mcp.tool()
async def list_search_console_sites() -> str:
    """
    List Google Search Console properties accessible to the service account.

    Use before querying analytics. The service account must be added as a user
    in GSC (Settings → Users and permissions) for each property.
    """
    try:
        return _json_response(list_gsc_sites())
    except Exception as exc:
        logger.exception("list_search_console_sites failed")
        return _json_response({"error": f"{type(exc).__name__}: {exc}"})


@mcp.tool()
async def get_search_console_analytics(
    site_url: Annotated[
        str,
        Field(
            description="GSC property URL exactly as shown in Search Console, e.g. https://example.com/ or sc-domain:example.com"
        ),
    ],
    dimension: Annotated[
        str,
        Field(description="One of: query, page, country, device, date, searchAppearance"),
    ] = "query",
    start_date: Annotated[
        Optional[str],
        Field(default=None, description="YYYY-MM-DD (default: 28 days ending 3 days ago)"),
    ] = None,
    end_date: Annotated[
        Optional[str],
        Field(default=None, description="YYYY-MM-DD (default: 3 days ago)"),
    ] = None,
    row_limit: Annotated[int, Field(description="Max rows (1-25000)", ge=1, le=25000)] = 100,
    query_contains: Annotated[
        Optional[str],
        Field(default=None, description="Optional filter: query dimension contains this text"),
    ] = None,
) -> str:
    """
    Pull Search Console performance data (clicks, impressions, CTR, position).

    Use for SEO audits, keyword opportunities, page-level performance, and cannibalization checks.
    """
    try:
        dims = [dimension.strip()]
        filters = None
        if query_contains and dimension == "query":
            filters = [
                {
                    "filters": [
                        {
                            "dimension": "query",
                            "operator": "contains",
                            "expression": query_contains,
                        }
                    ]
                }
            ]
        result = query_gsc_search_analytics(
            site_url,
            dimensions=dims,
            start_date=start_date,
            end_date=end_date,
            row_limit=row_limit,
            dimension_filter_groups=filters,
        )
        return _json_response(result)
    except Exception as exc:
        logger.exception("get_search_console_analytics failed")
        return _json_response({"error": f"{type(exc).__name__}: {exc}"})


@mcp.tool()
async def get_search_console_page_query_map(
    site_url: Annotated[str, Field(description="GSC property URL")],
    query_contains: Annotated[
        Optional[str],
        Field(default=None, description="Optional query filter (contains)"),
    ] = None,
    row_limit: Annotated[int, Field(description="Max rows", ge=1, le=25000)] = 250,
) -> str:
    """
    Pull page + query Search Console data for cannibalization and overlap analysis.

    Equivalent to dimensions=[page, query] in the Search Analytics API.
    """
    try:
        start, end = _default_dates()
        body: Dict[str, Any] = {
            "startDate": start,
            "endDate": end,
            "dimensions": ["page", "query"],
            "rowLimit": max(1, min(int(row_limit), 25000)),
        }
        if query_contains:
            body["dimensionFilterGroups"] = [
                {
                    "filters": [
                        {
                            "dimension": "query",
                            "operator": "contains",
                            "expression": query_contains,
                        }
                    ]
                }
            ]
        encoded = quote(site_url, safe="")
        status, payload = _gsc_request(
            "POST",
            f"/webmasters/v3/sites/{encoded}/searchAnalytics/query",
            body=body,
        )
        if status >= 400:
            return _json_response(
                {"error": payload, "status": status, "siteUrl": site_url}
            )
        rows = payload.get("rows", []) if isinstance(payload, dict) else []
        return _json_response(
            {
                "status": status,
                "siteUrl": site_url,
                "startDate": start,
                "endDate": end,
                "rowCount": len(rows),
                "rows": rows,
            }
        )
    except Exception as exc:
        logger.exception("get_search_console_page_query_map failed")
        return _json_response({"error": f"{type(exc).__name__}: {exc}"})


@mcp.tool()
async def inspect_search_console_url(
    site_url: Annotated[str, Field(description="GSC property URL")],
    inspection_url: Annotated[str, Field(description="Full URL to inspect, e.g. https://example.com/page")],
) -> str:
    """Inspect indexing status for a URL in Google Search Console."""
    try:
        return _json_response(inspect_gsc_url(site_url, inspection_url))
    except Exception as exc:
        logger.exception("inspect_search_console_url failed")
        return _json_response({"error": f"{type(exc).__name__}: {exc}"})


@mcp.tool()
async def list_search_console_sitemaps(
    site_url: Annotated[str, Field(description="GSC property URL")],
) -> str:
    """List sitemaps submitted for a Search Console property."""
    try:
        return _json_response(list_gsc_sitemaps(site_url))
    except Exception as exc:
        logger.exception("list_search_console_sitemaps failed")
        return _json_response({"error": f"{type(exc).__name__}: {exc}"})
