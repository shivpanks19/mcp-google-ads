"""
MCP tools for Google Sheets (read/write) via the same service account as Google Ads.

Uses spreadsheets scope — do not call get_credentials() (adwords scope only).
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from typing import Annotated, Any, Dict, List, Optional, Union
from urllib.parse import parse_qs, urlparse

from google.oauth2 import service_account
from googleapiclient.discovery import build
from pydantic import Field

from google_ads_server import GOOGLE_ADS_CREDENTIALS_PATH, _load_credentials_dict_from_env, mcp

logger = logging.getLogger("google_ads_server")

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_sheets_credentials_cache: Optional[Any] = None
_sheets_service_cache: Optional[Any] = None

GOOGLE_SHEETS_SPREADSHEET_ID = (os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID") or "").strip()
GOOGLE_SHEETS_EMAIL_LEADS_TAB = (
    os.environ.get("GOOGLE_SHEETS_EMAIL_LEADS_TAB") or "E-mail leads"
).strip()
GOOGLE_SHEETS_LEAD_PIPELINE_TAB = (
    os.environ.get("GOOGLE_SHEETS_LEAD_PIPELINE_TAB") or "Lead Pipeline"
).strip()

_STATUS_ALIASES: Dict[str, str] = {
    "na": "NA",
    "n/a": "NA",
    "nq": "NQ",
    "cold": "Cold",
    "exploring": "Exploring",
    "won": "Won",
    "lost": "Lost",
    "repeated": "Repeated/Existing",
    "existing": "Repeated/Existing",
    "not update": "Not Updated",
    "on hold": "On Hold",
    "support call": "Support",
}

_UNATTRIBUTED_UTM = frozenset(
    {"", "no link", "no utm link", "no utm link.", "no utm link ", "n/a"}
)


def reset_sheets_caches() -> None:
    """Clear in-process Sheets credential/service caches (for tests)."""
    global _sheets_credentials_cache, _sheets_service_cache
    _sheets_credentials_cache = None
    _sheets_service_cache = None


def get_sheets_credentials():
    """Load service account credentials with spreadsheets (read/write) scope."""
    global _sheets_credentials_cache
    if _sheets_credentials_cache is not None:
        return _sheets_credentials_cache

    creds_dict = _load_credentials_dict_from_env()
    if creds_dict is not None:
        if creds_dict.get("type") != "service_account":
            raise ValueError(
                'GOOGLE_ADS_CREDENTIALS_JSON must be a service account key ({"type": "service_account", ...})'
            )
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=SHEETS_SCOPES,
        )
    elif GOOGLE_ADS_CREDENTIALS_PATH and os.path.exists(GOOGLE_ADS_CREDENTIALS_PATH):
        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_ADS_CREDENTIALS_PATH,
            scopes=SHEETS_SCOPES,
        )
    else:
        raise ValueError(
            "Set GOOGLE_ADS_CREDENTIALS_JSON or GOOGLE_ADS_CREDENTIALS_PATH for Sheets access"
        )

    _sheets_credentials_cache = credentials
    return credentials


def create_sheets_service():
    """Build a cached Google Sheets API v4 client."""
    global _sheets_service_cache
    if _sheets_service_cache is not None:
        return _sheets_service_cache
    _sheets_service_cache = build(
        "sheets",
        "v4",
        credentials=get_sheets_credentials(),
        cache_discovery=False,
    )
    return _sheets_service_cache


def _sheet_range_a1(sheet_title: str, a1: str = "A:ZZ") -> str:
    escaped = sheet_title.replace("'", "''")
    return f"'{escaped}'!{a1}"


def _resolve_spreadsheet_id(spreadsheet_id: Optional[str]) -> str:
    sid = (spreadsheet_id or GOOGLE_SHEETS_SPREADSHEET_ID).strip()
    if not sid:
        raise ValueError("Provide spreadsheet_id or set GOOGLE_SHEETS_SPREADSHEET_ID")
    return sid


def _parse_values_json(values: Union[List[List[Any]], str]) -> List[List[Any]]:
    if isinstance(values, str):
        parsed = json.loads(values)
    else:
        parsed = values
    if not isinstance(parsed, list):
        raise ValueError("values must be a 2D array (list of rows)")
    return [["" if c is None else c for c in (row if isinstance(row, list) else [row])] for row in parsed]


def read_sheet_values(spreadsheet_id: str, range_a1: str) -> List[List[str]]:
    """Read raw cell values from a spreadsheet range."""
    service = create_sheets_service()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_a1)
        .execute()
    )
    return result.get("values") or []


def list_spreadsheet_tabs(spreadsheet_id: str) -> List[str]:
    """Return tab (sheet) titles for a spreadsheet."""
    service = create_sheets_service()
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title").execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def create_sheet_tab(spreadsheet_id: str, sheet_title: str) -> bool:
    """Create a worksheet tab if it does not exist. Returns True if created."""
    title = sheet_title.strip()
    if not title:
        raise ValueError("sheet_title is required")
    if title in list_spreadsheet_tabs(spreadsheet_id):
        return False
    service = create_sheets_service()
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
    ).execute()
    return True


def write_sheet_values(
    spreadsheet_id: str,
    range_a1: str,
    values: List[List[Any]],
    *,
    value_input_option: str = "USER_ENTERED",
) -> Dict[str, Any]:
    """Overwrite cells in range with a 2D value grid."""
    service = create_sheets_service()
    body = {"values": values, "majorDimension": "ROWS"}
    result = (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=range_a1,
            valueInputOption=value_input_option,
            body=body,
        )
        .execute()
    )
    return {
        "updatedRange": result.get("updatedRange"),
        "updatedRows": result.get("updatedRows"),
        "updatedColumns": result.get("updatedColumns"),
        "updatedCells": result.get("updatedCells"),
    }


def append_sheet_values(
    spreadsheet_id: str,
    range_a1: str,
    values: List[List[Any]],
    *,
    value_input_option: str = "USER_ENTERED",
) -> Dict[str, Any]:
    """Append rows after the last row of the range/table."""
    service = create_sheets_service()
    body = {"values": values, "majorDimension": "ROWS"}
    result = (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=range_a1,
            valueInputOption=value_input_option,
            insertDataOption="INSERT_ROWS",
            body=body,
        )
        .execute()
    )
    updates = result.get("updates") or {}
    return {
        "updatedRange": updates.get("updatedRange"),
        "updatedRows": updates.get("updatedRows"),
        "updatedColumns": updates.get("updatedColumns"),
        "updatedCells": updates.get("updatedCells"),
    }


def clear_sheet_range(spreadsheet_id: str, range_a1: str) -> Dict[str, Any]:
    """Clear values in a range (keeps formatting)."""
    service = create_sheets_service()
    result = (
        service.spreadsheets()
        .values()
        .clear(spreadsheetId=spreadsheet_id, range=range_a1, body={})
        .execute()
    )
    return {"clearedRange": result.get("clearedRange")}


def build_sheet_report(
    spreadsheet_id: str,
    sheet_title: str,
    headers: List[str],
    rows: List[List[Any]],
    *,
    report_title: Optional[str] = None,
    start_cell: str = "A1",
    clear_tab: bool = True,
    create_tab_if_missing: bool = True,
) -> Dict[str, Any]:
    """
    Write a tabular report: optional title row, header row, then data rows.

    Typical use: push Google Ads / Meta performance tables to a dashboard sheet.
    """
    title = sheet_title.strip()
    if create_tab_if_missing:
        create_sheet_tab(spreadsheet_id, title)

    grid: List[List[Any]] = []
    if report_title:
        grid.append([report_title])
    if headers:
        grid.append(headers)
    grid.extend(rows)

    if clear_tab:
        clear_sheet_range(spreadsheet_id, _sheet_range_a1(title))

    write_result = write_sheet_values(
        spreadsheet_id,
        _sheet_range_a1(title, start_cell),
        grid,
    )
    return {
        "spreadsheetId": spreadsheet_id,
        "sheetTitle": title,
        "rowsWritten": len(grid),
        "dataRows": len(rows),
        **write_result,
    }


def _json_response(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _header_index(headers: List[str]) -> Dict[str, int]:
    return {h.strip().lower(): i for i, h in enumerate(headers) if h and str(h).strip()}


def _cell(row: List[str], idx: int) -> str:
    if idx < 0 or idx >= len(row):
        return ""
    return str(row[idx]).strip()


def normalize_status(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "Blank"
    canonical = _STATUS_ALIASES.get(text.lower())
    if canonical:
        return canonical
    return text


def _is_unattributed_utm(utm: str) -> bool:
    return (utm or "").strip().lower() in _UNATTRIBUTED_UTM


def _parse_utm_params(utm_cell: str) -> Dict[str, str]:
    text = (utm_cell or "").strip()
    if not text or _is_unattributed_utm(text):
        return {}

    query = text
    if "://" in text or text.startswith("?"):
        parsed = urlparse(text if "://" in text else f"https://x.test/{text.lstrip('?')}")
        query = parsed.query or text.lstrip("?")

    params = parse_qs(query, keep_blank_values=False)
    out: Dict[str, str] = {}
    for key in ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"):
        vals = params.get(key)
        if vals and vals[0]:
            out[key] = vals[0].replace("+", " ").strip()
    return out


def _campaign_key(utm_cell: str, campaigns_fallback: str) -> str:
    if _is_unattributed_utm(utm_cell):
        return "(Unattributed)"
    params = _parse_utm_params(utm_cell)
    if params.get("utm_campaign"):
        return params["utm_campaign"]
    if campaigns_fallback:
        return campaigns_fallback
    return "(Unattributed)"


def _channel_key(utm_cell: str) -> str:
    if _is_unattributed_utm(utm_cell):
        return "direct/unknown"
    params = _parse_utm_params(utm_cell)
    source = (params.get("utm_source") or "").lower()
    if not source:
        return "direct/unknown"
    if source in ("google", "gclid"):
        return "google"
    if source in ("yt", "youtube"):
        return "YT"
    if source in ("ig", "instagram"):
        return "ig"
    if source in ("fb", "facebook"):
        return "fb"
    return source


def analyze_email_leads_rows(
    headers: List[str],
    rows: List[List[str]],
    month_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Aggregate E-mail leads tab rows into PM-ready summary JSON."""
    idx = _header_index(headers)
    status_i = idx.get("status", -1)
    utm_i = idx.get("utm", -1)
    campaigns_i = idx.get("campaigns", -1)
    month_i = idx.get("month", -1)

    month_norm = (month_filter or "").strip().lower() or None

    overall_by_status: Dict[str, int] = defaultdict(int)
    by_campaign: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_search_term: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_channel: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    total = 0
    attributed = 0

    for row in rows:
        if not any(str(c).strip() for c in row):
            continue
        month_val = _cell(row, month_i)
        if month_norm and month_val.lower() != month_norm:
            continue

        total += 1
        status = normalize_status(_cell(row, status_i))
        utm = _cell(row, utm_i)
        campaigns = _cell(row, campaigns_i)

        overall_by_status[status] += 1

        campaign = _campaign_key(utm, campaigns)
        if campaign != "(Unattributed)":
            attributed += 1

        by_campaign[campaign][status] += 1
        by_campaign[campaign]["Total"] += 1

        channel = _channel_key(utm)
        by_channel[channel][status] += 1
        by_channel[channel]["Total"] += 1

        params = _parse_utm_params(utm)
        term = (params.get("utm_term") or "").strip()
        if term:
            by_search_term[term][status] += 1
            by_search_term[term]["Total"] += 1

    def _with_win_rate(bucket: Dict[str, Dict[str, int]]) -> List[Dict[str, Any]]:
        rows_out: List[Dict[str, Any]] = []
        for name, counts in bucket.items():
            total_n = counts.get("Total", 0) or sum(
                v for k, v in counts.items() if k not in ("Total", "Win%")
            )
            won = counts.get("Won", 0)
            win_pct = round(100.0 * won / total_n, 1) if total_n else 0.0
            rows_out.append(
                {
                    "name": name,
                    "Total": total_n,
                    "Cold": counts.get("Cold", 0),
                    "Exploring": counts.get("Exploring", 0),
                    "Won": won,
                    "NQ": counts.get("NQ", 0),
                    "NA": counts.get("NA", 0),
                    "Win%": win_pct,
                }
            )
        rows_out.sort(key=lambda r: r["Total"], reverse=True)
        return rows_out

    won_total = overall_by_status.get("Won", 0)
    win_rate = round(100.0 * won_total / total, 2) if total else 0.0
    pipeline = overall_by_status.get("Cold", 0) + overall_by_status.get("Exploring", 0)
    disqualified = overall_by_status.get("NQ", 0) + overall_by_status.get("NA", 0)

    return {
        "totalLeads": total,
        "attributed": attributed,
        "unattributed": total - attributed,
        "attributedPct": round(100.0 * attributed / total, 1) if total else 0.0,
        "overallWinRatePct": win_rate,
        "activePipeline": pipeline,
        "disqualified": disqualified,
        "monthFilter": month_filter,
        "overallByStatus": dict(sorted(overall_by_status.items(), key=lambda x: -x[1])),
        "byCampaign": _with_win_rate(by_campaign),
        "bySearchTerm": _with_win_rate(by_search_term),
        "byChannel": _with_win_rate(by_channel),
    }


def analyze_email_leads_tab(
    spreadsheet_id: str,
    tab: Optional[str] = None,
    month_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Read and analyze the E-mail leads tab."""
    sheet_tab = tab or GOOGLE_SHEETS_EMAIL_LEADS_TAB
    values = read_sheet_values(spreadsheet_id, _sheet_range_a1(sheet_tab))
    if not values:
        return {"error": f"No data in tab '{sheet_tab}'", "spreadsheetId": spreadsheet_id}
    headers, *rows = values
    report = analyze_email_leads_rows(headers, rows, month_filter=month_filter)
    report["spreadsheetId"] = spreadsheet_id
    report["tab"] = sheet_tab
    return report


@mcp.tool()
async def list_sheet_tabs(
    spreadsheet_id: Annotated[
        Optional[str],
        Field(
            default=None,
            description="Google Sheets spreadsheet ID (defaults to GOOGLE_SHEETS_SPREADSHEET_ID env)",
        ),
    ] = None,
) -> str:
    """List worksheet tab names in a Google Spreadsheet."""
    sid = (spreadsheet_id or GOOGLE_SHEETS_SPREADSHEET_ID).strip()
    if not sid:
        return _json_response(
            {"error": "Provide spreadsheet_id or set GOOGLE_SHEETS_SPREADSHEET_ID"}
        )
    try:
        tabs = list_spreadsheet_tabs(sid)
        return _json_response({"spreadsheetId": sid, "tabs": tabs})
    except Exception as e:
        logger.exception("list_sheet_tabs failed")
        return _json_response({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
async def read_sheet_range(
    spreadsheet_id: Annotated[
        Optional[str],
        Field(default=None, description="Spreadsheet ID (defaults to GOOGLE_SHEETS_SPREADSHEET_ID)"),
    ] = None,
    sheet_title: Annotated[str, Field(description="Worksheet tab title (case-sensitive)")] = "E-mail leads",
    a1_range: Annotated[
        Optional[str],
        Field(default=None, description="A1 range within the tab, e.g. A1:Z100 (default A:ZZ)"),
    ] = None,
) -> str:
    """Read raw cell values from a spreadsheet tab range."""
    sid = (spreadsheet_id or GOOGLE_SHEETS_SPREADSHEET_ID).strip()
    if not sid:
        return _json_response(
            {"error": "Provide spreadsheet_id or set GOOGLE_SHEETS_SPREADSHEET_ID"}
        )
    a1 = a1_range or "A:ZZ"
    try:
        values = read_sheet_values(sid, _sheet_range_a1(sheet_title, a1))
        return _json_response(
            {
                "spreadsheetId": sid,
                "range": _sheet_range_a1(sheet_title, a1),
                "rowCount": len(values),
                "values": values,
            }
        )
    except Exception as e:
        logger.exception("read_sheet_range failed")
        return _json_response({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
async def read_email_leads(
    spreadsheet_id: Annotated[
        Optional[str],
        Field(default=None, description="Spreadsheet ID (defaults to GOOGLE_SHEETS_SPREADSHEET_ID)"),
    ] = None,
    tab: Annotated[
        Optional[str],
        Field(default=None, description="Tab name (defaults to GOOGLE_SHEETS_EMAIL_LEADS_TAB)"),
    ] = None,
    month_filter: Annotated[
        Optional[str],
        Field(default=None, description='Filter by Month column, e.g. "May 2025"'),
    ] = None,
) -> str:
    """
    Analyze the E-mail leads sheet: status distribution, campaign-wise Won/NQ/NA, UTM channels, search terms.

    Requires the spreadsheet shared with the service account email and Sheets API enabled in GCP.
    """
    sid = (spreadsheet_id or GOOGLE_SHEETS_SPREADSHEET_ID).strip()
    if not sid:
        return _json_response(
            {"error": "Provide spreadsheet_id or set GOOGLE_SHEETS_SPREADSHEET_ID"}
        )
    try:
        report = analyze_email_leads_tab(sid, tab=tab, month_filter=month_filter)
        return _json_response(report)
    except Exception as e:
        logger.exception("read_email_leads failed")
        return _json_response({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
async def write_sheet_range(
    sheet_title: Annotated[str, Field(description="Worksheet tab title (case-sensitive)")],
    values: Annotated[
        Union[List[List[Any]], str],
        Field(description="2D array of cell values, or JSON string of same"),
    ],
    spreadsheet_id: Annotated[
        Optional[str],
        Field(default=None, description="Spreadsheet ID (defaults to GOOGLE_SHEETS_SPREADSHEET_ID)"),
    ] = None,
    a1_range: Annotated[
        Optional[str],
        Field(default=None, description="Top-left A1 anchor, e.g. A1 (default). Range grows to fit values."),
    ] = None,
    value_input_option: Annotated[
        str,
        Field(description="RAW (literal strings) or USER_ENTERED (formulas/numbers parsed by Sheets)"),
    ] = "USER_ENTERED",
) -> str:
    """Write (overwrite) a block of cells on a worksheet tab."""
    try:
        sid = _resolve_spreadsheet_id(spreadsheet_id)
        grid = _parse_values_json(values)
        anchor = a1_range or "A1"
        result = write_sheet_values(
            sid,
            _sheet_range_a1(sheet_title, anchor),
            grid,
            value_input_option=value_input_option,
        )
        return _json_response({"spreadsheetId": sid, "sheetTitle": sheet_title, **result})
    except Exception as e:
        logger.exception("write_sheet_range failed")
        return _json_response({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
async def append_sheet_rows(
    sheet_title: Annotated[str, Field(description="Worksheet tab title (case-sensitive)")],
    rows: Annotated[
        Union[List[List[Any]], str],
        Field(description="Rows to append (2D array or JSON string)"),
    ],
    spreadsheet_id: Annotated[
        Optional[str],
        Field(default=None, description="Spreadsheet ID (defaults to GOOGLE_SHEETS_SPREADSHEET_ID)"),
    ] = None,
    value_input_option: Annotated[str, Field(description="RAW or USER_ENTERED")] = "USER_ENTERED",
) -> str:
    """Append rows to the bottom of a worksheet tab (after existing data)."""
    try:
        sid = _resolve_spreadsheet_id(spreadsheet_id)
        grid = _parse_values_json(rows)
        result = append_sheet_values(
            sid,
            _sheet_range_a1(sheet_title, "A1"),
            grid,
            value_input_option=value_input_option,
        )
        return _json_response(
            {"spreadsheetId": sid, "sheetTitle": sheet_title, "appendedRows": len(grid), **result}
        )
    except Exception as e:
        logger.exception("append_sheet_rows failed")
        return _json_response({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
async def clear_sheet_tab(
    sheet_title: Annotated[str, Field(description="Worksheet tab to clear")],
    spreadsheet_id: Annotated[
        Optional[str],
        Field(default=None, description="Spreadsheet ID (defaults to GOOGLE_SHEETS_SPREADSHEET_ID)"),
    ] = None,
    a1_range: Annotated[
        Optional[str],
        Field(default=None, description="Optional sub-range to clear (default: entire tab)"),
    ] = None,
) -> str:
    """Clear cell values on a tab or sub-range before writing a fresh report."""
    try:
        sid = _resolve_spreadsheet_id(spreadsheet_id)
        a1 = a1_range or "A:ZZ"
        result = clear_sheet_range(sid, _sheet_range_a1(sheet_title, a1))
        return _json_response({"spreadsheetId": sid, "sheetTitle": sheet_title, **result})
    except Exception as e:
        logger.exception("clear_sheet_tab failed")
        return _json_response({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
async def create_sheet_tab_tool(
    sheet_title: Annotated[str, Field(description="New worksheet tab name")],
    spreadsheet_id: Annotated[
        Optional[str],
        Field(default=None, description="Spreadsheet ID (defaults to GOOGLE_SHEETS_SPREADSHEET_ID)"),
    ] = None,
) -> str:
    """Create a new worksheet tab if it does not already exist."""
    try:
        sid = _resolve_spreadsheet_id(spreadsheet_id)
        created = create_sheet_tab(sid, sheet_title)
        return _json_response(
            {
                "spreadsheetId": sid,
                "sheetTitle": sheet_title,
                "created": created,
                "message": "Tab created" if created else "Tab already exists",
            }
        )
    except Exception as e:
        logger.exception("create_sheet_tab_tool failed")
        return _json_response({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
async def write_sheet_report(
    sheet_title: Annotated[str, Field(description="Target worksheet tab name")],
    headers: Annotated[
        Union[List[str], str],
        Field(description="Column header row (list or JSON array string)"),
    ],
    rows: Annotated[
        Union[List[List[Any]], str],
        Field(description="Data rows (2D array or JSON string)"),
    ],
    spreadsheet_id: Annotated[
        Optional[str],
        Field(default=None, description="Spreadsheet ID (defaults to GOOGLE_SHEETS_SPREADSHEET_ID)"),
    ] = None,
    report_title: Annotated[
        Optional[str],
        Field(default=None, description="Optional title row above headers, e.g. 'Google Ads — Week of May 19'"),
    ] = None,
    clear_tab: Annotated[
        bool,
        Field(description="Clear the tab before writing (recommended for dashboard refreshes)"),
    ] = True,
    create_tab_if_missing: Annotated[
        bool,
        Field(description="Create the tab if it does not exist"),
    ] = True,
) -> str:
    """
    Write a full tabular report to Google Sheets (title + headers + rows).

    Use after fetching Google Ads / Meta metrics to push a dashboard refresh to the
    spreadsheet configured in GOOGLE_SHEETS_SPREADSHEET_ID (or pass spreadsheet_id).
    Service account needs Editor access on the spreadsheet.
    """
    try:
        sid = _resolve_spreadsheet_id(spreadsheet_id)
        if isinstance(headers, str):
            header_row = json.loads(headers)
        else:
            header_row = list(headers)
        if not isinstance(header_row, list):
            raise ValueError("headers must be a list of column names")
        data_rows = _parse_values_json(rows)
        result = build_sheet_report(
            sid,
            sheet_title,
            header_row,
            data_rows,
            report_title=report_title,
            clear_tab=clear_tab,
            create_tab_if_missing=create_tab_if_missing,
        )
        return _json_response(result)
    except Exception as e:
        logger.exception("write_sheet_report failed")
        return _json_response({"error": f"{type(e).__name__}: {e}"})
