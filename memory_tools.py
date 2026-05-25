"""
MCP tools for Supabase-backed AI memory and report snapshots.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union

from pydantic import Field

import supabase_store as store
from google_ads_server import mcp


def _json_response(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
async def save_client_context(
    customer_id: str = Field(description="Google Ads customer ID (10 digits, no dashes)"),
    descriptive_name: Optional[str] = Field(default=None, description="Account display name"),
    currency_code: Optional[str] = Field(default=None, description="ISO currency code, e.g. INR, USD"),
    aliases: Optional[Union[List[str], str]] = Field(
        default=None,
        description="Alternate names for matching (JSON array string or list), e.g. ['EyeRIS']",
    ),
    notes: Optional[str] = Field(default=None, description="Long-form client notes"),
    metadata: Optional[Union[Dict[str, Any], str]] = Field(
        default=None,
        description="Arbitrary JSON metadata (dict or JSON string)",
    ),
) -> str:
    """
    Upsert client/account context in Supabase for AI memory.

    Use after resolving a Google Ads account (list_accounts + GAQL name lookup).
    """
    try:
        row = store.upsert_client(
            customer_id=customer_id,
            descriptive_name=descriptive_name,
            currency_code=currency_code,
            aliases=aliases,
            notes=notes,
            metadata=metadata,
        )
        return _json_response({"status": "saved", "client": row})
    except store.SupabaseNotConfiguredError as e:
        return str(e)
    except Exception as e:
        return _json_response({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
async def recall_client_context(
    customer_id: Optional[str] = Field(default=None, description="10-digit Google Ads customer ID"),
    query: Optional[str] = Field(
        default=None,
        description="Fuzzy match on descriptive_name or aliases when customer_id is unknown",
    ),
    memory_limit: int = Field(default=20, description="Max recent memory entries to include"),
) -> str:
    """
    Recall saved client profile and recent memory entries from Supabase.

    Provide customer_id OR query (not both required, but at least one).
    """
    try:
        if not customer_id and not query:
            return _json_response({"error": "Provide customer_id or query"})
        data = store.recall_client_context(
            customer_id=customer_id,
            query=query,
            memory_limit=memory_limit,
        )
        return _json_response(data)
    except store.SupabaseNotConfiguredError as e:
        return str(e)
    except Exception as e:
        return _json_response({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
async def save_memory(
    content: str = Field(description="Memory body: insight, decision, audit finding, etc."),
    entry_type: str = Field(
        default="insight",
        description="One of: context, insight, decision, audit",
    ),
    customer_id: Optional[str] = Field(
        default=None,
        description="Link memory to a client (must exist via save_client_context)",
    ),
    title: Optional[str] = Field(default=None, description="Short headline"),
    tags: Optional[Union[List[str], str]] = Field(
        default=None,
        description="Tags for filtering (list or JSON array string)",
    ),
    source: Optional[str] = Field(default=None, description="Origin, e.g. cursor, weekly-review"),
) -> str:
    """
    Save a session insight or decision to Supabase AI memory.
    """
    try:
        row = store.insert_memory(
            content=content,
            entry_type=entry_type,
            customer_id=customer_id,
            title=title,
            tags=tags,
            source=source,
        )
        return _json_response({"status": "saved", "memory": row})
    except store.SupabaseNotConfiguredError as e:
        return str(e)
    except Exception as e:
        return _json_response({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
async def search_memory(
    customer_id: Optional[str] = Field(default=None, description="Filter by Google Ads customer ID"),
    entry_type: Optional[str] = Field(default=None, description="context | insight | decision | audit"),
    tags: Optional[Union[List[str], str]] = Field(default=None, description="Match any of these tags"),
    q: Optional[str] = Field(default=None, description="Keyword search in title and content"),
    since: Optional[str] = Field(default=None, description="Created on/after (YYYY-MM-DD)"),
    until: Optional[str] = Field(default=None, description="Created on/before (YYYY-MM-DD)"),
    limit: int = Field(default=50, description="Max rows to return"),
) -> str:
    """
    Search saved memory entries with optional filters.
    """
    try:
        rows = store.search_memory(
            customer_id=customer_id,
            entry_type=entry_type,
            tags=tags,
            q=q,
            since=since,
            until=until,
            limit=limit,
        )
        return _json_response({"count": len(rows), "entries": rows})
    except store.SupabaseNotConfiguredError as e:
        return str(e)
    except Exception as e:
        return _json_response({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
async def save_report_snapshot(
    customer_id: str = Field(description="Google Ads customer ID (10 digits)"),
    report_type: str = Field(
        description="Snapshot kind, e.g. campaign_performance, weekly_summary, custom_gaql",
    ),
    period_start: Optional[str] = Field(default=None, description="Period start YYYY-MM-DD"),
    period_end: Optional[str] = Field(default=None, description="Period end YYYY-MM-DD"),
    metrics: Union[Dict[str, Any], str] = Field(
        description="Normalized metrics JSON (dict or JSON string)",
    ),
    summary: Optional[str] = Field(default=None, description="Executive summary text"),
) -> str:
    """
    Persist a report snapshot for period-over-period comparison in Supabase.
    """
    try:
        row = store.insert_report_snapshot(
            customer_id=customer_id,
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
            metrics=metrics,
            summary=summary,
        )
        return _json_response({"status": "saved", "snapshot": row})
    except store.SupabaseNotConfiguredError as e:
        return str(e)
    except Exception as e:
        return _json_response({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
async def list_report_snapshots(
    customer_id: str = Field(description="Google Ads customer ID (10 digits)"),
    report_type: Optional[str] = Field(default=None, description="Filter by report_type"),
    period_start: Optional[str] = Field(default=None, description="Snapshots with period_start >= this date"),
    period_end: Optional[str] = Field(default=None, description="Snapshots with period_end <= this date"),
    limit: int = Field(default=20, description="Max snapshots to return"),
) -> str:
    """
    List saved report snapshots for a client, newest first.
    """
    try:
        rows = store.list_report_snapshots(
            customer_id=customer_id,
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
            limit=limit,
        )
        return _json_response({"count": len(rows), "snapshots": rows})
    except store.SupabaseNotConfiguredError as e:
        return str(e)
    except Exception as e:
        return _json_response({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
async def save_analysis_text_snapshot(
    customer_id: str = Field(description="Google Ads customer ID (10 digits)"),
    analysis_type: str = Field(
        description=(
            "Report category slug, e.g. campaign_performance_analysis, "
            "ad_performance_analysis, impression_share_analysis, custom_label"
        ),
    ),
    body: str = Field(description="Full analysis text (Markdown or plain text)"),
    title: Optional[str] = Field(
        default=None,
        description="Optional short headline shown in listings",
    ),
    metadata: Optional[Union[Dict[str, Any], str]] = Field(
        default=None,
        description="Optional JSON: e.g. days compared, currency, model, source links",
    ),
) -> str:
    """
    Save a timestamped narrative analysis to Supabase (table ``analysis_text_snapshots``).

    Each save is a new row; ``created_at`` is assigned by the database.
    Requires migration ``002_analysis_text_snapshots.sql`` applied when using this table.
    """
    try:
        row = store.insert_analysis_text_snapshot(
            customer_id=customer_id,
            analysis_type=analysis_type,
            body=body,
            title=title,
            metadata=metadata,
            auto_upsert_client=True,
        )
        return _json_response({"status": "saved", "analysis": row})
    except store.SupabaseNotConfiguredError as e:
        return str(e)
    except Exception as e:
        return _json_response({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
async def list_analysis_text_snapshots(
    customer_id: str = Field(description="Google Ads customer ID (10 digits)"),
    analysis_type: Optional[str] = Field(
        default=None,
        description="Filter by analysis_type (exact match)",
    ),
    since: Optional[str] = Field(default=None, description="created_at on/after YYYY-MM-DD"),
    until: Optional[str] = Field(default=None, description="created_at on/before YYYY-MM-DD"),
    limit: int = Field(default=30, description="Max rows, newest first"),
) -> str:
    """
    List saved narrative analyses for a client (newest ``created_at`` first).
    """
    try:
        rows = store.list_analysis_text_snapshots(
            customer_id=customer_id,
            analysis_type=analysis_type,
            since=since,
            until=until,
            limit=limit,
        )
        return _json_response({"count": len(rows), "analyses": rows})
    except store.SupabaseNotConfiguredError as e:
        return str(e)
    except Exception as e:
        return _json_response({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
async def sync_list_accounts_to_supabase(
    notes: Optional[str] = Field(
        default=None,
        description="Optional notes written to each upserted google_ads_clients row when provided",
    ),
) -> str:
    """
    Call Google Ads list_accounts, then upsert every returned customer ID into Supabase
    (google_ads_clients). Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.

    Useful to seed client rows before save_memory / save_report_snapshot / save_analysis_text_snapshot.
    """
    try:
        from google_ads_server import list_accounts

        listing = await list_accounts()
        out = store.sync_list_accounts_output_to_clients(listing, notes=notes)
        return _json_response(out)
    except store.SupabaseNotConfiguredError as e:
        return str(e)
    except Exception as e:
        return _json_response({"error": f"{type(e).__name__}: {e}"})
