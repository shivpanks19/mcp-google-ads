"""
PM-head orchestration: classify performance and apply campaign edits directly.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, Dict, List, Optional, Tuple

from pydantic import Field

import mutate_helpers as mh
from google_ads_server import (
    fetch_campaign_performance_table_and_rows,
    format_customer_id,
    get_credentials,
    mcp,
)

logger = logging.getLogger("google_ads_server")


def _campaign_metrics_from_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aggregate GAQL campaign rows (with segments.date) by campaign id."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        c = row.get("campaign") or {}
        m = row.get("metrics") or {}
        cid = str(c.get("id", ""))
        if not cid:
            continue
        if cid not in by_id:
            by_id[cid] = {
                "campaign_id": cid,
                "name": c.get("name") or "",
                "status": c.get("status") or "",
                "clicks": 0,
                "impressions": 0,
                "cost_micros": 0,
                "conversions": 0.0,
            }
        agg = by_id[cid]
        agg["clicks"] += int(m.get("clicks") or 0)
        agg["impressions"] += int(m.get("impressions") or 0)
        agg["cost_micros"] += int(m.get("costMicros") or m.get("cost_micros") or 0)
        agg["conversions"] += float(m.get("conversions") or 0)
    out = []
    for agg in by_id.values():
        spend = mh.micros_to_currency(agg["cost_micros"])
        cpa = spend / agg["conversions"] if agg["conversions"] > 0 else None
        ctr = agg["clicks"] / agg["impressions"] if agg["impressions"] else 0
        agg["spend"] = spend
        agg["cpa"] = cpa
        agg["ctr"] = ctr
        out.append(agg)
    return out


def classify_campaigns(
    campaigns: List[Dict[str, Any]],
    blended_cpa: Optional[float],
    min_spend: float,
    min_clicks_pause: int,
    max_cpa_multiplier: float,
) -> Dict[str, List[Dict[str, Any]]]:
    """Return buckets: pause, reduce_budget, increase_budget, maintain."""
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "pause": [],
        "reduce_budget": [],
        "increase_budget": [],
        "maintain": [],
    }
    threshold_cpa = (blended_cpa or 0) * max_cpa_multiplier if blended_cpa else None

    for c in campaigns:
        if c.get("status") != "ENABLED":
            continue
        spend = c.get("spend") or 0
        if spend < min_spend:
            buckets["maintain"].append({**c, "reason": "below min spend threshold"})
            continue

        if c["conversions"] == 0 and c["clicks"] >= min_clicks_pause:
            buckets["pause"].append({**c, "reason": f"0 conv with {c['clicks']} clicks"})
            continue

        cpa = c.get("cpa")
        if cpa is not None and threshold_cpa and cpa > threshold_cpa and c["conversions"] >= 1:
            buckets["reduce_budget"].append(
                {**c, "reason": f"CPA {cpa:.0f} > {threshold_cpa:.0f} ({max_cpa_multiplier}x blended)"}
            )
            continue

        if (
            cpa is not None
            and blended_cpa
            and cpa < blended_cpa * 0.85
            and c["conversions"] >= 2
        ):
            buckets["increase_budget"].append(
                {**c, "reason": f"Strong CPA {cpa:.0f} vs blended {blended_cpa:.0f}"}
            )
            continue

        buckets["maintain"].append({**c, "reason": "within thresholds"})

    return buckets


async def _audit_memory(customer_id: str, summary: str) -> Optional[str]:
    try:
        import memory_tools
        import supabase_store as store

        if not store.is_configured():
            return None
        result = await memory_tools.save_memory(
            content=summary,
            entry_type="decision",
            customer_id=customer_id,
            title="Weekly performance actions applied",
            tags=["campaign_edit", "weekly_optimization"],
            source="optimization_actions",
        )
        return result
    except Exception as e:
        logger.warning("Could not save audit memory: %s", e)
        return None


@mcp.tool()
async def apply_weekly_performance_actions(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    days: Annotated[int, Field(description="Performance lookback days")] = 7,
    pause_zero_conversion_spenders: Annotated[
        bool, Field(description="Pause ENABLED campaigns with clicks but no conversions")
    ] = True,
    min_clicks_to_pause: Annotated[int, Field(description="Min clicks before pause rule applies")] = 30,
    min_spend_inr: Annotated[float, Field(description="Min spend in account currency for rules")] = 500.0,
    max_cpa_multiplier: Annotated[
        float, Field(description="Reduce budget when CPA exceeds this multiple of blended CPA")
    ] = 2.0,
    budget_reduce_pct: Annotated[float, Field(description="Percent to reduce budget for high-CPA campaigns")] = 15.0,
    budget_increase_pct: Annotated[
        float, Field(description="Percent to increase budget for strong CPA campaigns (0 = skip increases)")
    ] = 0.0,
    shift_budget_pct: Annotated[
        float, Field(description="Alias: if >0, enables budget_increase_pct for winners")
    ] = 0.0,
    add_search_term_negatives: Annotated[
        bool, Field(description="Add campaign negatives from waste search terms for paused/high-CPA campaigns")
    ] = False,
    search_term_min_cost: Annotated[float, Field(description="Min term spend for negative keyword addition")] = 100.0,
    save_audit_to_supabase: Annotated[bool, Field(description="Write decision to save_memory when Supabase configured")] = True,
) -> str:
    """
    Classify campaigns by weekly performance and apply pause/budget changes directly.

    PM-head rules: pause waste before scale; reduce budget on high CPA; optional negatives from search terms.
    """
    try:
        increase_pct = budget_increase_pct if budget_increase_pct > 0 else shift_budget_pct

        perf = await fetch_campaign_performance_table_and_rows(customer_id, days)
        if not perf.get("ok"):
            return f"Error fetching performance: {perf.get('error')}"

        campaigns = _campaign_metrics_from_rows(perf.get("rows") or [])
        enabled = [c for c in campaigns if c.get("status") == "ENABLED" and c.get("cost_micros", 0) > 0]
        total_conv = sum(c["conversions"] for c in enabled)
        total_spend = sum(c["spend"] for c in enabled)
        blended_cpa = total_spend / total_conv if total_conv > 0 else None

        buckets = classify_campaigns(
            campaigns,
            blended_cpa,
            min_spend=min_spend_inr,
            min_clicks_pause=min_clicks_to_pause,
            max_cpa_multiplier=max_cpa_multiplier,
        )

        creds = get_credentials()
        applied: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []

        if pause_zero_conversion_spenders:
            pause_ops = []
            for c in buckets["pause"]:
                pause_ops.append(
                    {
                        "updateMask": "status",
                        "update": {
                            "resourceName": mh.campaign_resource_name(customer_id, c["campaign_id"]),
                            "status": "PAUSED",
                        },
                    }
                )
            if pause_ops:
                data, err = mh._mutate_raw(customer_id, "campaigns", pause_ops, creds=creds)
                if err:
                    errors.append({"action": "pause", "error": err, "campaigns": [c["campaign_id"] for c in buckets["pause"]]})
                else:
                    for c in buckets["pause"]:
                        applied.append(
                            {
                                "action": "pause",
                                "campaign_id": c["campaign_id"],
                                "name": c["name"],
                                "reason": c["reason"],
                                "spend": c["spend"],
                                "clicks": c["clicks"],
                            }
                        )

        for c in buckets["reduce_budget"]:
            settings, s_err = mh.fetch_campaign_settings_rows(customer_id, campaign_id=c["campaign_id"])
            if s_err or not settings:
                errors.append({"action": "reduce_budget", "campaign_id": c["campaign_id"], "error": s_err})
                continue
            s = settings[0]
            current = float(s.get("daily_budget") or 0)
            if current <= 0:
                continue
            new_budget = max(current * (1 - budget_reduce_pct / 100), 1.0)
            amt = mh.currency_to_micros(new_budget)
            data, err, warning = mh.mutate_campaign_budget_amount(
                customer_id, s["budget_resource_name"], amt, creds=creds
            )
            entry = {
                "action": "reduce_budget",
                "campaign_id": c["campaign_id"],
                "name": c["name"],
                "reason": c["reason"],
                "previous_daily_budget": current,
                "new_daily_budget": round(new_budget, 2),
                "reduce_pct": budget_reduce_pct,
            }
            if warning:
                entry["warning"] = warning
            if err:
                entry["error"] = err
                errors.append(entry)
            else:
                applied.append(entry)

        if increase_pct > 0:
            for c in buckets["increase_budget"]:
                settings, s_err = mh.fetch_campaign_settings_rows(customer_id, campaign_id=c["campaign_id"])
                if s_err or not settings:
                    errors.append({"action": "increase_budget", "campaign_id": c["campaign_id"], "error": s_err})
                    continue
                s = settings[0]
                current = float(s.get("daily_budget") or 0)
                if current <= 0:
                    continue
                new_budget = current * (1 + increase_pct / 100)
                amt = mh.currency_to_micros(new_budget)
                data, err, warning = mh.mutate_campaign_budget_amount(
                    customer_id, s["budget_resource_name"], amt, creds=creds
                )
                entry = {
                    "action": "increase_budget",
                    "campaign_id": c["campaign_id"],
                    "name": c["name"],
                    "reason": c["reason"],
                    "previous_daily_budget": current,
                    "new_daily_budget": round(new_budget, 2),
                    "increase_pct": increase_pct,
                }
                if warning:
                    entry["warning"] = warning
                if err:
                    entry["error"] = err
                    errors.append(entry)
                else:
                    applied.append(entry)

        if add_search_term_negatives:
            targets = buckets["pause"] + buckets["reduce_budget"]
            for c in targets[:5]:
                waste, w_err = mh.fetch_waste_search_terms(
                    customer_id,
                    c["campaign_id"],
                    days,
                    mh.currency_to_micros(search_term_min_cost),
                    0.0,
                    limit=15,
                )
                if w_err or not waste:
                    continue
                terms = [w["search_term"] for w in waste]
                existing, _ = mh.existing_negative_keywords_campaign(customer_id, c["campaign_id"])
                ops, skipped, resource = mh.build_negative_keyword_operations(
                    customer_id, terms, "campaign", c["campaign_id"], None, "PHRASE", existing
                )
                if not ops:
                    continue
                data, err = mh._mutate_raw(customer_id, resource, ops, creds=creds)
                applied.append(
                    {
                        "action": "add_negatives",
                        "campaign_id": c["campaign_id"],
                        "added": len(ops),
                        "skipped": skipped,
                        "error": err,
                    }
                )

        currency = mh.fetch_account_currency_code(customer_id)
        lines = [
            f"# Weekly performance actions — `{format_customer_id(customer_id)}`",
            "",
            f"- **Window:** last {days} days",
            f"- **Currency:** {currency}",
            f"- **Blended CPA:** {blended_cpa:.2f} {currency}" if blended_cpa else "- **Blended CPA:** n/a",
            f"- **Enabled campaigns with spend:** {len(enabled)}",
            "",
            "## Classification",
            "",
            f"- **Pause candidates:** {len(buckets['pause'])}",
            f"- **Reduce budget:** {len(buckets['reduce_budget'])}",
            f"- **Increase budget:** {len(buckets['increase_budget'])}",
            f"- **Maintain:** {len(buckets['maintain'])}",
            "",
            "## Applied changes",
            "",
        ]
        if not applied:
            lines.append("_No changes applied (no candidates or all failed)._")
        for a in applied:
            lines.append(f"- **{a.get('action')}** `{a.get('campaign_id')}` {a.get('name', '')[:40]} — {a.get('reason', '')}")

        if errors:
            lines.extend(["", "## Errors", ""])
            for e in errors:
                lines.append(f"- {json.dumps(e, default=str)}")

        report = "\n".join(lines)

        if save_audit_to_supabase and applied:
            await _audit_memory(customer_id, report[:8000])

        return report
    except Exception as e:
        logger.exception("apply_weekly_performance_actions failed")
        return f"Error applying weekly performance actions: {e}"


@mcp.tool()
async def analyze_and_apply_campaign_edits(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    actions: Annotated[
        List[Dict[str, Any]],
        Field(
            description=(
                "Explicit actions, e.g. "
                '[{"type":"pause","campaign_id":"123"}, '
                '{"type":"budget","campaign_id":"123","daily_budget":5000}, '
                '{"type":"negatives","campaign_id":"123","keywords":["free"]}]'
            )
        ),
    ],
    save_audit_to_supabase: Annotated[bool, Field(description="Persist summary via save_memory")] = True,
) -> str:
    """
    Apply an explicit list of campaign edits after reviewing analysis output.

    Supported action types: pause, enable, budget, negatives, rename.
    """
    try:
        if not actions:
            return "actions list cannot be empty."

        creds = get_credentials()
        results: List[Dict[str, Any]] = []

        for action in actions:
            atype = (action.get("type") or "").lower()
            cid = str(action.get("campaign_id", "")).strip()
            if not cid and atype != "negatives":
                results.append({"error": "missing campaign_id", "action": action})
                continue

            if atype in ("pause", "enable"):
                st = "PAUSED" if atype == "pause" else "ENABLED"
                data, err = mh.mutate_campaign_update(
                    customer_id, cid, {"status": st}, ["status"], creds=creds
                )
                results.append({"type": atype, "campaign_id": cid, "ok": not err, "error": err})

            elif atype == "budget":
                db = action.get("daily_budget")
                if db is None:
                    results.append({"type": atype, "campaign_id": cid, "error": "daily_budget required"})
                    continue
                settings, err = mh.fetch_campaign_settings_rows(customer_id, campaign_id=cid)
                if err or not settings:
                    results.append({"type": atype, "campaign_id": cid, "error": err or "not found"})
                    continue
                amt = mh.currency_to_micros(float(db))
                data, m_err, warning = mh.mutate_campaign_budget_amount(
                    customer_id, settings[0]["budget_resource_name"], amt, creds=creds
                )
                entry = {"type": atype, "campaign_id": cid, "daily_budget": db, "ok": not m_err, "error": m_err}
                if warning:
                    entry["warning"] = warning
                results.append(entry)

            elif atype == "negatives":
                kws = action.get("keywords") or []
                if not kws:
                    results.append({"type": atype, "error": "keywords required"})
                    continue
                if not cid:
                    results.append({"type": atype, "error": "campaign_id required for negatives"})
                    continue
                existing, err = mh.existing_negative_keywords_campaign(customer_id, cid)
                ops, skipped, resource = mh.build_negative_keyword_operations(
                    customer_id, kws, "campaign", cid, None, action.get("match_type", "PHRASE"), existing
                )
                if not ops:
                    results.append({"type": atype, "campaign_id": cid, "skipped": skipped, "added": 0})
                    continue
                data, m_err = mh._mutate_raw(customer_id, resource, ops, creds=creds)
                results.append(
                    {
                        "type": atype,
                        "campaign_id": cid,
                        "added": len(ops),
                        "skipped": skipped,
                        "ok": not m_err,
                        "error": m_err,
                    }
                )

            elif atype == "rename":
                new_name = action.get("new_name")
                if not new_name:
                    results.append({"type": atype, "campaign_id": cid, "error": "new_name required"})
                    continue
                data, err = mh.mutate_campaign_update(
                    customer_id, cid, {"name": str(new_name)}, ["name"], creds=creds
                )
                results.append({"type": atype, "campaign_id": cid, "new_name": new_name, "ok": not err, "error": err})

            else:
                results.append({"error": f"unknown action type: {atype}", "action": action})

        summary = json.dumps({"customer_id": format_customer_id(customer_id), "results": results}, indent=2)
        if save_audit_to_supabase:
            await _audit_memory(customer_id, summary[:8000])
        return summary
    except Exception as e:
        return f"Error applying campaign edits: {e}"
