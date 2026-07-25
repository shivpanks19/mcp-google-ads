"""
MCP tools for Google Ads campaign / ad group / keyword mutations.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Annotated, Any, Dict, List, Optional

from pydantic import Field

import mutate_helpers as mh
from google_ads_server import format_customer_id, get_credentials, mcp

logger = logging.getLogger("google_ads_server")

VALID_CAMPAIGN_STATUS = frozenset({"ENABLED", "PAUSED"})
VALID_AD_GROUP_STATUS = frozenset({"ENABLED", "PAUSED"})
VALID_KEYWORD_MATCH_TYPES = frozenset({"EXACT", "PHRASE", "BROAD"})
VALID_SEARCH_CAMPAIGN_BIDDING = frozenset({"MAXIMIZE_CLICKS", "MANUAL_CPC", "MAXIMIZE_CONVERSIONS"})
VALID_BIDDING_STRATEGIES = frozenset(
    {
        "MAXIMIZE_CONVERSIONS",
        "MAXIMIZE_CONVERSION_VALUE",
        "TARGET_CPA",
        "TARGET_ROAS",
        "MANUAL_CPC",
        "MAXIMIZE_CLICKS",
        "TARGET_SPEND",
    }
)


def _normalize_bidding_strategy(strategy: str) -> str:
    strat = (strategy or "").upper().replace(" ", "_")
    if strat in ("MAXIMIZE_CLICKS", "TARGET_SPEND"):
        return "TARGET_SPEND"
    return strat


def _bidding_update_fields(
    strategy: str,
    *,
    target_cpa: Optional[float] = None,
    target_roas: Optional[float] = None,
    cpc_bid_ceiling: Optional[float] = None,
) -> tuple[Dict[str, Any], List[str]]:
    """Build campaign mutate fields/mask for a bidding strategy switch."""
    strat = _normalize_bidding_strategy(strategy)
    update_fields: Dict[str, Any] = {}
    mask: List[str] = []

    if strat == "MANUAL_CPC":
        update_fields["manualCpc"] = {"enhancedCpcEnabled": False}
        mask = ["manual_cpc.enhanced_cpc_enabled"]
    elif strat == "TARGET_SPEND":
        update_fields["targetSpend"] = {}
        mask = ["target_spend"]
        if cpc_bid_ceiling is not None:
            update_fields["targetSpend"] = {
                "cpcBidCeilingMicros": str(mh.currency_to_micros(cpc_bid_ceiling))
            }
            mask = ["target_spend.cpc_bid_ceiling_micros"]
    elif strat == "MAXIMIZE_CONVERSIONS":
        update_fields["maximizeConversions"] = {}
        mask = ["maximize_conversions"]
        if target_cpa is not None:
            update_fields["maximizeConversions"] = {
                "targetCpaMicros": str(mh.currency_to_micros(target_cpa))
            }
            mask = ["maximize_conversions.target_cpa_micros"]
    elif strat == "TARGET_CPA":
        if target_cpa is None:
            raise ValueError("target_cpa is required for TARGET_CPA strategy.")
        update_fields["targetCpa"] = {"targetCpaMicros": str(mh.currency_to_micros(target_cpa))}
        mask = ["target_cpa.target_cpa_micros"]
    elif strat == "TARGET_ROAS":
        if target_roas is None:
            raise ValueError("target_roas is required for TARGET_ROAS strategy.")
        update_fields["targetRoas"] = {"targetRoas": float(target_roas)}
        mask = ["target_roas.target_roas"]
    elif strat == "MAXIMIZE_CONVERSION_VALUE":
        update_fields["maximizeConversionValue"] = {}
        mask = ["maximize_conversion_value"]
        if target_roas is not None:
            update_fields["maximizeConversionValue"] = {"targetRoas": float(target_roas)}
            mask = ["maximize_conversion_value.target_roas"]
    else:
        raise ValueError(
            f"strategy must be one of: {', '.join(sorted(VALID_BIDDING_STRATEGIES))}"
        )

    return update_fields, mask


def _json_out(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _mutations_disabled_by_env() -> bool:
    return os.environ.get("GOOGLE_ADS_DISABLE_MUTATIONS", "").strip().lower() in ("1", "true", "yes")


def _validate_only_forced() -> bool:
    return os.environ.get("GOOGLE_ADS_MUTATE_VALIDATE_ONLY", "").strip().lower() in ("1", "true", "yes")


def _effective_validate_only(validate_only: bool) -> bool:
    return bool(validate_only) or _validate_only_forced()


def _resource_id(resource_name: str) -> str:
    return str(resource_name or "").split("/")[-1]


def _gaql_string(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


def _extract_mutate_resource_names(data: Optional[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for result in (data or {}).get("results") or []:
        rn = result.get("resourceName")
        if rn:
            names.append(rn)
    return names


def _keyword_op(ad_group_resource_name: str, text: str, match_type: str, status: str = "PAUSED") -> Dict[str, Any]:
    mt = (match_type or "PHRASE").upper()
    if mt not in VALID_KEYWORD_MATCH_TYPES:
        mt = "PHRASE"
    return {
        "create": {
            "adGroup": ad_group_resource_name,
            "status": status.upper(),
            "keyword": {"text": text.strip(), "matchType": mt},
        }
    }


def _rsa_op(
    ad_group_resource_name: str,
    final_url: str,
    headlines: List[str],
    descriptions: List[str],
    status: str = "PAUSED",
) -> Dict[str, Any]:
    return {
        "create": {
            "adGroup": ad_group_resource_name,
            "status": status.upper(),
            "ad": {
                "finalUrls": [final_url],
                "responsiveSearchAd": {
                    "headlines": [{"text": h.strip()} for h in headlines if h and h.strip()],
                    "descriptions": [{"text": d.strip()} for d in descriptions if d and d.strip()],
                },
            },
        }
    }


def _existing_campaign_by_name(customer_id: str, name: str) -> Optional[Dict[str, Any]]:
    rows, err = mh.gaql_search(
        customer_id,
        f"""
        SELECT campaign.id, campaign.name, campaign.status, campaign.resource_name, campaign.advertising_channel_type
        FROM campaign
        WHERE campaign.name = '{_gaql_string(name)}'
          AND campaign.status != 'REMOVED'
        LIMIT 1
        """,
    )
    if err or not rows:
        return None
    c = rows[0].get("campaign") or {}
    return {
        "id": str(c.get("id", "")),
        "name": c.get("name") or "",
        "status": c.get("status") or "",
        "resource_name": c.get("resourceName") or c.get("resource_name") or "",
        "channel_type": c.get("advertisingChannelType") or c.get("advertising_channel_type") or "",
    }


def _existing_budget_by_name(customer_id: str, name: str) -> Optional[Dict[str, Any]]:
    rows, err = mh.gaql_search(
        customer_id,
        f"""
        SELECT campaign_budget.id, campaign_budget.name, campaign_budget.resource_name, campaign_budget.amount_micros
        FROM campaign_budget
        WHERE campaign_budget.name = '{_gaql_string(name)}'
          AND campaign_budget.status != 'REMOVED'
        LIMIT 1
        """,
    )
    if err or not rows:
        return None
    b = rows[0].get("campaignBudget") or rows[0].get("campaign_budget") or {}
    return {
        "id": str(b.get("id", "")),
        "name": b.get("name") or "",
        "resource_name": b.get("resourceName") or b.get("resource_name") or "",
        "amount_micros": b.get("amountMicros") or b.get("amount_micros"),
    }


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
async def create_campaign_budget(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    name: Annotated[str, Field(description="Campaign budget name")],
    daily_budget: Annotated[float, Field(description="Daily budget in account currency, e.g. INR")],
    explicitly_shared: Annotated[bool, Field(description="Whether the budget can be shared by multiple campaigns")] = False,
    validate_only: Annotated[bool, Field(description="Dry-run validate without applying")] = False,
    force_create: Annotated[bool, Field(description="Create even if a budget with the same name exists")] = False,
) -> str:
    """Create a campaign budget."""
    try:
        if _mutations_disabled_by_env():
            return "Mutations are disabled (GOOGLE_ADS_DISABLE_MUTATIONS=1)."
        validate_only = _effective_validate_only(validate_only)
        budget_name = (name or "").strip()
        if not budget_name:
            return "name is required."
        if daily_budget <= 0:
            return "daily_budget must be positive."

        existing = None if force_create else _existing_budget_by_name(customer_id, budget_name)
        if existing and not validate_only:
            return _json_out({"status": "exists", "budget": existing})

        op = {
            "create": {
                "name": budget_name,
                "amountMicros": str(mh.currency_to_micros(daily_budget)),
                "deliveryMethod": "STANDARD",
                "explicitlyShared": bool(explicitly_shared),
            }
        }
        data, err = mh._mutate_raw(
            customer_id,
            "campaignBudgets",
            [op],
            creds=get_credentials(),
            validate_only=validate_only,
        )
        if err:
            return f"Error creating campaign budget: {err}"
        names = _extract_mutate_resource_names(data)
        return _json_out(
            {
                "status": "validated" if validate_only else "created",
                "customer_id": format_customer_id(customer_id),
                "budget_name": budget_name,
                "daily_budget": daily_budget,
                "amount_micros": mh.currency_to_micros(daily_budget),
                "resource_name": names[0] if names else None,
                "mutate": mh.format_mutate_results(data),
            }
        )
    except Exception as e:
        logger.exception("create_campaign_budget failed")
        return f"Error creating campaign budget: {e}"


@mcp.tool()
async def create_search_campaign(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    name: Annotated[str, Field(description="Search campaign name")],
    campaign_budget_resource_name: Annotated[
        str,
        Field(description="Campaign budget resource name, e.g. customers/123/campaignBudgets/456"),
    ],
    status: Annotated[str, Field(description="PAUSED or ENABLED; default PAUSED")] = "PAUSED",
    bidding_strategy: Annotated[
        str,
        Field(description="MAXIMIZE_CLICKS, MANUAL_CPC, or MAXIMIZE_CONVERSIONS"),
    ] = "MAXIMIZE_CLICKS",
    cpc_bid_ceiling: Annotated[
        Optional[float],
        Field(description="Optional Max Clicks CPC ceiling in account currency"),
    ] = None,
    target_google_search: Annotated[bool, Field(description="Target Google Search")] = True,
    target_search_partners: Annotated[bool, Field(description="Target Google Search Partners")] = False,
    target_content_network: Annotated[bool, Field(description="Target Display/content network")] = False,
    positive_geo_target_type: Annotated[str, Field(description="PRESENCE or PRESENCE_OR_INTEREST")] = "PRESENCE",
    validate_only: Annotated[bool, Field(description="Dry-run validate without applying")] = False,
    force_create: Annotated[bool, Field(description="Create even if a campaign with the same name exists")] = False,
) -> str:
    """Create a paused Search campaign linked to an existing campaign budget."""
    try:
        if _mutations_disabled_by_env():
            return "Mutations are disabled (GOOGLE_ADS_DISABLE_MUTATIONS=1)."
        validate_only = _effective_validate_only(validate_only)
        campaign_name = (name or "").strip()
        if not campaign_name:
            return "name is required."
        st = status.upper()
        if st not in VALID_CAMPAIGN_STATUS:
            return f"status must be one of: {', '.join(sorted(VALID_CAMPAIGN_STATUS))}"
        strategy = bidding_strategy.upper().replace(" ", "_")
        if strategy not in VALID_SEARCH_CAMPAIGN_BIDDING:
            return f"bidding_strategy must be one of: {', '.join(sorted(VALID_SEARCH_CAMPAIGN_BIDDING))}"

        existing = None if force_create else _existing_campaign_by_name(customer_id, campaign_name)
        if existing and not validate_only:
            return _json_out({"status": "exists", "campaign": existing})

        campaign: Dict[str, Any] = {
            "name": campaign_name,
            "status": st,
            "advertisingChannelType": "SEARCH",
            "campaignBudget": campaign_budget_resource_name,
            "networkSettings": {
                "targetGoogleSearch": bool(target_google_search),
                "targetSearchNetwork": bool(target_search_partners),
                "targetContentNetwork": bool(target_content_network),
                "targetPartnerSearchNetwork": bool(target_search_partners),
            },
            "geoTargetTypeSetting": {
                "positiveGeoTargetType": positive_geo_target_type.upper(),
                "negativeGeoTargetType": "PRESENCE",
            },
            "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
        }
        if strategy == "MAXIMIZE_CLICKS":
            campaign["targetSpend"] = {}
            if cpc_bid_ceiling is not None:
                campaign["targetSpend"]["cpcBidCeilingMicros"] = str(
                    mh.currency_to_micros(cpc_bid_ceiling)
                )
        elif strategy == "MANUAL_CPC":
            campaign["manualCpc"] = {}
        elif strategy == "MAXIMIZE_CONVERSIONS":
            campaign["maximizeConversions"] = {}

        data, err = mh._mutate_raw(
            customer_id,
            "campaigns",
            [{"create": campaign}],
            creds=get_credentials(),
            validate_only=validate_only,
        )
        if err:
            return f"Error creating search campaign: {err}"
        names = _extract_mutate_resource_names(data)
        return _json_out(
            {
                "status": "validated" if validate_only else "created",
                "customer_id": format_customer_id(customer_id),
                "campaign_name": campaign_name,
                "resource_name": names[0] if names else None,
                "campaign_id": _resource_id(names[0]) if names else None,
                "mutate": mh.format_mutate_results(data),
            }
        )
    except Exception as e:
        logger.exception("create_search_campaign failed")
        return f"Error creating search campaign: {e}"


@mcp.tool()
async def create_ad_groups(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    campaign_id: Annotated[str, Field(description="Campaign ID")],
    ad_groups: Annotated[
        List[Dict[str, Any]],
        Field(description="List of {name, status?, cpc_bid?}; status defaults PAUSED"),
    ],
    validate_only: Annotated[bool, Field(description="Dry-run validate without applying")] = False,
) -> str:
    """Create ad groups under an existing campaign."""
    try:
        if _mutations_disabled_by_env():
            return "Mutations are disabled (GOOGLE_ADS_DISABLE_MUTATIONS=1)."
        validate_only = _effective_validate_only(validate_only)
        if not ad_groups:
            return "ad_groups list cannot be empty."
        campaign_rn = mh.campaign_resource_name(customer_id, re.sub(r"\D", "", str(campaign_id)))
        ops: List[Dict[str, Any]] = []
        for ag in ad_groups:
            name = str(ag.get("name") or "").strip()
            if not name:
                return "Each ad group requires name."
            status = str(ag.get("status") or "PAUSED").upper()
            if status not in VALID_AD_GROUP_STATUS:
                return f"Invalid status for {name}: {status}"
            create: Dict[str, Any] = {"name": name, "campaign": campaign_rn, "status": status, "type": "SEARCH_STANDARD"}
            if ag.get("cpc_bid") is not None:
                create["cpcBidMicros"] = str(mh.currency_to_micros(float(ag["cpc_bid"])))
            ops.append({"create": create})

        data, err = mh._mutate_raw(customer_id, "adGroups", ops, creds=get_credentials(), validate_only=validate_only)
        if err:
            return f"Error creating ad groups: {err}"
        names = _extract_mutate_resource_names(data)
        return _json_out(
            {
                "status": "validated" if validate_only else "created",
                "created_count": len(ops),
                "resource_names": names,
                "ad_group_ids": [_resource_id(n) for n in names],
                "mutate": mh.format_mutate_results(data),
            }
        )
    except Exception as e:
        logger.exception("create_ad_groups failed")
        return f"Error creating ad groups: {e}"


@mcp.tool()
async def create_keywords(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    ad_group_id: Annotated[str, Field(description="Ad group ID")],
    keywords: Annotated[
        List[Dict[str, Any]],
        Field(description="List of {text, match_type?, status?}; match_type defaults PHRASE, status defaults PAUSED"),
    ],
    validate_only: Annotated[bool, Field(description="Dry-run validate without applying")] = False,
) -> str:
    """Create positive keywords in an ad group."""
    try:
        if _mutations_disabled_by_env():
            return "Mutations are disabled (GOOGLE_ADS_DISABLE_MUTATIONS=1)."
        validate_only = _effective_validate_only(validate_only)
        if not keywords:
            return "keywords list cannot be empty."
        ad_group_rn = mh.ad_group_resource_name(customer_id, re.sub(r"\D", "", str(ad_group_id)))
        ops: List[Dict[str, Any]] = []
        for kw in keywords:
            text = str(kw.get("text") or "").strip()
            if not text:
                continue
            status = str(kw.get("status") or "PAUSED").upper()
            if status not in VALID_AD_GROUP_STATUS:
                return f"Invalid keyword status for {text}: {status}"
            ops.append(_keyword_op(ad_group_rn, text, str(kw.get("match_type") or "PHRASE"), status))
        if not ops:
            return "No valid keywords to create."

        data, err = mh._mutate_raw(
            customer_id,
            "adGroupCriteria",
            ops,
            creds=get_credentials(),
            validate_only=validate_only,
            partial_failure=True,
        )
        if err:
            return f"Error creating keywords: {err}"
        return _json_out(
            {
                "status": "validated" if validate_only else "created",
                "keyword_count": len(ops),
                "mutate": mh.format_mutate_results(data),
            }
        )
    except Exception as e:
        logger.exception("create_keywords failed")
        return f"Error creating keywords: {e}"


@mcp.tool()
async def create_responsive_search_ad(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    ad_group_id: Annotated[str, Field(description="Ad group ID")],
    final_url: Annotated[str, Field(description="Final URL for the RSA")],
    headlines: Annotated[List[str], Field(description="RSA headlines, 3-15 items")],
    descriptions: Annotated[List[str], Field(description="RSA descriptions, 2-4 items")],
    status: Annotated[str, Field(description="PAUSED or ENABLED; default PAUSED")] = "PAUSED",
    validate_only: Annotated[bool, Field(description="Dry-run validate without applying")] = False,
) -> str:
    """Create a responsive search ad in an ad group."""
    try:
        if _mutations_disabled_by_env():
            return "Mutations are disabled (GOOGLE_ADS_DISABLE_MUTATIONS=1)."
        validate_only = _effective_validate_only(validate_only)
        st = status.upper()
        if st not in VALID_AD_GROUP_STATUS:
            return f"status must be one of: {', '.join(sorted(VALID_AD_GROUP_STATUS))}"
        clean_headlines = [h.strip() for h in headlines if h and h.strip()]
        clean_descriptions = [d.strip() for d in descriptions if d and d.strip()]
        if not (3 <= len(clean_headlines) <= 15):
            return "Responsive search ads require 3 to 15 headlines."
        if not (2 <= len(clean_descriptions) <= 4):
            return "Responsive search ads require 2 to 4 descriptions."
        if not final_url or not final_url.startswith(("http://", "https://")):
            return "final_url must start with http:// or https://."

        ad_group_rn = mh.ad_group_resource_name(customer_id, re.sub(r"\D", "", str(ad_group_id)))
        op = _rsa_op(ad_group_rn, final_url, clean_headlines, clean_descriptions, st)
        data, err = mh._mutate_raw(customer_id, "adGroupAds", [op], creds=get_credentials(), validate_only=validate_only)
        if err:
            return f"Error creating responsive search ad: {err}"
        names = _extract_mutate_resource_names(data)
        return _json_out(
            {
                "status": "validated" if validate_only else "created",
                "resource_name": names[0] if names else None,
                "mutate": mh.format_mutate_results(data),
            }
        )
    except Exception as e:
        logger.exception("create_responsive_search_ad failed")
        return f"Error creating responsive search ad: {e}"


@mcp.tool()
async def create_campaign_location_targets(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    campaign_id: Annotated[str, Field(description="Campaign ID")],
    geo_target_constant_ids: Annotated[
        List[str],
        Field(description="Geo target constant IDs, e.g. 2356 for India"),
    ],
    negative: Annotated[bool, Field(description="Create negative location criteria")] = False,
    validate_only: Annotated[bool, Field(description="Dry-run validate without applying")] = False,
) -> str:
    """Create campaign-level location targets."""
    try:
        if _mutations_disabled_by_env():
            return "Mutations are disabled (GOOGLE_ADS_DISABLE_MUTATIONS=1)."
        validate_only = _effective_validate_only(validate_only)
        campaign_rn = mh.campaign_resource_name(customer_id, re.sub(r"\D", "", str(campaign_id)))
        ops: List[Dict[str, Any]] = []
        for gid in geo_target_constant_ids:
            digits = re.sub(r"\D", "", str(gid))
            if not digits:
                continue
            ops.append(
                {
                    "create": {
                        "campaign": campaign_rn,
                        "negative": bool(negative),
                        "location": {"geoTargetConstant": f"geoTargetConstants/{digits}"},
                    }
                }
            )
        if not ops:
            return "geo_target_constant_ids must contain at least one valid ID."
        data, err = mh._mutate_raw(customer_id, "campaignCriteria", ops, creds=get_credentials(), validate_only=validate_only)
        if err:
            return f"Error creating campaign location targets: {err}"
        return _json_out(
            {
                "status": "validated" if validate_only else "created",
                "location_count": len(ops),
                "mutate": mh.format_mutate_results(data),
            }
        )
    except Exception as e:
        logger.exception("create_campaign_location_targets failed")
        return f"Error creating campaign location targets: {e}"


@mcp.tool()
async def create_paused_search_campaign_build(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    campaign_name: Annotated[str, Field(description="Search campaign name")],
    daily_budget: Annotated[float, Field(description="Daily budget in account currency")],
    final_url: Annotated[str, Field(description="Final URL for all RSAs")],
    ad_groups: Annotated[
        List[Dict[str, Any]],
        Field(
            description=(
                "List of ad groups: {name, keywords:[{text,match_type}], "
                "headlines:[...], descriptions:[...]}"
            )
        ),
    ],
    negative_keywords: Annotated[List[str], Field(description="Campaign-level negative keyword texts")] = [],
    geo_target_constant_ids: Annotated[List[str], Field(description="Geo target IDs; default empty")] = [],
    campaign_budget_resource_name: Annotated[
        Optional[str],
        Field(description="Reuse an existing campaign budget resource name; skips budget create"),
    ] = None,
    validate_only: Annotated[bool, Field(description="Dry-run validate without applying")] = False,
) -> str:
    """
    Create a complete paused Search build: budget, campaign, optional locations,
    ad groups, keywords, one RSA per ad group, and campaign negatives.
    """
    try:
        if _mutations_disabled_by_env():
            return "Mutations are disabled (GOOGLE_ADS_DISABLE_MUTATIONS=1)."
        validate_only = _effective_validate_only(validate_only)
        if not ad_groups:
            return "ad_groups list cannot be empty."
        if not final_url.startswith(("http://", "https://")):
            return "final_url must start with http:// or https://."

        creds = get_credentials()
        fid = format_customer_id(customer_id)
        budget_name = f"{campaign_name} Budget"
        existing_budget_rn = (campaign_budget_resource_name or "").strip()
        if existing_budget_rn:
            budget_rn = existing_budget_rn
        else:
            budget_rn = f"customers/{fid}/campaignBudgets/-1"
        campaign_rn = f"customers/{fid}/campaigns/-2"
        results: Dict[str, Any] = {
            "status": "validated" if validate_only else "created",
            "customer_id": fid,
            "campaign_name": campaign_name,
            "operation_counts": {},
            "campaign_budget_resource_name": budget_rn,
        }

        operations: List[Dict[str, Any]] = []
        if not existing_budget_rn:
            operations.append(
                {
                    "campaignBudgetOperation": {
                        "create": {
                            "resourceName": budget_rn,
                            "name": budget_name,
                            "amountMicros": str(mh.currency_to_micros(daily_budget)),
                            "deliveryMethod": "STANDARD",
                            "explicitlyShared": False,
                        }
                    }
                }
            )
            results["operation_counts"]["campaign_budgets"] = 1
        else:
            results["operation_counts"]["campaign_budgets"] = 0

        operations.append(
            {
                "campaignOperation": {
                    "create": {
                        "resourceName": campaign_rn,
                        "name": campaign_name,
                        "status": "PAUSED",
                        "advertisingChannelType": "SEARCH",
                        "campaignBudget": budget_rn,
                        "networkSettings": {
                            "targetGoogleSearch": True,
                            "targetSearchNetwork": False,
                            "targetContentNetwork": False,
                            "targetPartnerSearchNetwork": False,
                        },
                        "geoTargetTypeSetting": {
                            "positiveGeoTargetType": "PRESENCE_OR_INTEREST",
                            "negativeGeoTargetType": "PRESENCE",
                        },
                        "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
                        "manualCpc": {},
                    }
                }
            }
        )
        results["operation_counts"]["campaigns"] = 1

        if geo_target_constant_ids:
            location_count = 0
            for gid in geo_target_constant_ids:
                geo_id = re.sub(r"\D", "", str(gid))
                if not geo_id:
                    continue
                operations.append(
                    {
                        "campaignCriterionOperation": {
                            "create": {
                                "campaign": campaign_rn,
                                "location": {"geoTargetConstant": f"geoTargetConstants/{geo_id}"},
                            }
                        }
                    }
                )
                location_count += 1
            results["operation_counts"]["location_targets"] = location_count

        keyword_count = 0
        rsa_count = 0
        for idx, ag in enumerate(ad_groups):
            ag_name = str(ag.get("name") or "").strip()
            if not ag_name:
                return "Each ad group requires name."
            ag_rn = f"customers/{fid}/adGroups/-{idx + 10}"
            operations.append(
                {
                    "adGroupOperation": {
                        "create": {
                            "resourceName": ag_rn,
                            "name": ag_name,
                            "campaign": campaign_rn,
                            "status": "PAUSED",
                            "type": "SEARCH_STANDARD",
                        }
                    }
                }
            )

            for kw in ag.get("keywords") or []:
                text = str(kw.get("text") if isinstance(kw, dict) else kw).strip()
                if text:
                    mt = str((kw.get("match_type") if isinstance(kw, dict) else "PHRASE") or "PHRASE")
                    operations.append({"adGroupCriterionOperation": _keyword_op(ag_rn, text, mt, "PAUSED")})
                    keyword_count += 1

            headlines = [str(h) for h in ag.get("headlines") or []]
            descriptions = [str(d) for d in ag.get("descriptions") or []]
            if headlines or descriptions:
                if not (3 <= len([h for h in headlines if h.strip()]) <= 15):
                    return f"Ad group {ag.get('name')} RSA requires 3 to 15 headlines."
                if not (2 <= len([d for d in descriptions if d.strip()]) <= 4):
                    return f"Ad group {ag.get('name')} RSA requires 2 to 4 descriptions."
                operations.append({"adGroupAdOperation": _rsa_op(ag_rn, final_url, headlines, descriptions, "PAUSED")})
                rsa_count += 1

        results["operation_counts"]["ad_groups"] = len(ad_groups)
        results["operation_counts"]["keywords"] = keyword_count
        results["operation_counts"]["rsas"] = rsa_count

        if negative_keywords:
            neg_ops, skipped, _resource = mh.build_negative_keyword_operations(
                customer_id,
                negative_keywords,
                "campaign",
                "2",
                None,
                "PHRASE",
                set(),
            )
            for op in neg_ops:
                op["create"]["campaign"] = campaign_rn
                operations.append({"campaignCriterionOperation": op})
            results["operation_counts"]["negative_keywords"] = len(neg_ops)
            results["skipped_negative_keywords"] = skipped

        data, err = mh.mutate_google_ads_operations(
            customer_id,
            operations,
            creds=creds,
            validate_only=validate_only,
            partial_failure=False,
        )
        if err:
            return f"Error creating paused search campaign build: {err}"

        resource_names = _extract_mutate_resource_names(data)
        results["operation_count"] = len(operations)
        results["resource_names"] = resource_names
        results["mutate"] = mh.format_mutate_results(data)

        return _json_out(results)
    except Exception as e:
        logger.exception("create_paused_search_campaign_build failed")
        return f"Error creating paused search campaign build: {e}"


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
            description=(
                "MAXIMIZE_CLICKS (or TARGET_SPEND), MANUAL_CPC, MAXIMIZE_CONVERSIONS, "
                "MAXIMIZE_CONVERSION_VALUE, TARGET_CPA, or TARGET_ROAS"
            )
        ),
    ],
    target_cpa: Annotated[
        Optional[float], Field(description="Target CPA in account currency (for TARGET_CPA / MAXIMIZE_CONVERSIONS)")
    ] = None,
    target_roas: Annotated[Optional[float], Field(description="Target ROAS (for TARGET_ROAS / MAXIMIZE_CONVERSION_VALUE)")] = None,
    cpc_bid_ceiling: Annotated[
        Optional[float],
        Field(description="Max CPC bid ceiling in account currency (for MAXIMIZE_CLICKS / TARGET_SPEND)"),
    ] = None,
) -> str:
    """Update campaign bidding strategy (Search campaigns; limited support for other channel types)."""
    try:
        strat = _normalize_bidding_strategy(strategy)
        if strat not in VALID_BIDDING_STRATEGIES and strategy.upper().replace(" ", "_") not in VALID_BIDDING_STRATEGIES:
            return f"strategy must be one of: {', '.join(sorted(VALID_BIDDING_STRATEGIES))}"

        camp, err = mh.resolve_campaign(customer_id, campaign_id=campaign_id)
        if err or not camp:
            return err or "Campaign not found."

        channel = (camp.get("advertising_channel_type") or "").upper()
        if channel and channel not in ("SEARCH", "SHOPPING", "PERFORMANCE_MAX", "UNKNOWN", ""):
            return f"Bidding updates for channel type {channel} may fail; test on a single campaign first."

        try:
            update_fields, mask = _bidding_update_fields(
                strategy,
                target_cpa=target_cpa,
                target_roas=target_roas,
                cpc_bid_ceiling=cpc_bid_ceiling,
            )
        except ValueError as exc:
            return str(exc)

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
                "cpc_bid_ceiling": cpc_bid_ceiling,
                "mutate": mh.format_mutate_results(data),
            }
        )
    except Exception as e:
        return f"Error updating bidding: {e}"


@mcp.tool()
async def update_ad_group_cpc_bid(
    customer_id: Annotated[str, Field(description="Google Ads customer ID")],
    ad_group_id: Annotated[str, Field(description="Ad group ID")],
    cpc_bid: Annotated[float, Field(description="Default CPC bid in account currency")],
) -> str:
    """Set ad group default CPC bid (useful after switching to MANUAL_CPC)."""
    try:
        ag, err = mh.resolve_ad_group(customer_id, ad_group_id=ad_group_id)
        if err or not ag:
            return err or "Ad group not found."

        amount_micros = str(mh.currency_to_micros(cpc_bid))
        op = {
            "updateMask": "cpcBidMicros",
            "update": {
                "resourceName": ag["resource_name"],
                "cpcBidMicros": amount_micros,
            },
        }
        creds = get_credentials()
        data, m_err = mh._mutate_raw(customer_id, "adGroups", [op], creds=creds)
        if m_err:
            return f"Error updating ad group CPC bid: {m_err}"

        return _json_out(
            {
                "status": "updated",
                "ad_group_id": ag["id"],
                "ad_group_name": ag["name"],
                "cpc_bid": cpc_bid,
                "mutate": mh.format_mutate_results(data),
            }
        )
    except Exception as e:
        return f"Error updating ad group CPC bid: {e}"


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

            if op.get("strategy") or op.get("target_cpa") is not None or op.get("cpc_bid_ceiling") is not None:
                strat = str(op.get("strategy") or "MAXIMIZE_CONVERSIONS")
                tcpa = op.get("target_cpa")
                troas = op.get("target_roas")
                cpc_ceiling = op.get("cpc_bid_ceiling")
                try:
                    update_fields, mask = _bidding_update_fields(
                        strat,
                        target_cpa=float(tcpa) if tcpa is not None else None,
                        target_roas=float(troas) if troas is not None else None,
                        cpc_bid_ceiling=float(cpc_ceiling) if cpc_ceiling is not None else None,
                    )
                    data, m_err = mh.mutate_campaign_update(
                        customer_id, cid, update_fields, mask, creds=creds
                    )
                    results.append(
                        {
                            "campaign_id": cid,
                            "action": "bidding",
                            "strategy": _normalize_bidding_strategy(strat),
                            "ok": not m_err,
                            "error": m_err,
                            "mutate": mh.format_mutate_results(data) if data else None,
                        }
                    )
                except ValueError as exc:
                    results.append(
                        {
                            "campaign_id": cid,
                            "action": "bidding",
                            "strategy": strat,
                            "ok": False,
                            "error": str(exc),
                            "mutate": None,
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
