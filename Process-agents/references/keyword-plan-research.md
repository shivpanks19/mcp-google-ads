# Keyword plan research agent

Run this workflow when you need **Keyword Planner–style discovery** (ideas, volume, competition, top-of-page bids) before or alongside live campaigns. Canonical CLI examples live in [`docs/keyword-plan-examples.md`](../../docs/keyword-plan-examples.md).

## Prerequisites

- Google Ads **customer_id** (10 digits, no dashes). Use MCP **`list_accounts`** first; prefer a **non-manager** account for Planner calls unless your setup uses the MCC as the planning customer.
- **Developer token** must be **Basic** or **Standard**. Explorer tokens return `DEVELOPER_TOKEN_NOT_APPROVED` on Planner methods.
- Optional: `GOOGLE_ADS_LOGIN_CUSTOMER_ID` for MCC-linked clients. If Planner returns `CUSTOMER_NOT_FOUND` while **GAQL works** for the same customer, the login-customer-id may not match that account; the server’s `make_api_request` retries the same POST **without** `login-customer-id` when Google returns `CUSTOMER_NOT_FOUND` (see `google_ads_server.py`).

## Steps (MCP)

1. **`list_accounts`** — pick `customer_id`.
2. **`suggest_geo_targets`** — resolve names (e.g. India, Mumbai, Maharashtra) to `geoTargetConstants/…` if you need exact IDs.
3. **`generate_keyword_ideas`** — pass `seed_keywords` and/or `page_url` / `site_url`, plus `geo_targets`, `language`, `network`, `limit`.
4. **`get_keyword_metrics`** — refresh historical metrics for an explicit shortlist (max 1000 keywords).
5. **Post-launch:** **`run_gaql`** on `search_term_view` (see examples in `docs/keyword-plan-examples.md`) for real query performance.

## Default seeds (India B2B / enterprise AV example)

Use as placeholders; replace with the client’s products and cities.

- `microsoft teams room setup`
- `video conferencing solution enterprise`
- `digital signage mumbai`
- `yealink dealer maharashtra`

## Output

- Prefer **`format: json`** for pipelines; **`table`** for human review.
- Shortlist ideas by intent, geo fit, and CPC band before expanding match types in Search builds.

## Safety

- Planner calls are **read-only** but consume API quota; keep **`limit`** reasonable on cron or batched jobs.
- Do not auto-apply negatives or keywords from research without human review of intent and brand rules.
