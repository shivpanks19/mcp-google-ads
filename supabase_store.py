"""
Supabase persistence for Google Ads MCP: client context, session memory, report snapshots.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

from supabase import Client, create_client

SUPABASE_NOT_CONFIGURED = (
    "SUPABASE_NOT_CONFIGURED: Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env "
    "(or Railway variables) and run supabase/migrations/001_initial.sql and "
    "supabase/migrations/002_analysis_text_snapshots.sql in your project."
)

VALID_ENTRY_TYPES = frozenset({"context", "insight", "decision", "audit"})

_client: Optional[Client] = None


class SupabaseNotConfiguredError(RuntimeError):
    """Raised when Supabase env vars are missing."""

    def __init__(self) -> None:
        super().__init__(SUPABASE_NOT_CONFIGURED)


def is_configured() -> bool:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return bool(url and key)


def normalize_customer_id(customer_id: str) -> str:
    """Strip dashes/spaces; return 10-digit customer ID."""
    digits = re.sub(r"\D", "", customer_id or "")
    if len(digits) != 10:
        raise ValueError(f"customer_id must be 10 digits, got: {customer_id!r}")
    return digits


def get_client() -> Client:
    global _client
    if not is_configured():
        raise SupabaseNotConfiguredError()
    if _client is None:
        _client = create_client(
            os.environ["SUPABASE_URL"].strip(),
            os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip(),
        )
    return _client


def reset_client() -> None:
    """Clear cached client (for tests)."""
    global _client
    _client = None


def _parse_date(value: Optional[str]) -> Optional[str]:
    if value is None or not str(value).strip():
        return None
    s = str(value).strip()
    datetime.strptime(s, "%Y-%m-%d")
    return s


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if isinstance(row, dict):
        return row
    return dict(row)


def _ensure_list(value: Optional[Union[List[str], str]]) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            return [value.strip()] if value.strip() else []
        return [value.strip()] if value.strip() else []
    return [str(x) for x in value]


def _ensure_dict(value: Optional[Union[Dict[str, Any], str]]) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def upsert_client(
    customer_id: str,
    descriptive_name: Optional[str] = None,
    currency_code: Optional[str] = None,
    aliases: Optional[Union[List[str], str]] = None,
    notes: Optional[str] = None,
    metadata: Optional[Union[Dict[str, Any], str]] = None,
) -> Dict[str, Any]:
    cid = normalize_customer_id(customer_id)
    sb = get_client()

    existing = (
        sb.table("google_ads_clients")
        .select("*")
        .eq("customer_id", cid)
        .limit(1)
        .execute()
    )
    rows = existing.data or []

    payload: Dict[str, Any] = {"customer_id": cid}
    if descriptive_name is not None:
        payload["descriptive_name"] = descriptive_name
    if currency_code is not None:
        payload["currency_code"] = currency_code
    if aliases is not None:
        payload["aliases"] = _ensure_list(aliases)
    if notes is not None:
        payload["notes"] = notes
    if metadata is not None:
        payload["metadata"] = _ensure_dict(metadata)

    if rows:
        row_id = rows[0]["id"]
        update_payload = {k: v for k, v in payload.items() if k != "customer_id"}
        if update_payload:
            result = (
                sb.table("google_ads_clients")
                .update(update_payload)
                .eq("id", row_id)
                .execute()
            )
            if result.data:
                return _row_to_dict(result.data[0])
        return _row_to_dict(rows[0])

    insert_payload = {
        "customer_id": cid,
        "descriptive_name": descriptive_name,
        "currency_code": currency_code,
        "aliases": _ensure_list(aliases),
        "notes": notes,
        "metadata": _ensure_dict(metadata),
    }
    result = sb.table("google_ads_clients").insert(insert_payload).execute()
    if not result.data:
        raise RuntimeError("Failed to insert google_ads_clients row")
    return _row_to_dict(result.data[0])


def get_client_by_customer_id(customer_id: str) -> Optional[Dict[str, Any]]:
    cid = normalize_customer_id(customer_id)
    sb = get_client()
    result = (
        sb.table("google_ads_clients")
        .select("*")
        .eq("customer_id", cid)
        .limit(1)
        .execute()
    )
    if result.data:
        return _row_to_dict(result.data[0])
    return None


def parse_account_ids_from_list_accounts_output(list_accounts_text: str) -> List[str]:
    """
    Parse lines like 'Account ID: 1234567890' from list_accounts() tool output.

    Returns stable 10-digit customer IDs in file order (no dedup).
    """
    ids: List[str] = []
    for line in (list_accounts_text or "").splitlines():
        s = line.strip()
        m = re.match(r"^Account ID:\s*(\d{10})\s*$", s)
        if m:
            ids.append(m.group(1))
    return ids


def _postgrest_error_message_and_code(exc: BaseException) -> tuple[str, Optional[str]]:
    """Normalize PostgREST / Supabase client errors to (message, code)."""
    msg = str(exc)
    code: Optional[str] = None
    if getattr(exc, "args", None) and isinstance(exc.args[0], dict):
        d = exc.args[0]
        code = d.get("code")
        if d.get("message"):
            msg = str(d["message"])
    return msg, code


def sync_list_accounts_output_to_clients(
    list_accounts_text: str,
    *,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Upsert each customer ID from list_accounts() output into ``google_ads_clients``.

    Merges ``metadata`` with ``list_accounts_synced_at`` / ``list_accounts_sync`` flags.
    """
    if not is_configured():
        raise SupabaseNotConfiguredError()

    text = (list_accounts_text or "").strip()
    if text.startswith("Error"):
        return {"status": "error", "message": text, "customer_ids": [], "clients": []}

    ids = parse_account_ids_from_list_accounts_output(list_accounts_text)
    if not ids:
        return {
            "status": "no_accounts_parsed",
            "message": "No 'Account ID: NNNNNNNNNN' lines found in input.",
            "raw_preview": text[:500],
            "customer_ids": [],
            "clients": [],
        }

    ts = datetime.now(timezone.utc).isoformat()
    clients: List[Dict[str, Any]] = []
    try:
        for cid in ids:
            existing = get_client_by_customer_id(cid)
            base_meta: Dict[str, Any] = {}
            if existing and existing.get("metadata"):
                m = existing["metadata"]
                if isinstance(m, dict):
                    base_meta = dict(m)
            base_meta["list_accounts_synced_at"] = ts
            base_meta["list_accounts_sync"] = True
            row = upsert_client(customer_id=cid, notes=notes, metadata=base_meta)
            clients.append(row)
    except Exception as e:
        msg, code = _postgrest_error_message_and_code(e)
        raw = str(e)
        low = msg.lower()

        # Missing table / PostgREST schema cache (do not match on table name alone — RLS errors cite it too).
        if (
            code == "PGRST205"
            or "PGRST205" in raw
            or "could not find the table" in low
        ):
            return {
                "status": "supabase_schema_missing",
                "message": (
                    "Supabase does not have the expected tables. In the Supabase dashboard, open "
                    "SQL → New query, paste and run the full contents of "
                    "`supabase/migrations/001_initial.sql`, then run this sync again. "
                    "(PostgREST may take a short moment to refresh its schema cache.)"
                ),
                "technical": msg,
                "customer_ids": ids,
                "clients": [],
            }

        # RLS / wrong API key: anon & authenticated are denied by migration policies; service_role bypasses RLS.
        if code == "42501" or "row-level security" in low:
            return {
                "status": "supabase_permission_denied",
                "message": (
                    "Supabase blocked the insert (row-level security). Use the **service_role** "
                    "secret from Dashboard → Project Settings → API as SUPABASE_SERVICE_ROLE_KEY "
                    "(not the anon or publishable browser key). Restart the script after updating .env."
                ),
                "technical": msg,
                "customer_ids": ids,
                "clients": [],
            }

        raise

    return {"status": "ok", "count": len(ids), "customer_ids": ids, "clients": clients}


def find_clients_by_query(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    q = (query or "").strip().lower()
    if not q:
        return []

    sb = get_client()
    result = sb.table("google_ads_clients").select("*").limit(100).execute()
    matches: List[Dict[str, Any]] = []
    for row in result.data or []:
        name = (row.get("descriptive_name") or "").lower()
        aliases = [str(a).lower() for a in (row.get("aliases") or [])]
        cid = row.get("customer_id", "")
        if q in name or q in cid or any(q in a for a in aliases):
            matches.append(_row_to_dict(row))
        if len(matches) >= limit:
            break
    return matches


def recall_client_context(
    customer_id: Optional[str] = None,
    query: Optional[str] = None,
    memory_limit: int = 20,
) -> Dict[str, Any]:
    client_row: Optional[Dict[str, Any]] = None

    if customer_id:
        client_row = get_client_by_customer_id(customer_id)
    elif query:
        matches = find_clients_by_query(query, limit=5)
        if len(matches) == 1:
            client_row = matches[0]
        elif len(matches) > 1:
            return {
                "status": "ambiguous",
                "message": "Multiple clients matched; pass customer_id explicitly.",
                "matches": matches,
            }
        else:
            return {
                "status": "not_found",
                "message": f"No client found matching {query!r}.",
            }
    else:
        raise ValueError("Provide customer_id or query")

    if not client_row:
        return {
            "status": "not_found",
            "message": f"No client context for customer_id={customer_id!r}.",
        }

    sb = get_client()
    memories = (
        sb.table("memory_entries")
        .select("*")
        .eq("client_id", client_row["id"])
        .order("created_at", desc=True)
        .limit(memory_limit)
        .execute()
    )

    return {
        "status": "ok",
        "client": client_row,
        "recent_memory": memories.data or [],
    }


def insert_memory(
    content: str,
    entry_type: str = "insight",
    customer_id: Optional[str] = None,
    title: Optional[str] = None,
    tags: Optional[Union[List[str], str]] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    if not content or not str(content).strip():
        raise ValueError("content is required")
    et = (entry_type or "insight").lower().strip()
    if et not in VALID_ENTRY_TYPES:
        raise ValueError(f"entry_type must be one of: {sorted(VALID_ENTRY_TYPES)}")

    client_id: Optional[str] = None
    if customer_id:
        row = get_client_by_customer_id(customer_id)
        if not row:
            raise ValueError(
                f"No client for customer_id={customer_id!r}. "
                "Call save_client_context first."
            )
        client_id = row["id"]

    payload = {
        "client_id": client_id,
        "entry_type": et,
        "title": title,
        "content": content.strip(),
        "tags": _ensure_list(tags),
        "source": source,
    }
    sb = get_client()
    result = sb.table("memory_entries").insert(payload).execute()
    if not result.data:
        raise RuntimeError("Failed to insert memory_entries row")
    return _row_to_dict(result.data[0])


def search_memory(
    customer_id: Optional[str] = None,
    entry_type: Optional[str] = None,
    tags: Optional[Union[List[str], str]] = None,
    q: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    sb = get_client()
    query_builder = sb.table("memory_entries").select("*, google_ads_clients(customer_id, descriptive_name)")

    if customer_id:
        row = get_client_by_customer_id(customer_id)
        if not row:
            return []
        query_builder = query_builder.eq("client_id", row["id"])

    if entry_type:
        et = entry_type.lower().strip()
        if et not in VALID_ENTRY_TYPES:
            raise ValueError(f"entry_type must be one of: {sorted(VALID_ENTRY_TYPES)}")
        query_builder = query_builder.eq("entry_type", et)

    if since:
        query_builder = query_builder.gte("created_at", _parse_date(since))

    if until:
        query_builder = query_builder.lte("created_at", f"{_parse_date(until)}T23:59:59")

    result = query_builder.order("created_at", desc=True).limit(limit).execute()
    rows = result.data or []

    tag_list = _ensure_list(tags)
    if tag_list:
        tag_set = {t.lower() for t in tag_list}
        rows = [
            r
            for r in rows
            if tag_set.intersection({str(t).lower() for t in (r.get("tags") or [])})
        ]

    if q:
        needle = q.lower()
        rows = [
            r
            for r in rows
            if needle in (r.get("title") or "").lower()
            or needle in (r.get("content") or "").lower()
        ]

    return [_row_to_dict(r) for r in rows]


def insert_report_snapshot(
    customer_id: str,
    report_type: str,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    metrics: Optional[Union[Dict[str, Any], str]] = None,
    summary: Optional[str] = None,
) -> Dict[str, Any]:
    if not report_type or not str(report_type).strip():
        raise ValueError("report_type is required")

    row = get_client_by_customer_id(customer_id)
    if not row:
        raise ValueError(
            f"No client for customer_id={customer_id!r}. Call save_client_context first."
        )

    payload = {
        "client_id": row["id"],
        "report_type": report_type.strip(),
        "period_start": _parse_date(period_start),
        "period_end": _parse_date(period_end),
        "metrics": _ensure_dict(metrics),
        "summary": summary,
    }
    sb = get_client()
    result = sb.table("report_snapshots").insert(payload).execute()
    if not result.data:
        raise RuntimeError("Failed to insert report_snapshots row")
    return _row_to_dict(result.data[0])


def list_report_snapshots(
    customer_id: str,
    report_type: Optional[str] = None,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    row = get_client_by_customer_id(customer_id)
    if not row:
        return []

    sb = get_client()
    query_builder = (
        sb.table("report_snapshots")
        .select("*")
        .eq("client_id", row["id"])
    )

    if report_type:
        query_builder = query_builder.eq("report_type", report_type.strip())

    if period_start:
        query_builder = query_builder.gte("period_start", _parse_date(period_start))

    if period_end:
        query_builder = query_builder.lte("period_end", _parse_date(period_end))

    result = query_builder.order("created_at", desc=True).limit(limit).execute()
    return [_row_to_dict(r) for r in (result.data or [])]


def persist_campaign_performance_snapshot(
    customer_id: str,
    days: int,
    api_results: List[Dict[str, Any]],
    *,
    summary: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Insert a ``report_snapshots`` row for campaign performance (``get_campaign_performance``).

    Creates a minimal ``google_ads_clients`` row via ``upsert_client`` if missing.

    ``period_start`` / ``period_end`` are UTC **calendar** dates spanning ``days``
    inclusive, ending today UTC—aligned with GAQL ``LAST_N_DAYS`` rolling windows
    when the query is run the same calendar day in UTC.
    """
    if not is_configured():
        return {
            "status": "skipped",
            "message": "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set.",
        }

    cid = normalize_customer_id(customer_id)
    n = max(int(days), 1)
    today_utc = datetime.now(timezone.utc).date()
    period_end = today_utc.isoformat()
    period_start = (today_utc - timedelta(days=n - 1)).isoformat()
    report_type = f"campaign_performance_last_{n}d"

    metrics: Dict[str, Any] = {
        "days": n,
        "source": "get_campaign_performance",
        "row_count": len(api_results),
        "rows": api_results[:50],
    }

    try:
        if not get_client_by_customer_id(cid):
            upsert_client(cid)
        snap = insert_report_snapshot(
            customer_id=cid,
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
            metrics=metrics,
            summary=summary,
        )
        return {
            "status": "ok",
            "report_type": report_type,
            "period_start": period_start,
            "period_end": period_end,
            "snapshot": snap,
        }
    except Exception as e:
        msg, code = _postgrest_error_message_and_code(e)
        raw = str(e)
        low = msg.lower()
        if code == "PGRST205" or "PGRST205" in raw or "could not find the table" in low:
            return {
                "status": "supabase_schema_missing",
                "message": (
                    "Run supabase/migrations/001_initial.sql in the Supabase SQL Editor, then retry."
                ),
                "technical": msg,
            }
        if code == "42501" or "row-level security" in low:
            return {
                "status": "supabase_permission_denied",
                "message": (
                    "Use the service_role / secret API key as SUPABASE_SERVICE_ROLE_KEY "
                    "(not the publishable key)."
                ),
                "technical": msg,
            }
        return {"status": "error", "message": msg, "technical": raw}


def insert_analysis_text_snapshot(
    customer_id: str,
    analysis_type: str,
    body: str,
    *,
    title: Optional[str] = None,
    metadata: Optional[Union[Dict[str, Any], str]] = None,
    auto_upsert_client: bool = True,
) -> Dict[str, Any]:
    """
    Insert a narrative analysis (Markdown or plain text) for a client.

    ``analysis_type`` is a free-form label, e.g. ``campaign_performance_analysis``,
    ``ad_performance_analysis``, ``impression_share_analysis``.

    ``created_at`` is set by the database (server timestamp).

    If ``auto_upsert_client`` is True and no ``google_ads_clients`` row exists, creates a minimal row.
    """
    if not analysis_type or not str(analysis_type).strip():
        raise ValueError("analysis_type is required")
    text = (body or "").strip()
    if not text:
        raise ValueError("body is required")

    cid = normalize_customer_id(customer_id)
    if auto_upsert_client and not get_client_by_customer_id(cid):
        upsert_client(cid)

    row = get_client_by_customer_id(cid)
    if not row:
        raise ValueError(
            f"No client for customer_id={customer_id!r}. Call save_client_context or enable auto_upsert_client."
        )

    payload: Dict[str, Any] = {
        "client_id": row["id"],
        "analysis_type": analysis_type.strip(),
        "title": title.strip() if title and str(title).strip() else None,
        "body": text,
        "metadata": _ensure_dict(metadata),
    }
    sb = get_client()
    result = sb.table("analysis_text_snapshots").insert(payload).execute()
    if not result.data:
        raise RuntimeError("Failed to insert analysis_text_snapshots row")
    return _row_to_dict(result.data[0])


def list_analysis_text_snapshots(
    customer_id: str,
    analysis_type: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 30,
) -> List[Dict[str, Any]]:
    """List narrative analysis snapshots for a client, newest ``created_at`` first."""
    row = get_client_by_customer_id(customer_id)
    if not row:
        return []

    sb = get_client()
    q = sb.table("analysis_text_snapshots").select("*").eq("client_id", row["id"])

    if analysis_type:
        q = q.eq("analysis_type", analysis_type.strip())

    if since:
        q = q.gte("created_at", f"{_parse_date(since)}T00:00:00")

    if until:
        q = q.lte("created_at", f"{_parse_date(until)}T23:59:59")

    result = q.order("created_at", desc=True).limit(limit).execute()
    return [_row_to_dict(r) for r in (result.data or [])]
