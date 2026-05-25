"""
MCP tools for Google Ads campaign / ad group / keyword mutations.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, Dict, List, Optional

from pydantic import Field

import mutate_helpers as mh
from google_ads_server import format_customer_id, get_credentials, mcp

logger = logging.getLogger("google_ads_server")

VALID_CAMPAIGN_STATUS = frozenset({"ENABLED", "PAUSED"})
VALID_AD_GROUP_STATUS = frozenset({"ENABLED", "PAUSED"})
VALID_BIDDING_STRATEGIES = frozenset(
    {
        "MAXIMIZE_CONVERSIONS",
        "MAXIMIZE_CONVERSION_VALUE",
        "TARGET_CPA",
        "TARGET_ROAS",
        "MANUAL_CPC",
    }
)


def _json_out(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
async def get_campaign_settings(
    customer_id: Annotated[str, Field(description="Google Ads customer ID (10 digits)")],
    campaign_id: Annotated[Optional[str], Field(description="Campaign ID")] = None,
    campaign_name: Annotated[Optional[str], Field(description="Exact campaign name")] = None,
    format: Annotated[str, Field(description="table or json")] = "table",
) -> str:
    """
    Read campaign settings before making edits: status, budget, bidding, channel type.
    """
    try:
        rows, err = mh.fetch_campaign_settings_rows(customer_id, campaign_id, campaign_name)
        if err:
            return f"Error fetching campaign settings: {err}"
        if not rows:
            return "No campaigns found for the given filters."

        currency = mh.fetch_account_currency_code(customer_id)
        if (format or "table").lower() == "json":
            return _json_out({"customer_id": format_customer_id(customer_id), "currency": currency, "campaigns": rows})

        lines = [
            f"Campaign settings for account {format_customer_id(customer_id)} (currency: {currency}):",
            "-" * 100,
            "id | name | status | channel | bidding | daily_budget | shared_budget | budget_id",
            "-" * 100,
        ]
        for r in rows:
            shared = "yes" if r.get("budget_reference_count", 0) and int(r.get("budget_reference_count") or 0) > 1 else "no"
            lines.append(
                f"{r['campaign_id']} | {r['campaign_name'][:40]} | {r['status']} | {r['channel_type']} | "
                f"{r['bidding_strategy_type']} | {r['daily_budget']:.2f} | {shared} | {r['budget_id']}"
            )
        return "\n".join(lines)
    except Exception as e:
        logger.exception("get_campaign_settings failed")
        return f"Error fetching campaign settings: {e}"


@mcp.tool()
async def list_campaign_budgets(
    customer_id: Annotated[str, Field(description="Google Ads customer ID (10 digits)")],
    format: Annotated[str, Field(description="table or json")] = "table",
) -> str:
    """List campaign budgets with amounts and shared-budget reference counts."""
    try:
        rows, err = mh.fetch_campaign_budgets(customer_id)
        if err:
            return f"Error listing budgets: {err}"
        currency = mh.fetch_account_currency_code(customer_id)
        if (format or "table").lower() == "json":
            return _json_out({"customer_id": format_customer_id(customer_id), "currency": currency, "budgets": rows})

        lines = [
            f"Campaign budgets for account {format_customer_id(customer_id)} (currency: {currency}):",
            "-" * 90,
            "budget_id | name | daily_budget | shared | reference_count | status",
            "-" * 90,
        ]
        for b in rows:
            shared = "yes" if b.get("explicitly_shared") else "no"
            lines.append(
                f"{b['budget_id']} | {(b['name'] or '')[:30]} | {b['daily_budget']:.2f} | {shared} | "
                f"{b.get('reference_count', '')} | {b['status']}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing budgets: {e}"


@mcp.tool()
async def update_campaign_status(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    status: Annotated[str, Field(description="ENABLED or PAUSED")],
    campaign_id: Annotated[Optional[str], Field(description="Campaign ID")] = None,
    campaign_name: Annotated[Optional[str], Field(description="Campaign name if ID omitted")] = None,
) -> str:
    """Pause or enable a campaign."""
    try:
        st = status.upper()
        if st not in VALID_CAMPAIGN_STATUS:
            return f"status must be one of: {', '.join(sorted(VALID_CAMPAIGN_STATUS))}"

        camp, err = mh.resolve_campaign(customer_id, campaign_id, campaign_name)
        if err or not camp:
            return err or "Campaign not found."

        creds = get_credentials()
        data, m_err = mh.mutate_campaign_update(
            customer_id, camp["id"], {"status": st}, ["status"], creds=creds
        )
        if m_err:
            return f"Error updating campaign status: {m_err}"

        return _json_out(
            {
                "status": "updated",
                "campaign_id": camp["id"],
                "campaign_name": camp["name"],
                "previous_status": camp["status"],
                "new_status": st,
                "mutate": mh.format_mutate_results(data),
            }
        )
    except Exception as e:
        return f"Error updating campaign status: {e}"


@mcp.tool()
async def update_campaign_budget(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    daily_budget: Annotated[float, Field(description="New daily budget in account currency (e.g. INR)")],
    campaign_id: Annotated[str, Field(description="Campaign ID")],
) -> str:
    """Update daily budget for the campaign's linked campaignBudget resource."""
    try:
        if daily_budget <= 0:
            return "daily_budget must be positive."

        settings, err = mh.fetch_campaign_settings_rows(customer_id, campaign_id=campaign_id)
        if err:
            return f"Error resolving campaign budget: {err}"
        if not settings:
            return "Campaign not found."

        s = settings[0]
        budget_rn = s.get("budget_resource_name")
        if not budget_rn:
            return "Campaign has no linked campaign budget."

        amount_micros = mh.currency_to_micros(daily_budget)
        creds = get_credentials()
        data, m_err, warning = mh.mutate_campaign_budget_amount(
            customer_id, budget_rn, amount_micros, creds=creds
        )
        if m_err:
            return f"Error updating budget: {m_err}"

        payload: Dict[str, Any] = {
            "status": "updated",
            "campaign_id": s["campaign_id"],
            "campaign_name": s["campaign_name"],
            "budget_resource_name": budget_rn,
            "previous_daily_budget": s["daily_budget"],
            "new_daily_budget": daily_budget,
            "amount_micros": amount_micros,
            "mutate": mh.format_mutate_results(data),
        }
        if warning:
            payload["warning"] = warning
        return _json_out(payload)
    except Exception as e:
        return f"Error updating campaign budget: {e}"


@mcp.tool()
async def update_campaign_bidding(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    campaign_id: Annotated[str, Field(description="Campaign ID")],
    strategy: Annotated[
        str,
        Field(
            description="MAXIMIZE_CONVERSIONS, MAXIMIZE_CONVERSION_VALUE, TARGET_CPA, TARGET_ROAS, or MANUAL_CPC"
        ),
    ],
    target_cpa: Annotated[
        Optional[float], Field(description="Target CPA in account currency (for TARGET_CPA / MAXIMIZE_CONVERSIONS)")
    ] = None,
    target_roas: Annotated[Optional[float], Field(description="Target ROAS (for TARGET_ROAS / MAXIMIZE_CONVERSION_VALUE)")] = None,
) -> str:
    """Update campaign bidding strategy (Search campaigns; limited support for other channel types)."""
    try:
        strat = strategy.upper().replace(" ", "_")
        if strat not in VALID_BIDDING_STRATEGIES:
            return f"strategy must be one of: {', '.join(sorted(VALID_BIDDING_STRATEGIES))}"

        camp, err = mh.resolve_campaign(customer_id, campaign_id=campaign_id)
        if err or not camp:
            return err or "Campaign not found."

        channel = (camp.get("advertising_channel_type") or "").upper()
        if channel and channel not in ("SEARCH", "SHOPPING", "PERFORMANCE_MAX", "UNKNOWN", ""):
            return f"Bidding updates for channel type {channel} may fail; test on a single campaign first."

        update_fields: Dict[str, Any] = {}
        mask: List[str] = []

        if strat == "MANUAL_CPC":
            update_fields["manualCpc"] = {}
            update_fields["biddingStrategyType"] = "MANUAL_CPC"
            mask = ["manualCpc", "biddingStrategyType"]
        elif strat == "MAXIMIZE_CONVERSIONS":
            update_fields["biddingStrategyType"] = "MAXIMIZE_CONVERSIONS"
            update_fields["maximizeConversions"] = {}
            mask = ["biddingStrategyType", "maximizeConversions"]
            if target_cpa is not None:
                update_fields["maximizeConversions"] = {
                    "targetCpaMicros": str(mh.currency_to_micros(target_cpa))
                }
                mask.append("maximizeConversions.targetCpaMicros")
        elif strat == "TARGET_CPA":
            if target_cpa is None:
                return "target_cpa is required for TARGET_CPA strategy."
            update_fields["biddingStrategyType"] = "TARGET_CPA"
            update_fields["targetCpa"] = {"targetCpaMicros": str(mh.currency_to_micros(target_cpa))}
            mask = ["biddingStrategyType", "targetCpa"]
        elif strat == "TARGET_ROAS":
            if target_roas is None:
                return "target_roas is required for TARGET_ROAS strategy."
            update_fields["biddingStrategyType"] = "TARGET_ROAS"
            update_fields["targetRoas"] = {"targetRoas": float(target_roas)}
            mask = ["biddingStrategyType", "targetRoas"]
        elif strat == "MAXIMIZE_CONVERSION_VALUE":
            update_fields["biddingStrategyType"] = "MAXIMIZE_CONVERSION_VALUE"
            update_fields["maximizeConversionValue"] = {}
            mask = ["biddingStrategyType", "maximizeConversionValue"]
            if target_roas is not None:
                update_fields["maximizeConversionValue"] = {"targetRoas": float(target_roas)}
                mask.append("maximizeConversionValue.targetRoas")

        creds = get_credentials()
        data, m_err = mh.mutate_campaign_update(
            customer_id, camp["id"], update_fields, mask, creds=creds
        )
        if m_err:
            return f"Error updating bidding: {m_err}"

        return _json_out(
            {
                "status": "updated",
                "campaign_id": camp["id"],
                "campaign_name": camp["name"],
                "strategy": strat,
                "target_cpa": target_cpa,
                "target_roas": target_roas,
                "mutate": mh.format_mutate_results(data),
            }
        )
    except Exception as e:
        return f"Error updating bidding: {e}"


@mcp.tool()
async def rename_campaign(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    campaign_id: Annotated[str, Field(description="Campaign ID")],
    new_name: Annotated[str, Field(description="New campaign name")],
) -> str:
    """Rename a campaign."""
    try:
        name = (new_name or "").strip()
        if not name:
            return "new_name is required."

        camp, err = mh.resolve_campaign(customer_id, campaign_id=campaign_id)
        if err or not camp:
            return err or "Campaign not found."

        creds = get_credentials()
        data, m_err = mh.mutate_campaign_update(
            customer_id, camp["id"], {"name": name}, ["name"], creds=creds
        )
        if m_err:
            return f"Error renaming campaign: {m_err}"

        return _json_out(
            {
                "status": "updated",
                "campaign_id": camp["id"],
                "previous_name": camp["name"],
                "new_name": name,
                "mutate": mh.format_mutate_results(data),
            }
        )
    except Exception as e:
        return f"Error renaming campaign: {e}"


@mcp.tool()
async def update_ad_group_status(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    status: Annotated[str, Field(description="ENABLED or PAUSED")],
    ad_group_id: Annotated[Optional[str], Field(description="Ad group ID")] = None,
    ad_group_name: Annotated[Optional[str], Field(description="Ad group name")] = None,
    campaign_id: Annotated[Optional[str], Field(description="Disambiguate ad group by campaign")] = None,
) -> str:
    """Pause or enable an ad group."""
    try:
        st = status.upper()
        if st not in VALID_AD_GROUP_STATUS:
            return f"status must be one of: {', '.join(sorted(VALID_AD_GROUP_STATUS))}"

        ag, err = mh.resolve_ad_group(customer_id, ad_group_id, ad_group_name, campaign_id)
        if err or not ag:
            return err or "Ad group not found."

        creds = get_credentials()
        data, m_err = mh.mutate_ad_group_status(customer_id, ag["id"], st, creds=creds)
        if m_err:
            return f"Error updating ad group status: {m_err}"

        return _json_out(
            {
                "status": "updated",
                "ad_group_id": ag["id"],
                "ad_group_name": ag["name"],
                "campaign_id": ag["campaign_id"],
                "previous_status": ag["status"],
                "new_status": st,
                "mutate": mh.format_mutate_results(data),
            }
        )
    except Exception as e:
        return f"Error updating ad group status: {e}"


@mcp.tool()
async def add_negative_keywords(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    keywords: Annotated[List[str], Field(description="Keywords to add as negatives")],
    level: Annotated[str, Field(description="campaign or ad_group")],
    campaign_id: Annotated[str, Field(description="Campaign ID")],
    ad_group_id: Annotated[Optional[str], Field(description="Required when level=ad_group")] = None,
    match_type: Annotated[str, Field(description="EXACT, PHRASE, or BROAD")] = "PHRASE",
) -> str:
    """Add negative keywords at campaign or ad group level."""
    try:
        if not keywords:
            return "keywords list cannot be empty."
        lvl = (level or "campaign").lower()
        if lvl not in ("campaign", "ad_group"):
            return "level must be 'campaign' or 'ad_group'."
        if lvl == "ad_group" and not ad_group_id:
            return "ad_group_id is required when level=ad_group."

        if lvl == "campaign":
            existing, err = mh.existing_negative_keywords_campaign(customer_id, campaign_id)
        else:
            existing, err = mh.existing_negative_keywords_ad_group(customer_id, ad_group_id or "")
        if err:
            return f"Error checking existing negatives: {err}"

        ops, skipped, resource = mh.build_negative_keyword_operations(
            customer_id, keywords, lvl, campaign_id, ad_group_id, match_type, existing
        )
        if not ops:
            return _json_out({"status": "no_changes", "skipped_duplicates": skipped, "added": 0})

        creds = get_credentials()
        data, m_err = mh._mutate_raw(customer_id, resource, ops, creds=creds)
        if m_err:
            return f"Error adding negative keywords: {m_err}"

        return _json_out(
            {
                "status": "updated",
                "level": lvl,
                "campaign_id": campaign_id,
                "ad_group_id": ad_group_id,
                "added": len(ops),
                "skipped_duplicates": skipped,
                "mutate": mh.format_mutate_results(data),
            }
        )
    except Exception as e:
        return f"Error adding negative keywords: {e}"


@mcp.tool()
async def add_campaign_negative_keywords_from_search_terms(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    campaign_id: Annotated[str, Field(description="Campaign ID")],
    days: Annotated[int, Field(description="Lookback days for search terms")] = 7,
    min_cost: Annotated[float, Field(description="Minimum spend in account currency to qualify as waste")] = 100.0,
    max_conversions: Annotated[float, Field(description="Max conversions (0 = zero-conv terms only)")] = 0.0,
    limit: Annotated[int, Field(description="Max search terms to add")] = 25,
    match_type: Annotated[str, Field(description="EXACT or PHRASE")] = "PHRASE",
) -> str:
    """
    Find high-spend low-conversion search terms and add them as campaign-level negatives.
    """
    try:
        min_micros = mh.currency_to_micros(min_cost)
        waste, err = mh.fetch_waste_search_terms(
            customer_id, campaign_id, days, min_micros, max_conversions, limit=limit
        )
        if err:
            return f"Error fetching search terms: {err}"
        if not waste:
            return _json_out(
                {
                    "status": "no_waste_terms",
                    "campaign_id": campaign_id,
                    "message": f"No terms found with spend >= {min_cost} and conversions <= {max_conversions}.",
                }
            )

        terms = [w["search_term"] for w in waste]
        existing, err = mh.existing_negative_keywords_campaign(customer_id, campaign_id)
        if err:
            return f"Error checking existing negatives: {err}"

        ops, skipped, resource = mh.build_negative_keyword_operations(
            customer_id, terms, "campaign", campaign_id, None, match_type, existing
        )
        if not ops:
            return _json_out(
                {
                    "status": "no_changes",
                    "campaign_id": campaign_id,
                    "candidate_terms": terms,
                    "skipped_duplicates": skipped,
                }
            )

        creds = get_credentials()
        data, m_err = mh._mutate_raw(customer_id, resource, ops, creds=creds)
        if m_err:
            return f"Error adding negatives from search terms: {m_err}"

        return _json_out(
            {
                "status": "updated",
                "campaign_id": campaign_id,
                "added": len(ops),
                "skipped_duplicates": skipped,
                "terms_added": [t for t in terms if t.lower() not in {s.lower() for s in skipped}],
                "waste_terms": waste,
                "mutate": mh.format_mutate_results(data),
            }
        )
    except Exception as e:
        return f"Error adding negatives from search terms: {e}"


@mcp.tool()
async def bulk_update_campaigns(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    operations: Annotated[
        List[Dict[str, Any]],
        Field(
            description=(
                "List of ops: {campaign_id, status?, daily_budget?, target_cpa?, strategy?}. "
                "Applied in order; budget/bidding use separate API calls per campaign."
            )
        ),
    ],
) -> str:
    """Apply multiple campaign updates (status, budget, bidding) in one tool call."""
    try:
        if not operations:
            return "operations list cannot be empty."

        creds = get_credentials()
        results: List[Dict[str, Any]] = []
        status_ops: List[Dict[str, Any]] = []

        for op in operations:
            cid = str(op.get("campaign_id", "")).strip()
            if not cid:
                results.append({"error": "missing campaign_id", "op": op})
                continue

            if op.get("status"):
                st = str(op["status"]).upper()
                if st in VALID_CAMPAIGN_STATUS:
                    status_ops.append(
                        {
                            "updateMask": "status",
                            "update": {
                                "resourceName": mh.campaign_resource_name(customer_id, cid),
                                "status": st,
                            },
                        }
                    )
                    results.append({"campaign_id": cid, "action": "status", "new_status": st, "pending": True})

            if op.get("daily_budget") is not None:
                settings, err = mh.fetch_campaign_settings_rows(customer_id, campaign_id=cid)
                if err or not settings:
                    results.append({"campaign_id": cid, "action": "budget", "error": err or "not found"})
                else:
                    s = settings[0]
                    amt = mh.currency_to_micros(float(op["daily_budget"]))
                    data, m_err, warning = mh.mutate_campaign_budget_amount(
                        customer_id, s["budget_resource_name"], amt, creds=creds
                    )
                    entry = {
                        "campaign_id": cid,
                        "action": "budget",
                        "new_daily_budget": op["daily_budget"],
                        "ok": not m_err,
                        "error": m_err,
                    }
                    if warning:
                        entry["warning"] = warning
                    if data:
                        entry["mutate"] = mh.format_mutate_results(data)
                    results.append(entry)

            if op.get("strategy") or op.get("target_cpa") is not None:
                strat = str(op.get("strategy") or "MAXIMIZE_CONVERSIONS").upper()
                tcpa = op.get("target_cpa")
                update_fields: Dict[str, Any] = {"biddingStrategyType": strat}
                mask = ["biddingStrategyType"]
                if strat == "MAXIMIZE_CONVERSIONS" and tcpa is not None:
                    update_fields["maximizeConversions"] = {
                        "targetCpaMicros": str(mh.currency_to_micros(float(tcpa)))
                    }
                    mask.extend(["maximizeConversions", "maximizeConversions.targetCpaMicros"])
                elif strat == "TARGET_CPA" and tcpa is not None:
                    update_fields["targetCpa"] = {"targetCpaMicros": str(mh.currency_to_micros(float(tcpa)))}
                    mask.append("targetCpa")
                data, m_err = mh.mutate_campaign_update(customer_id, cid, update_fields, mask, creds=creds)
                results.append(
                    {
                        "campaign_id": cid,
                        "action": "bidding",
                        "strategy": strat,
                        "ok": not m_err,
                        "error": m_err,
                        "mutate": mh.format_mutate_results(data) if data else None,
                    }
                )

        if status_ops:
            data, m_err = mh._mutate_raw(customer_id, "campaigns", status_ops, creds=creds)
            for r in results:
                if r.get("pending") and r.get("action") == "status":
                    r.pop("pending", None)
                    r["ok"] = not m_err
                    r["error"] = m_err
                    if data:
                        r["mutate"] = mh.format_mutate_results(data)

        return _json_out({"customer_id": format_customer_id(customer_id), "results": results})
    except Exception as e:
        return f"Error in bulk_update_campaigns: {e}"
