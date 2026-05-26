# Keyword plan research (agent reference)

Use this playbook for **pre-campaign keyword discovery** with Google Ads **Keyword Planner** APIs (not GAQL). GAQL cannot invent new keywords or return Planner search volumes for arbitrary seeds; use the Keyword Plan tools instead.

**Prerequisites**

- `customer_id`: 10 digits, no dashes. Must be an account the authenticated user can access for **KeywordPlanIdeaService** / **KeywordPlanIdeaService:GenerateKeywordIdeas** (not only listable in a hierarchy view).
- **Developer token**: Keyword Planner methods require **Basic** or **Standard** access. Explorer (test) tokens return `DEVELOPER_TOKEN_NOT_APPROVED`.
- **MCC-linked clients**: set `GOOGLE_ADS_LOGIN_CUSTOMER_ID` to the **manager** customer ID when the API requires a login-customer header for child accounts. If `list_accounts` returns IDs but `generate_keyword_ideas` returns `CUSTOMER_NOT_FOUND`, verify the ID is the **operational** Google Ads customer for API calls and that login-customer is set when needed.
- **OAuth / access**: Standard access to the Google Ads account for the user or service account calling the API. `USER_PERMISSION_DENIED` means the credential cannot run Planner for that customer.

---

## Agent workflow (run in order)

1. **`list_accounts`** — pick a `customer_id` that you will use for Planner calls (prefer the leaf account you manage ads for).
2. **`suggest_geo_targets`** — resolve human names to `geoTargetConstants/…` (optional if you pass well-known names like `India` or `geoTargetConstants/2356`). The geo suggest endpoint is global; `customer_id` is still passed for formatting/cache context.
3. **`generate_keyword_ideas`** — discovery from **seed keywords**, a **page URL**, or a **site** domain; returns avg. monthly searches, competition, and top-of-page bid ranges.
4. **`get_keyword_metrics`** — refresh historical metrics for an **explicit shortlist** of keywords after human or agent review.
5. **(Post-launch)** **`run_gaql`** on `search_term_view` — optimize from **actual** queries once campaigns serve traffic.

---

## Default seeds (enterprise AV / India B2B example)

Use as `seed_keywords` or adapt to the client vertical:

- `microsoft teams room setup`
- `video conferencing solution enterprise`
- `digital signage mumbai`
- `yealink dealer maharashtra`

**Common geo shortcuts**

- India (country): `geoTargetConstants/2356` or geo name `India`.
- City/state: pass `Mumbai`, `Maharashtra`, etc., or resolve via `suggest_geo_targets` first.

**Language**

- Default targeting language in tools is often `English` → `languageConstants/1000`. Other names and IDs are supported by the implementation; see `keyword_plan_tools._LANGUAGE_NAME_TO_ID`.

**Network**

- `GOOGLE_SEARCH` (default) or `GOOGLE_SEARCH_AND_PARTNERS`.

---

## MCP tools (this repo)

| Step | Tool | Role |
|------|------|------|
| 1 | `list_accounts` | Discover accessible `customer_id` values |
| 2 | `suggest_geo_targets` | Map location strings → `geoTargetConstants/…` |
| 3 | `generate_keyword_ideas` | Ideas + volumes from seeds / URL / site |
| 4 | `get_keyword_metrics` | Metrics for a fixed keyword list |
| 5 | `run_gaql` | Live search terms and performance |

---

## Output formats

- **`format`: `table`** — human-readable default for chat.
- **`format`: `json`** or **`csv`** — pipelines and downstream agents (`generate_keyword_ideas`, `get_keyword_metrics`).

---

## Post-launch: search terms (GAQL)

After campaigns run, prefer `search_term_view` for real query behavior (example pattern):

```sql
SELECT
  search_term_view.search_term,
  metrics.impressions,
  metrics.clicks,
  metrics.cost_micros,
  metrics.conversions
FROM search_term_view
WHERE segments.date DURING LAST_30_DAYS
  AND metrics.impressions > 0
ORDER BY metrics.impressions DESC
LIMIT 50
```

---

## Further examples

Copy-paste **CLI** snippets (same behavior as the MCP layer) live in [`docs/keyword-plan-examples.md`](../../docs/keyword-plan-examples.md). Broader tool context: [`docs/mcp-tools-and-reports.md`](../../docs/mcp-tools-and-reports.md).

---

## Troubleshooting quick reference

| Symptom | Likely cause |
|--------|----------------|
| `DEVELOPER_TOKEN_NOT_APPROVED` | Explorer token; upgrade to Basic/Standard. |
| `USER_PERMISSION_DENIED` | OAuth user / SA lacks access to Planner for that customer. |
| `CUSTOMER_NOT_FOUND` on ideas/metrics but `list_accounts` showed the ID | Wrong operational customer, or missing `GOOGLE_ADS_LOGIN_CUSTOMER_ID` for MCC hierarchy. |
| Geo mismatch | Re-run `suggest_geo_targets` with `country_code` (e.g. `IN`) and pick the intended row (city vs postal code). |
