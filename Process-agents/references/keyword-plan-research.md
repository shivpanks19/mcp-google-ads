# Agent: Keyword plan research (Keyword Planner)

**Goal:** Produce pre-launch or expansion keyword intelligence using **Google Ads Keyword Planner** APIs—not GAQL. GAQL cannot invent seed volumes or return Planner forecasts for arbitrary new phrases.

**Outputs:** A dated Markdown brief under `reports/keyword-plan/` named `{customer_id}-keyword-plan-{YYYY-MM-DD}.md` (or append to an existing client dossier if your org uses a single file per account).

---

## Prerequisites

| Requirement | Notes |
|-------------|--------|
| **Developer token** | Must be **Basic** or **Standard**. Explorer tokens return `DEVELOPER_TOKEN_NOT_APPROVED` on `generateKeywordIdeas`. |
| **Auth** | On headless servers: `GOOGLE_ADS_AUTH_TYPE=service_account` and `GOOGLE_ADS_CREDENTIALS_JSON` (single-line JSON). The principal must have access to the target Google Ads account. |
| **`customer_id`** | 10 digits, no dashes. Resolve with `list_accounts` (MCP `list_accounts` on `my-server` or equivalent). |
| **Optional MCC** | Set `GOOGLE_ADS_LOGIN_CUSTOMER_ID` when the account is under an MCC and the API requires it. |

Repo implementation: `keyword_plan_tools.py` (see also `docs/keyword-plan-examples.md` and the “Recommended keyword research workflow” section in `docs/mcp-tools-and-reports.md`).

---

## Procedure (run in order)

### 1) Resolve account

- Call **`list_accounts`** → pick **`customer_id`**.
- Optionally call **`get_account_currency`** so CPC bands in the narrative match the account currency.

### 2) Resolve geography

- Call **`suggest_geo_targets`** with human-readable names (e.g. `India`, `Mumbai`, `Maharashtra`) **or** pass known resource names such as `geoTargetConstants/2356` (India).
- Record the resolved `geoTargetConstants/*` values used in steps 3–4.

### 3) Generate keyword ideas

- Call **`generate_keyword_ideas`** with **at least one** of: `seed_keywords`, `page_url`, or `site_url`.
- Typical defaults for this repo’s B2B / India examples: `language="English"`, `geo_targets` including India or state-level targets, `network="GOOGLE_SEARCH"`, `limit` 30–50 for a first pass.
- Prefer **`format="json"`** when feeding downstream tooling; use table output for human review.

### 4) Shortlist and refresh metrics

- Curate a list of **exact** strings to evaluate (remove duplicates, fix spelling, drop irrelevant intents).
- Call **`get_keyword_metrics`** on that list with the **same** geo + language as step 3.
- Use `format="table"` in notes or `format="json"` for structured storage.

### 5) (Post-launch only) Validate with live queries

- Run GAQL on **`search_term_view`** (e.g. via `run_gaql` / `execute_gaql_query`) to align Planner estimates with **actual** queries and negatives.

---

## Report template (Markdown)

The brief should include:

1. **Metadata:** date, `customer_id`, currency, geo + language used.  
2. **Seeds:** URL/site seed if used; else verbatim seed keywords.  
3. **Ideas table or top N:** keyword, avg monthly searches, competition, low/high CPC (account currency).  
4. **Shortlist + refreshed metrics:** same columns for the chosen build list.  
5. **Notes:** brand vs non-brand, intent (informational / transactional), obvious negatives to validate in Ads.  
6. **Limitations:** Planner is directional; treat as research, not ground truth for auction outcomes.

---

## Failure handling

| Symptom | Action |
|---------|--------|
| `USER_PERMISSION_DENIED` | Fix Ads account access for the OAuth user or service account. |
| `DEVELOPER_TOKEN_NOT_APPROVED` | Upgrade developer token or use a test account with an approved token. |
| Empty ideas | Broaden seeds, add `page_url` / `site_url`, or widen geo; verify language constant. |
| No credentials in environment | Do **not** fabricate volumes. Write the run log with **blocked** status and list required env vars (see Prerequisites). |

---

## One-shot CLI (repo root)

Replace `YOUR_CUSTOMER_ID` and paths with real values; requires `.venv` and `pip install -r requirements.txt`.

```bash
cd /workspace && .venv/bin/python <<'PY'
import asyncio
from keyword_plan_tools import suggest_geo_targets, generate_keyword_ideas, get_keyword_metrics

CID = "YOUR_CUSTOMER_ID"

async def main():
    print(await suggest_geo_targets(CID, ["India", "Maharashtra"], country_code="IN"))
    ideas = await generate_keyword_ideas(
        customer_id=CID,
        seed_keywords=[
            "microsoft teams room setup",
            "video conferencing solution enterprise",
            "interactive flat panel classroom",
        ],
        geo_targets=["India"],
        language="English",
        limit=40,
        format="json",
    )
    print(ideas)
    # Parse JSON in a real pipeline; example static shortlist:
    metrics = await get_keyword_metrics(
        customer_id=CID,
        keywords=[
            "interactive flat panel",
            "smart board for classroom",
        ],
        geo_targets=["India"],
        language="English",
        format="table",
    )
    print(metrics)

asyncio.run(main())
PY
```

---

## Maintenance

- Keep this file aligned with `docs/keyword-plan-examples.md` when new seed patterns or MCP tool names change.  
- Cron or automation triggers should **commit** generated reports on success, or a **single** `reports/keyword-plan/latest-run.md` stub when credentials are missing (avoid silent no-ops).
