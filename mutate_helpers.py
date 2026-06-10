"""
Shared helpers for Google Ads API mutate (write) operations.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from google_ads_server import (
    API_VERSION,
    format_customer_id,
    get_credentials,
    make_api_request,
    _gaql_search_raw,
)

# REST mutate resource keys (path segment before :mutate)
MUTATE_RESOURCES = frozenset(
    {
        "campaigns",
        "campaignBudgets",
        "adGroups",
        "adGroupAds",
        "campaignCriteria",
        "adGroupCriteria",
    }
)


def currency_to_micros(amount: float) -> int:
    return int(round(float(amount) * 1_000_000))


def micros_to_currency(micros: Any) -> float:
    try:
        return int(micros) / 1_000_000
    except (TypeError, ValueError):
        return 0.0


def campaign_resource_name(customer_id: str, campaign_id: str) -> str:
    return f"customers/{format_customer_id(customer_id)}/campaigns/{str(campaign_id).strip()}"


def ad_group_resource_name(customer_id: str, ad_group_id: str) -> str:
    return f"customers/{format_customer_id(customer_id)}/adGroups/{str(ad_group_id).strip()}"


def ad_group_ad_resource_name(customer_id: str, ad_group_id: str, ad_id: str) -> str:
    return f"customers/{format_customer_id(customer_id)}/adGroupAds/{str(ad_group_id).strip()}~{str(ad_id).strip()}"


def campaign_budget_resource_name(customer_id: str, budget_id: str) -> str:
    return f"customers/{format_customer_id(customer_id)}/campaignBudgets/{str(budget_id).strip()}"


def _budget_id_from_resource_name(resource_name: str) -> str:
    return (resource_name or "").split("/")[-1]


def parse_mutate_error(error_text: str) -> str:
    if not error_text:
        return "Unknown mutate error"
    if "USER_PERMISSION_DENIED" in error_text:
        return (
            "USER_PERMISSION_DENIED: credentials lack edit access for this account. "
            "Ensure the user or service account has Standard access in Google Ads."
        )
    if "OPERATION_NOT_PERMITTED_FOR_CONTEXT" in error_text:
        return "OPERATION_NOT_PERMITTED_FOR_CONTEXT: this change is not allowed for this campaign type or state."
    if "MUTATE_NOT_ALLOWED" in error_text:
        return "MUTATE_NOT_ALLOWED: resource cannot be mutated in its current state."
    try:
        payload = json.loads(error_text)
        msg = payload.get("error", {}).get("message")
        details = payload.get("error", {}).get("details") or []
        parts: List[str] = []
        for detail in details:
            for err in detail.get("errors") or []:
                line = err.get("message") or ""
                code = err.get("errorCode") or {}
                for k, v in code.items():
                    if v:
                        line = f"{line} ({k}: {v})".strip()
                if line:
                    parts.append(line)
        if parts:
            return "; ".join(parts)
        if msg:
            return msg
    except json.JSONDecodeError:
        pass
    return error_text


def gaql_search(customer_id: str, query: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    fid = format_customer_id(customer_id)
    return _gaql_search_raw(fid, query)


def fetch_account_currency_code(customer_id: str, creds=None) -> str:
    fid = format_customer_id(customer_id)
    url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{fid}/googleAds:search"
    payload = {"query": "SELECT customer.currency_code FROM customer LIMIT 1"}
    data, error = make_api_request(url, method="POST", payload=payload, creds=creds)
    if error or not data:
        return "USD"
    results = data.get("results") or []
    if not results:
        return "USD"
    customer = results[0].get("customer") or {}
    return customer.get("currencyCode") or customer.get("currency_code") or "USD"


def _campaign_row(row: Dict[str, Any]) -> Dict[str, Any]:
    c = row.get("campaign") or {}
    return {
        "id": str(c.get("id", "")),
        "name": c.get("name") or "",
        "status": c.get("status") or "",
        "resource_name": c.get("resourceName") or c.get("resource_name") or "",
        "campaign_budget": c.get("campaignBudget") or c.get("campaign_budget") or "",
        "advertising_channel_type": c.get("advertisingChannelType") or c.get("advertising_channel_type") or "",
        "bidding_strategy_type": c.get("biddingStrategyType") or c.get("bidding_strategy_type") or "",
    }


def resolve_campaign(
    customer_id: str,
    campaign_id: Optional[str] = None,
    campaign_name: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if campaign_id:
        cid = re.sub(r"\D", "", str(campaign_id))
        q = f"""
            SELECT
              campaign.id,
              campaign.name,
              campaign.status,
              campaign.resource_name,
              campaign.campaign_budget,
              campaign.advertising_channel_type,
              campaign.bidding_strategy_type
            FROM campaign
            WHERE campaign.id = {cid}
            LIMIT 1
        """
    elif campaign_name:
        safe = campaign_name.replace("'", "\\'")
        q = f"""
            SELECT
              campaign.id,
              campaign.name,
              campaign.status,
              campaign.resource_name,
              campaign.campaign_budget,
              campaign.advertising_channel_type,
              campaign.bidding_strategy_type
            FROM campaign
            WHERE campaign.name = '{safe}'
            LIMIT 5
        """
    else:
        return None, "Provide campaign_id or campaign_name."

    rows, err = gaql_search(customer_id, q)
    if err:
        return None, err
    if not rows:
        return None, "Campaign not found."
    if not campaign_id and len(rows) > 1:
        names = [(_campaign_row(r).get("name") or "") for r in rows]
        return None, f"Multiple campaigns match name '{campaign_name}': {names}. Use campaign_id."
    return _campaign_row(rows[0]), None


def resolve_ad_group(
    customer_id: str,
    ad_group_id: Optional[str] = None,
    ad_group_name: Optional[str] = None,
    campaign_id: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if ad_group_id:
        agid = re.sub(r"\D", "", str(ad_group_id))
        where = f"ad_group.id = {agid}"
    elif ad_group_name:
        safe = ad_group_name.replace("'", "\\'")
        where = f"ad_group.name = '{safe}'"
        if campaign_id:
            cid_digits = re.sub(r"\D", "", str(campaign_id))
            where += f" AND campaign.id = {cid_digits}"
    else:
        return None, "Provide ad_group_id or ad_group_name."

    q = f"""
        SELECT
          ad_group.id,
          ad_group.name,
          ad_group.status,
          ad_group.resource_name,
          campaign.id,
          campaign.name
        FROM ad_group
        WHERE {where}
        LIMIT 5
    """
    rows, err = gaql_search(customer_id, q)
    if err:
        return None, err
    if not rows:
        return None, "Ad group not found."
    if not ad_group_id and len(rows) > 1:
        names = [str((r.get("adGroup") or r.get("ad_group") or {}).get("name", "")) for r in rows]
        return None, f"Multiple ad groups match: {names}. Use ad_group_id or campaign_id."
    ag = rows[0].get("adGroup") or rows[0].get("ad_group") or {}
    camp = rows[0].get("campaign") or {}
    return {
        "id": str(ag.get("id", "")),
        "name": ag.get("name") or "",
        "status": ag.get("status") or "",
        "resource_name": ag.get("resourceName") or ag.get("resource_name") or "",
        "campaign_id": str(camp.get("id", "")),
        "campaign_name": camp.get("name") or "",
    }, None


def fetch_campaign_settings_rows(
    customer_id: str,
    campaign_id: Optional[str] = None,
    campaign_name: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    fid = format_customer_id(customer_id)
    if campaign_id:
        cid = re.sub(r"\D", "", str(campaign_id))
        where = f"campaign.id = {cid}"
    elif campaign_name:
        safe = campaign_name.replace("'", "\\'")
        where = f"campaign.name = '{safe}'"
    else:
        where = "campaign.status != 'REMOVED'"

    q = f"""
        SELECT
          campaign.id,
          campaign.name,
          campaign.status,
          campaign.resource_name,
          campaign.campaign_budget,
          campaign.advertising_channel_type,
          campaign.bidding_strategy_type,
          campaign.maximize_conversions.target_cpa_micros,
          campaign.maximize_conversion_value.target_roas,
          campaign.target_cpa.target_cpa_micros,
          campaign.target_roas.target_roas,
          campaign_budget.id,
          campaign_budget.name,
          campaign_budget.amount_micros,
          campaign_budget.explicitly_shared,
          campaign_budget.reference_count
        FROM campaign
        WHERE {where}
        ORDER BY campaign.name
        LIMIT 100
    """
    rows, err = gaql_search(fid, q)
    if err:
        return [], err

    out: List[Dict[str, Any]] = []
    for row in rows:
        c = row.get("campaign") or {}
        b = row.get("campaignBudget") or row.get("campaign_budget") or {}
        out.append(
            {
                "campaign_id": str(c.get("id", "")),
                "campaign_name": c.get("name") or "",
                "status": c.get("status") or "",
                "resource_name": c.get("resourceName") or c.get("resource_name") or "",
                "channel_type": c.get("advertisingChannelType") or c.get("advertising_channel_type") or "",
                "bidding_strategy_type": c.get("biddingStrategyType") or c.get("bidding_strategy_type") or "",
                "budget_resource_name": c.get("campaignBudget") or c.get("campaign_budget") or "",
                "budget_id": str(b.get("id", "")),
                "budget_name": b.get("name") or "",
                "daily_budget_micros": b.get("amountMicros") or b.get("amount_micros"),
                "daily_budget": micros_to_currency(b.get("amountMicros") or b.get("amount_micros")),
                "budget_explicitly_shared": b.get("explicitlyShared") or b.get("explicitly_shared"),
                "budget_reference_count": b.get("referenceCount") or b.get("reference_count"),
                "maximize_conversions_target_cpa_micros": (
                    (c.get("maximizeConversions") or {}).get("targetCpaMicros")
                    if isinstance(c.get("maximizeConversions"), dict)
                    else c.get("maximizeConversions")
                ),
                "target_cpa_micros": (
                    (c.get("targetCpa") or {}).get("targetCpaMicros")
                    if isinstance(c.get("targetCpa"), dict)
                    else None
                ),
                "target_roas": (
                    (c.get("targetRoas") or {}).get("targetRoas")
                    if isinstance(c.get("targetRoas"), dict)
                    else None
                ),
            }
        )
    return out, None


def fetch_campaign_budgets(customer_id: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    q = """
        SELECT
          campaign_budget.id,
          campaign_budget.name,
          campaign_budget.resource_name,
          campaign_budget.amount_micros,
          campaign_budget.explicitly_shared,
          campaign_budget.reference_count,
          campaign_budget.status
        FROM campaign_budget
        WHERE campaign_budget.status != 'REMOVED'
        ORDER BY campaign_budget.amount_micros DESC
        LIMIT 200
    """
    rows, err = gaql_search(customer_id, q)
    if err:
        return [], err
    out = []
    for row in rows:
        b = row.get("campaignBudget") or row.get("campaign_budget") or {}
        micros = b.get("amountMicros") or b.get("amount_micros")
        out.append(
            {
                "budget_id": str(b.get("id", "")),
                "name": b.get("name") or "",
                "resource_name": b.get("resourceName") or b.get("resource_name") or "",
                "amount_micros": micros,
                "daily_budget": micros_to_currency(micros),
                "explicitly_shared": b.get("explicitlyShared") or b.get("explicitly_shared"),
                "reference_count": b.get("referenceCount") or b.get("reference_count"),
                "status": b.get("status") or "",
            }
        )
    return out, None


def count_campaigns_on_budget(customer_id: str, budget_resource_name: str) -> int:
    bid = _budget_id_from_resource_name(budget_resource_name)
    if not bid:
        return 0
    q = f"""
        SELECT campaign.id
        FROM campaign
        WHERE campaign.campaign_budget = 'customers/{format_customer_id(customer_id)}/campaignBudgets/{bid}'
          AND campaign.status != 'REMOVED'
        LIMIT 100
    """
    rows, err = gaql_search(customer_id, q)
    if err or not rows:
        return 0
    return len(rows)


def _mutate_raw(
    customer_id: str,
    resource: str,
    operations: List[Dict[str, Any]],
    creds=None,
    response_content_type: str = "MUTABLE_RESOURCE",
    validate_only: bool = False,
    partial_failure: bool = False,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if resource not in MUTATE_RESOURCES:
        return None, f"Unsupported mutate resource: {resource}"
    if not operations:
        return None, "No mutate operations provided."

    fid = format_customer_id(customer_id)
    url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{fid}/{resource}:mutate"
    payload: Dict[str, Any] = {
        "operations": operations,
        "responseContentType": response_content_type,
        "validateOnly": bool(validate_only),
        "partialFailure": bool(partial_failure),
    }
    data, error = make_api_request(url, method="POST", payload=payload, creds=creds)
    if error:
        return None, parse_mutate_error(error)
    return data, None


def mutate_google_ads_operations(
    customer_id: str,
    operations: List[Dict[str, Any]],
    creds=None,
    validate_only: bool = False,
    partial_failure: bool = False,
    response_content_type: str = "MUTABLE_RESOURCE",
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Run GoogleAdsService.Mutate for cross-resource operations with temporary IDs."""
    if not operations:
        return None, "No mutate operations provided."
    fid = format_customer_id(customer_id)
    url = f"https://googleads.googleapis.com/{API_VERSION}/customers/{fid}/googleAds:mutate"
    payload: Dict[str, Any] = {
        "mutateOperations": operations,
        "validateOnly": bool(validate_only),
        "partialFailure": bool(partial_failure),
        "responseContentType": response_content_type,
    }
    data, error = make_api_request(url, method="POST", payload=payload, creds=creds)
    if error:
        return None, parse_mutate_error(error)
    return data, None


def mutate_campaign_update(
    customer_id: str,
    campaign_id: str,
    update_fields: Dict[str, Any],
    update_mask: List[str],
    creds=None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    rn = campaign_resource_name(customer_id, campaign_id)
    body = {"resourceName": rn, **update_fields}
    op = {"updateMask": ",".join(update_mask), "update": body}
    return _mutate_raw(customer_id, "campaigns", [op], creds=creds)


def mutate_campaign_budget_amount(
    customer_id: str,
    budget_resource_name: str,
    amount_micros: int,
    creds=None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """Returns (data, error, warning)."""
    warning = None
    ref_count = count_campaigns_on_budget(customer_id, budget_resource_name)
    if ref_count > 1:
        warning = (
            f"Budget is linked to {ref_count} campaigns; changing amount affects all of them."
        )
    op = {
        "updateMask": "amountMicros",
        "update": {
            "resourceName": budget_resource_name,
            "amountMicros": str(amount_micros),
        },
    }
    data, err = _mutate_raw(customer_id, "campaignBudgets", [op], creds=creds)
    return data, err, warning


def mutate_ad_group_status(
    customer_id: str,
    ad_group_id: str,
    status: str,
    creds=None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    rn = ad_group_resource_name(customer_id, ad_group_id)
    op = {
        "updateMask": "status",
        "update": {"resourceName": rn, "status": status.upper()},
    }
    return _mutate_raw(customer_id, "adGroups", [op], creds=creds)


def _digits_only(value: str) -> str:
    return re.sub(r"\D", "", str(value))


def existing_negative_keywords_campaign(
    customer_id: str, campaign_id: str
) -> Tuple[set[str], Optional[str]]:
    cid = _digits_only(campaign_id)
    q = f"""
        SELECT campaign_criterion.keyword.text, campaign_criterion.keyword.match_type
        FROM campaign_criterion
        WHERE campaign.id = {cid}
          AND campaign_criterion.type = 'KEYWORD'
          AND campaign_criterion.negative = TRUE
        LIMIT 10000
    """
    rows, err = gaql_search(customer_id, q)
    if err:
        return set(), err
    keys = set()
    for row in rows:
        cc = row.get("campaignCriterion") or row.get("campaign_criterion") or {}
        kw = cc.get("keyword") or {}
        text = (kw.get("text") or "").strip().lower()
        mt = kw.get("matchType") or kw.get("match_type") or ""
        if text:
            keys.add(f"{text}|{mt}")
    return keys, None


def existing_negative_keywords_ad_group(
    customer_id: str, ad_group_id: str
) -> Tuple[set[str], Optional[str]]:
    agid = _digits_only(ad_group_id)
    q = f"""
        SELECT ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type
        FROM ad_group_criterion
        WHERE ad_group.id = {agid}
          AND ad_group_criterion.type = 'KEYWORD'
          AND ad_group_criterion.negative = TRUE
        LIMIT 10000
    """
    rows, err = gaql_search(customer_id, q)
    if err:
        return set(), err
    keys = set()
    for row in rows:
        agc = row.get("adGroupCriterion") or row.get("ad_group_criterion") or {}
        kw = agc.get("keyword") or {}
        text = (kw.get("text") or "").strip().lower()
        mt = kw.get("matchType") or kw.get("match_type") or ""
        if text:
            keys.add(f"{text}|{mt}")
    return keys, None


def build_negative_keyword_operations(
    customer_id: str,
    keywords: List[str],
    level: str,
    campaign_id: str,
    ad_group_id: Optional[str],
    match_type: str,
    existing: set[str],
) -> Tuple[List[Dict[str, Any]], List[str], str]:
    resource = "campaignCriteria" if level == "campaign" else "adGroupCriteria"
    mt = match_type.upper()
    if mt not in ("EXACT", "PHRASE", "BROAD"):
        mt = "PHRASE"
    ops: List[Dict[str, Any]] = []
    skipped: List[str] = []
    parent_rn = (
        campaign_resource_name(customer_id, campaign_id)
        if level == "campaign"
        else ad_group_resource_name(customer_id, ad_group_id or "")
    )
    for kw in keywords:
        text = (kw or "").strip()
        if not text:
            continue
        key = f"{text.lower()}|{mt}"
        if key in existing:
            skipped.append(text)
            continue
        if level == "campaign":
            ops.append(
                {
                    "create": {
                        "campaign": parent_rn,
                        "negative": True,
                        "keyword": {"text": text, "matchType": mt},
                    }
                }
            )
        else:
            ops.append(
                {
                    "create": {
                        "adGroup": parent_rn,
                        "negative": True,
                        "keyword": {"text": text, "matchType": mt},
                    }
                }
            )
        existing.add(key)
    return ops, skipped, resource


def fetch_waste_search_terms(
    customer_id: str,
    campaign_id: str,
    days: int,
    min_cost_micros: int,
    max_conversions: float,
    limit: int = 50,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    cid = _digits_only(campaign_id)
    q = f"""
        SELECT
          search_term_view.search_term,
          metrics.cost_micros,
          metrics.clicks,
          metrics.conversions
        FROM search_term_view
        WHERE segments.date DURING LAST_{int(days)}_DAYS
          AND campaign.id = {cid}
          AND metrics.cost_micros >= {int(min_cost_micros)}
          AND metrics.conversions <= {float(max_conversions)}
        ORDER BY metrics.cost_micros DESC
        LIMIT {int(limit)}
    """
    rows, err = gaql_search(customer_id, q)
    if err:
        return [], err
    out = []
    for row in rows:
        st = row.get("searchTermView") or row.get("search_term_view") or {}
        m = row.get("metrics") or {}
        term = st.get("searchTerm") or st.get("search_term") or ""
        if term:
            out.append(
                {
                    "search_term": term,
                    "cost_micros": int(m.get("costMicros") or m.get("cost_micros") or 0),
                    "clicks": int(m.get("clicks") or 0),
                    "conversions": float(m.get("conversions") or 0),
                }
            )
    return out, None


def format_mutate_results(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not data:
        return {"results": []}
    results = data.get("results") or []
    return {"results": results, "partial_failure_error": data.get("partialFailureError")}
