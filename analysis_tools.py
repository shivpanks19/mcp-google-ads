"""
MCP tools: combine Supabase saved analyses / metric snapshots with live Google Ads data,
and emit a concrete Markdown performance brief.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Annotated, Any, Dict, List, Optional, Tuple

from pydantic import Field

import supabase_store as store
from google_ads_server import (
    fetch_ad_copy_asset_performance_rows,
    fetch_ad_performance_table_and_rows,
    fetch_campaign_performance_table_and_rows,
    get_account_currency,
    mcp,
)

# Google RSA limits (Search)
_HEADLINE_MAX_LEN = 30
_DESCRIPTION_MAX_LEN = 90


def _json_response(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _f(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _i(val: Any, default: int = 0) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def _campaign(row: Dict[str, Any]) -> Dict[str, Any]:
    c = row.get("campaign")
    return c if isinstance(c, dict) else {}


def _metrics(row: Dict[str, Any]) -> Dict[str, Any]:
    m = row.get("metrics")
    return m if isinstance(m, dict) else {}


def _ad(row: Dict[str, Any]) -> Dict[str, Any]:
    a = row.get("adGroupAd", row.get("ad_group_ad"))
    if isinstance(a, dict):
        inner = a.get("ad")
        return inner if isinstance(inner, dict) else {}
    return {}


def _ad_status(row: Dict[str, Any]) -> str:
    a = row.get("adGroupAd", row.get("ad_group_ad"))
    if isinstance(a, dict):
        return str(a.get("status", "") or "")
    return ""


def _ad_group(row: Dict[str, Any]) -> Dict[str, Any]:
    a = row.get("adGroup", row.get("ad_group"))
    return a if isinstance(a, dict) else {}


def _parse_currency_line(text: str) -> str:
    m = re.search(r"currency:\s*(\w+)", text or "", re.I)
    return m.group(1) if m else "unknown"


def _summarize_campaign_rows(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Return aggregate dict and top-N list by cost_micros (enabled spenders first)."""
    total_cost = total_clicks = total_impr = 0
    total_conv = 0.0
    by_status: Dict[str, int] = {}
    spenders: List[Dict[str, Any]] = []

    for row in rows:
        camp = _campaign(row)
        met = _metrics(row)
        st = str(camp.get("status", "") or "UNKNOWN").upper()
        by_status[st] = by_status.get(st, 0) + 1
        cost = _i(met.get("costMicros", met.get("cost_micros")))
        clicks = _i(met.get("clicks"))
        impr = _i(met.get("impressions"))
        conv = _f(met.get("conversions"))
        total_cost += cost
        total_clicks += clicks
        total_impr += impr
        total_conv += conv
        name = str(camp.get("name", "") or camp.get("resourceName", "campaign"))
        spenders.append(
            {
                "name": name,
                "status": st,
                "cost_micros": cost,
                "clicks": clicks,
                "impressions": impr,
                "conversions": conv,
                "cpc_micros": (cost // clicks) if clicks else 0,
            }
        )

    spenders.sort(key=lambda x: x["cost_micros"], reverse=True)
    enabled_spend = sum(
        s["cost_micros"] for s in spenders if s["status"] == "ENABLED" and s["cost_micros"] > 0
    )
    return (
        {
            "total_cost_micros": total_cost,
            "total_clicks": total_clicks,
            "total_impressions": total_impr,
            "total_conversions": total_conv,
            "by_status": by_status,
            "enabled_cost_micros": enabled_spend,
            "avg_cpc_micros": (total_cost // total_clicks) if total_clicks else 0,
        },
        spenders,
    )


def _summarize_ad_rows(rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    total_cost = total_clicks = total_impr = 0
    total_conv = 0.0
    ads: List[Dict[str, Any]] = []
    for row in rows:
        ad = _ad(row)
        met = _metrics(row)
        camp = _campaign(row)
        ag = _ad_group(row)
        cost = _i(met.get("costMicros", met.get("cost_micros")))
        clicks = _i(met.get("clicks"))
        impr = _i(met.get("impressions"))
        conv = _f(met.get("conversions"))
        total_cost += cost
        total_clicks += clicks
        total_impr += impr
        total_conv += conv
        ads.append(
            {
                "ad_name": str(ad.get("name", "") or ad.get("id", "ad")),
                "status": _ad_status(row),
                "campaign": str(camp.get("name", "")),
                "ad_group": str(ag.get("name", "")),
                "cost_micros": cost,
                "clicks": clicks,
                "impressions": impr,
                "conversions": conv,
            }
        )
    ads.sort(key=lambda x: x["impressions"], reverse=True)
    return (
        {
            "total_cost_micros": total_cost,
            "total_clicks": total_clicks,
            "total_impressions": total_impr,
            "total_conversions": total_conv,
        },
        ads,
    )


def _format_supabase_context(
    customer_id: str,
    *,
    analysis_limit: int,
    snapshot_limit: int,
) -> str:
    if not store.is_configured():
        return "## Saved context (Supabase)\n\n_Supabase not configured (`SUPABASE_*` unset)._\n\n"

    lines: List[str] = ["## Saved context (Supabase)", ""]

    analyses = store.list_analysis_text_snapshots(customer_id, limit=max(1, min(analysis_limit, 20)))
    if analyses:
        lines.append("### Recent narrative analyses (`analysis_text_snapshots`)")
        for a in analyses:
            ts = a.get("created_at", "")
            at = a.get("analysis_type", "")
            title = a.get("title") or "(no title)"
            body = (a.get("body") or "")[:600]
            if len(a.get("body") or "") > 600:
                body += " …"
            lines.append(f"- **{ts}** `{at}` — _{title}_")
            lines.append(f"  > {body.replace(chr(10), ' ')}")
        lines.append("")
    else:
        lines.append("_No rows in `analysis_text_snapshots` yet._\n")

    snaps = store.list_report_snapshots(customer_id, limit=max(1, min(snapshot_limit, 20)))
    if snaps:
        lines.append("### Recent metric snapshots (`report_snapshots`)")
        for s in snaps:
            lines.append(
                f"- **{s.get('created_at', '')}** `{s.get('report_type', '')}` "
                f"period {s.get('period_start')} → {s.get('period_end')}"
            )
            summ = s.get("summary")
            if summ:
                lines.append(f"  - summary: {str(summ)[:200]}{'…' if len(str(summ)) > 200 else ''}")
            m = s.get("metrics") if isinstance(s.get("metrics"), dict) else {}
            if m.get("row_count") is not None:
                lines.append(f"  - metrics row_count: {m.get('row_count')}")
        lines.append("")
    else:
        lines.append("_No rows in `report_snapshots` yet._\n")

    return "\n".join(lines)


def _build_markdown_report(
    customer_id: str,
    currency: str,
    campaign_days: int,
    ad_days: int,
    camp_rows: List[Dict[str, Any]],
    ad_rows: List[Dict[str, Any]],
    supabase_md: str,
) -> str:
    agg, top_c = _summarize_campaign_rows(camp_rows)
    ad_agg, top_a = _summarize_ad_rows(ad_rows)

    def money(micros: int) -> str:
        return f"{micros / 1_000_000:,.2f} {currency}"

    lines: List[str] = [
        f"# Running campaign & ad analysis — customer `{customer_id}`",
        "",
        f"- **Currency:** {currency}",
        f"- **Campaign window:** last **{campaign_days}** days (top 50 by cost in API)",
        f"- **Ad window:** last **{ad_days}** days (top 50 by impressions in API)",
        "",
        supabase_md,
        "## Live campaigns (aggregates)",
        "",
        f"- **Total cost:** {money(agg['total_cost_micros'])}",
        f"- **Total clicks:** {agg['total_clicks']:,}",
        f"- **Total impressions:** {agg['total_impressions']:,}",
        f"- **Total conversions:** {agg['total_conversions']:.2f}",
        f"- **Blended CPC:** {money(agg['avg_cpc_micros'])}" if agg["total_clicks"] else "- **Blended CPC:** n/a (no clicks)",
        f"- **Cost in ENABLED campaigns (subset above):** {money(agg['enabled_cost_micros'])}",
        f"- **Rows by campaign.status:** `{json.dumps(agg['by_status'])}`",
        "",
        "### Top campaigns by cost (up to 10)",
        "",
        "| Campaign | Status | Cost | Clicks | Conv. |",
        "|----------|--------|------|--------|-------|",
    ]

    for s in top_c[:10]:
        lines.append(
            f"| {s['name'][:48]} | {s['status']} | {money(s['cost_micros'])} | {s['clicks']} | {s['conversions']:.2f} |"
        )

    lines.extend(
        [
            "",
            "## Live ads (aggregates, same windows as above)",
            "",
            f"- **Total cost:** {money(ad_agg['total_cost_micros'])}",
            f"- **Total clicks:** {ad_agg['total_clicks']:,}",
            f"- **Total impressions:** {ad_agg['total_impressions']:,}",
            f"- **Total conversions:** {ad_agg['total_conversions']:.2f}",
            "",
            "### Top ads by impressions (up to 8)",
            "",
            "| Ad | Status | Campaign | Impr. | Clicks | Conv. |",
            "|----|--------|----------|-------|--------|-------|",
        ]
    )
    for a in top_a[:8]:
        lines.append(
            f"| {str(a['ad_name'])[:36]} | {a['status']} | {str(a['campaign'])[:22]} | "
            f"{a['impressions']} | {a['clicks']} | {a['conversions']:.2f} |"
        )

    # Concrete recommendations (rule-based)
    recs: List[str] = []
    if agg["total_clicks"] and agg["total_conversions"] / agg["total_clicks"] < 0.03:
        recs.append("Blended CVR is under ~3% across this top slice — review **search terms**, **LP alignment**, and **RSA assets** on the highest-cost ENABLED campaigns.")
    top1 = top_c[0] if top_c else None
    if top1 and top1["status"] == "ENABLED" and top1["clicks"] > 50 and top1["conversions"] < 1:
        recs.append(
            f"Top spender **{top1['name'][:40]}** has spend with very few conversions — prioritize a **query + ad diagnostic** on that campaign."
        )
    waste_ads = [a for a in top_a if a["clicks"] >= 30 and a["conversions"] == 0 and a["cost_micros"] > 0][:3]
    for a in waste_ads:
        recs.append(
            f"Ad **{a['ad_name'][:40]}** has **{a['clicks']}** clicks and **0** conversions — check creative/keyword intent and pause or rewrite if traffic is low quality."
        )
    if not recs:
        recs.append(
            "No hard red flags in the quick heuristics — still export **search terms** and **change history** for the top two campaigns this week."
        )

    lines.extend(["", "## Concrete next steps", ""] + [f"{i + 1}. {r}" for i, r in enumerate(recs)])
    lines.append("")
    lines.append(
        "_Method: aggregates over API top-50 rows; not full-account totals. "
        "Use `run_gaql` for exhaustive sums or segments._"
    )
    return "\n".join(lines)


@mcp.tool()
async def get_account_analysis_context(
    customer_id: Annotated[str, Field(description="Google Ads customer ID (10 digits)")],
    analysis_limit: Annotated[
        int, Field(ge=1, le=20, description="Max `analysis_text_snapshots` rows")
    ] = 5,
    snapshot_limit: Annotated[
        int, Field(ge=1, le=20, description="Max `report_snapshots` rows")
    ] = 5,
) -> str:
    """
    Load recent **narrative analyses** and **metric snapshots** from Supabase for this account
    (no live Google Ads API call). Use before/after `analyze_running_campaigns_and_ads`.
    """
    try:
        if not store.is_configured():
            return _json_response(
                {
                    "status": "skipped",
                    "message": "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set.",
                }
            )
        analyses = store.list_analysis_text_snapshots(customer_id, limit=analysis_limit)
        snaps = store.list_report_snapshots(customer_id, limit=snapshot_limit)
        return _json_response(
            {
                "customer_id": store.normalize_customer_id(customer_id),
                "analysis_text_snapshots": analyses,
                "report_snapshots": snaps,
            }
        )
    except Exception as e:
        return _json_response({"error": f"{type(e).__name__}: {e}"})


@mcp.tool()
async def analyze_running_campaigns_and_ads(
    customer_id: Annotated[
        str, Field(description="Google Ads customer ID (10 digits, no dashes)")
    ],
    campaign_days: Annotated[
        int, Field(ge=1, le=365, description="LAST_N_DAYS window for campaign query")
    ] = 30,
    ad_days: Annotated[
        int,
        Field(
            ge=1,
            le=365,
            description=(
                "LAST_N_DAYS window for ad_group_ad query "
                "(often shorter for fresher creative signals)"
            ),
        ),
    ] = 14,
    include_supabase_context: Annotated[
        bool,
        Field(
            description=(
                "Prepend recent analysis_text_snapshots + report_snapshots "
                "when Supabase is configured"
            )
        ),
    ] = True,
    supabase_analysis_limit: Annotated[int, Field(ge=0, le=15)] = 3,
    supabase_snapshot_limit: Annotated[int, Field(ge=0, le=15)] = 3,
) -> str:
    """
    **End-to-end brief:** pulls optional **Supabase** history (`analysis_text_snapshots`, `report_snapshots`),
    then **live** top campaigns (by cost) and top ads (by impressions) from the Google Ads API,
    and returns a **Markdown** report with aggregates and rule-based recommendations.

    Does **not** auto-write to Supabase; use `save_analysis_text_snapshot` to persist this output if desired.
    """
    try:
        currency_task = get_account_currency(customer_id)
        camp_task = fetch_campaign_performance_table_and_rows(customer_id, campaign_days)
        ad_task = fetch_ad_performance_table_and_rows(customer_id, ad_days)

        currency_raw, camp_data, ad_data = await asyncio.gather(currency_task, camp_task, ad_task)

        currency = _parse_currency_line(currency_raw)
        if currency_raw.startswith("Error"):
            return f"## Analysis aborted\n\n{currency_raw}"

        if not camp_data.get("ok"):
            return f"## Campaign fetch failed\n\n{camp_data.get('error')}"
        if not ad_data.get("ok"):
            return f"## Ad fetch failed\n\n{ad_data.get('error')}"

        if include_supabase_context:
            sup_md = _format_supabase_context(
                customer_id,
                analysis_limit=max(1, min(supabase_analysis_limit, 20)),
                snapshot_limit=max(1, min(supabase_snapshot_limit, 20)),
            )
        else:
            sup_md = (
                "## Saved context (Supabase)\n\n"
                "_Skipped (`include_supabase_context=False`)._\n\n"
            )

        cid = camp_data["formatted_customer_id"]
        report = _build_markdown_report(
            cid,
            currency,
            campaign_days,
            ad_days,
            camp_data.get("rows") or [],
            ad_data.get("rows") or [],
            sup_md,
        )
        return report
    except store.SupabaseNotConfiguredError as e:
        return str(e)
    except Exception as e:
        return f"## Error\n\n`{type(e).__name__}`: {e}"


def _asset_view(row: Dict[str, Any]) -> Dict[str, Any]:
    v = row.get("adGroupAdAssetView", row.get("ad_group_ad_asset_view"))
    return v if isinstance(v, dict) else {}


def _asset(row: Dict[str, Any]) -> Dict[str, Any]:
    a = row.get("asset")
    return a if isinstance(a, dict) else {}


def _asset_text(row: Dict[str, Any]) -> str:
    a = _asset(row)
    ta = a.get("textAsset", a.get("text_asset"))
    if isinstance(ta, dict):
        return str(ta.get("text", "") or "").strip()
    return ""


def _asset_field_type(row: Dict[str, Any]) -> str:
    av = _asset_view(row)
    ft = av.get("fieldType", av.get("field_type"))
    if ft:
        return str(ft).upper()
    text = _asset_text(row)
    if not text:
        return "UNKNOWN"
    if len(text) <= _HEADLINE_MAX_LEN:
        return "HEADLINE"
    return "DESCRIPTION"


def _asset_performance_label(row: Dict[str, Any]) -> str:
    av = _asset_view(row)
    label = av.get("performanceLabel", av.get("performance_label"))
    if not label:
        label = row.get("assetPerformanceLabel", row.get("asset_performance_label"))
    return str(label or "UNKNOWN").upper()


def _normalize_copy_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _parse_asset_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    text = _asset_text(row)
    if not text:
        return None
    met = _metrics(row)
    camp = _campaign(row)
    ag = _ad_group(row)
    ad = _ad(row)
    impr = _i(met.get("impressions"))
    clicks = _i(met.get("clicks"))
    cost = _i(met.get("costMicros", met.get("cost_micros")))
    conv = _f(met.get("conversions"))
    ctr_raw = met.get("ctr")
    ctr_pct = _f(ctr_raw) * 100 if ctr_raw not in (None, "") else ((clicks / impr * 100) if impr else 0.0)
    field_type = _asset_field_type(row)
    return {
        "text": text,
        "field_type": field_type,
        "performance_label": _asset_performance_label(row),
        "campaign": str(camp.get("name", "") or ""),
        "ad_group": str(ag.get("name", "") or ""),
        "ad_id": str(ad.get("id", "") or ""),
        "asset_id": str(_asset(row).get("id", "") or ""),
        "impressions": impr,
        "clicks": clicks,
        "cost_micros": cost,
        "conversions": conv,
        "ctr_pct": ctr_pct,
        "char_len": len(text),
    }


def summarize_ad_copy_assets(
    rows: List[Dict[str, Any]],
    *,
    campaign_filter: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Parse GAQL asset rows into normalized dicts and aggregate stats.

    Returns (aggregate_summary, parsed_asset_list).
    """
    parsed: List[Dict[str, Any]] = []
    cf = (campaign_filter or "").strip().lower()
    for row in rows:
        item = _parse_asset_row(row)
        if not item:
            continue
        if cf and cf not in item["campaign"].lower():
            continue
        parsed.append(item)

    by_label: Dict[str, int] = {}
    by_field: Dict[str, int] = {"HEADLINE": 0, "DESCRIPTION": 0, "UNKNOWN": 0}
    total_impr = total_clicks = total_cost = 0
    total_conv = 0.0
    for p in parsed:
        by_label[p["performance_label"]] = by_label.get(p["performance_label"], 0) + 1
        by_field[p["field_type"]] = by_field.get(p["field_type"], 0) + 1
        total_impr += p["impressions"]
        total_clicks += p["clicks"]
        total_cost += p["cost_micros"]
        total_conv += p["conversions"]

    parsed.sort(key=lambda x: x["impressions"], reverse=True)

    text_groups: Dict[str, List[Dict[str, Any]]] = {}
    for p in parsed:
        key = _normalize_copy_key(p["text"])
        text_groups.setdefault(key, []).append(p)

    duplicates = [
        {"text": items[0]["text"], "count": len(items), "total_impressions": sum(i["impressions"] for i in items)}
        for items in text_groups.values()
        if len(items) > 1
    ]
    duplicates.sort(key=lambda x: x["total_impressions"], reverse=True)

    over_limit = [
        p
        for p in parsed
        if (p["field_type"] == "HEADLINE" and p["char_len"] > _HEADLINE_MAX_LEN)
        or (p["field_type"] == "DESCRIPTION" and p["char_len"] > _DESCRIPTION_MAX_LEN)
    ]

    low_with_volume = [
        p
        for p in parsed
        if p["performance_label"] == "LOW" and p["impressions"] >= 500
    ]

    best_headlines = [p for p in parsed if p["field_type"] == "HEADLINE" and p["performance_label"] == "BEST"][:8]
    best_descriptions = [
        p for p in parsed if p["field_type"] == "DESCRIPTION" and p["performance_label"] == "BEST"
    ][:6]

    waste = [p for p in parsed if p["clicks"] >= 25 and p["conversions"] == 0 and p["cost_micros"] > 0][:8]

    return (
        {
            "asset_count": len(parsed),
            "by_performance_label": by_label,
            "by_field_type": by_field,
            "total_impressions": total_impr,
            "total_clicks": total_clicks,
            "total_cost_micros": total_cost,
            "total_conversions": total_conv,
            "blended_ctr_pct": (total_clicks / total_impr * 100) if total_impr else 0.0,
            "duplicate_lines": duplicates[:10],
            "over_char_limit": over_limit[:10],
            "low_label_high_impressions": low_with_volume[:10],
            "best_headlines": best_headlines,
            "best_descriptions": best_descriptions,
            "high_click_no_conversion": waste,
        },
        parsed,
    )


def _build_ad_copy_recommendations(agg: Dict[str, Any]) -> List[str]:
    recs: List[str] = []
    low_n = agg["by_performance_label"].get("LOW", 0)
    best_n = agg["by_performance_label"].get("BEST", 0)
    if agg["asset_count"] == 0:
        recs.append(
            "No text asset metrics returned — confirm the account runs **Search RSAs** with impressions "
            "in the window, or run `get_ad_creatives` for a static copy inventory."
        )
        return recs

    if low_n >= 3:
        recs.append(
            f"**{low_n}** assets are labeled **LOW** — pause or replace the highest-impression LOW lines "
            "before adding net-new variants (reduces weak combinations in the auction)."
        )
    if agg["duplicate_lines"]:
        d = agg["duplicate_lines"][0]
        recs.append(
            f"Duplicate copy detected (**\"{d['text'][:40]}…\"** appears **{d['count']}** times) — "
            "dedupe across ad groups or differentiate by intent/offer."
        )
    if agg["over_char_limit"]:
        recs.append(
            f"**{len(agg['over_char_limit'])}** lines exceed Google character limits — "
            f"headlines ≤{_HEADLINE_MAX_LEN}, descriptions ≤{_DESCRIPTION_MAX_LEN}."
        )
    if agg["low_label_high_impressions"]:
        p = agg["low_label_high_impressions"][0]
        recs.append(
            f"LOW asset with **{p['impressions']:,}** impr.: \"{p['text'][:50]}\" — "
            "rewrite or remove; consider pinning a BEST headline instead."
        )
    if best_n and agg["best_headlines"]:
        h = agg["best_headlines"][0]
        recs.append(
            f"Pin or replicate themes from BEST headline: \"{h['text'][:45]}\" "
            f"({h['impressions']:,} impr., {h['ctr_pct']:.2f}% CTR)."
        )
    for p in agg["high_click_no_conversion"][:2]:
        recs.append(
            f"\"{p['text'][:42]}\" has **{p['clicks']}** clicks, **0** conv. — "
            "check message match to landing page and search intent."
        )
    if not recs:
        recs.append(
            "No critical copy flags in heuristics — still add **2–3 fresh headlines** per top ad group "
            "and test against current BEST performers."
        )
    return recs


def _build_ad_copy_markdown_report(
    customer_id: str,
    currency: str,
    days: int,
    data_source: str,
    agg: Dict[str, Any],
    top_assets: List[Dict[str, Any]],
    supabase_md: str,
    campaign_filter: Optional[str],
) -> str:
    def money(micros: int) -> str:
        return f"{micros / 1_000_000:,.2f} {currency}"

    filter_note = f" (campaign name contains **{campaign_filter}**)" if campaign_filter else ""

    lines: List[str] = [
        f"# Ad copy analysis — customer `{customer_id}`",
        "",
        f"- **Currency:** {currency}",
        f"- **Window:** last **{days}** days",
        f"- **Data source:** `{data_source}` (top **200** text assets by impressions)",
        f"- **Scope:**{filter_note}",
        "",
        supabase_md,
        "## Portfolio summary",
        "",
        f"- **Assets analyzed:** {agg['asset_count']}",
        f"- **Impressions:** {agg['total_impressions']:,}",
        f"- **Clicks:** {agg['total_clicks']:,}",
        f"- **Blended CTR:** {agg['blended_ctr_pct']:.2f}%",
        f"- **Cost:** {money(agg['total_cost_micros'])}",
        f"- **Conversions:** {agg['total_conversions']:.2f}",
        f"- **By performance label:** `{json.dumps(agg['by_performance_label'])}`",
        f"- **Headlines / descriptions:** "
        f"{agg['by_field_type'].get('HEADLINE', 0)} / {agg['by_field_type'].get('DESCRIPTION', 0)}",
        "",
    ]

    if top_assets:
        lines.extend(
            [
                "### Top assets by impressions (up to 12)",
                "",
                "| Type | Label | Copy | Impr. | CTR | Conv. | Campaign |",
                "|------|-------|------|-------|-----|-------|----------|",
            ]
        )
        for p in top_assets[:12]:
            copy = p["text"].replace("|", "/")[:44]
            lines.append(
                f"| {p['field_type'][:4]} | {p['performance_label'][:5]} | {copy} | "
                f"{p['impressions']:,} | {p['ctr_pct']:.2f}% | {p['conversions']:.1f} | "
                f"{p['campaign'][:20]} |"
            )
        lines.append("")

    if agg["best_headlines"]:
        lines.extend(["### BEST headlines (pin candidates)", ""])
        for p in agg["best_headlines"][:6]:
            lines.append(f"- **{p['text']}** — {p['impressions']:,} impr., {p['ctr_pct']:.2f}% CTR")
        lines.append("")

    if agg["low_label_high_impressions"]:
        lines.extend(["### LOW labels with meaningful volume", ""])
        for p in agg["low_label_high_impressions"][:6]:
            lines.append(
                f"- **{p['text'][:60]}** — {p['impressions']:,} impr., label LOW, "
                f"{p['clicks']} clicks"
            )
        lines.append("")

    if agg["duplicate_lines"]:
        lines.extend(["### Duplicate copy (same text, multiple rows)", ""])
        for d in agg["duplicate_lines"][:5]:
            lines.append(f"- \"{d['text'][:55]}\" — **{d['count']}** placements, {d['total_impressions']:,} impr.")
        lines.append("")

    recs = _build_ad_copy_recommendations(agg)
    lines.extend(["## Recommended actions", ""] + [f"{i + 1}. {r}" for i, r in enumerate(recs)])
    lines.append("")
    lines.append(
        "_Method: asset-level metrics from Google performance labels; not full ad-strength or "
        "combination-level reporting. Use `get_ad_creatives` for full RSA sets; "
        "`save_analysis_text_snapshot` to persist this brief._"
    )
    return "\n".join(lines)


@mcp.tool()
async def analyze_ad_copy(
    customer_id: Annotated[
        str, Field(description="Google Ads customer ID (10 digits, no dashes)")
    ],
    days: Annotated[
        int,
        Field(
            ge=1,
            le=365,
            description="LAST_N_DAYS window for asset performance metrics",
        ),
    ] = 30,
    campaign_name_contains: Annotated[
        Optional[str],
        Field(
            description=(
                "Optional case-insensitive substring filter on campaign.name "
                "(omit for whole account)"
            )
        ),
    ] = None,
    include_supabase_context: Annotated[
        bool,
        Field(description="Prepend recent analysis_text_snapshots when Supabase is configured"),
    ] = True,
    supabase_analysis_limit: Annotated[int, Field(ge=0, le=15)] = 3,
) -> str:
    """
    **Ad copy brief:** pulls RSA **headline/description** assets with Google **performance labels**
    (BEST / GOOD / LOW / LEARNING) and metrics, then returns a **Markdown** report with
    top lines, duplicates, character-limit flags, and actionable recommendations.

    Uses `ad_group_ad_asset_view` when available; falls back to `asset_performance_label_view`.
    Does not auto-persist — use `save_analysis_text_snapshot` with `analysis_type=ad_copy_analysis`.
    """
    try:
        currency_task = get_account_currency(customer_id)
        assets_task = fetch_ad_copy_asset_performance_rows(customer_id, days)
        currency_raw, asset_data = await asyncio.gather(currency_task, assets_task)

        currency = _parse_currency_line(currency_raw)
        if currency_raw.startswith("Error"):
            return f"## Ad copy analysis aborted\n\n{currency_raw}"

        if not asset_data.get("ok"):
            return f"## Asset fetch failed\n\n{asset_data.get('error')}"

        rows = asset_data.get("rows") or []
        agg, parsed = summarize_ad_copy_assets(
            rows, campaign_filter=campaign_name_contains
        )

        if include_supabase_context:
            sup_md = _format_supabase_context(
                customer_id,
                analysis_limit=max(1, min(supabase_analysis_limit, 20)),
                snapshot_limit=0,
            )
        else:
            sup_md = (
                "## Saved context (Supabase)\n\n"
                "_Skipped (`include_supabase_context=False`)._\n\n"
            )

        cid = asset_data["formatted_customer_id"]
        report = _build_ad_copy_markdown_report(
            cid,
            currency,
            days,
            str(asset_data.get("source", "unknown")),
            agg,
            parsed,
            sup_md,
            campaign_name_contains,
        )
        return report
    except store.SupabaseNotConfiguredError as e:
        return str(e)
    except Exception as e:
        return f"## Error\n\n`{type(e).__name__}`: {e}"
