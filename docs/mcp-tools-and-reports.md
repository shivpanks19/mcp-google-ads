# MCP tools, queries, and reporting

This document describes what the **Google Ads MCP** exposes: there is no separate “saved query library” inside Google Ads—the server offers **tools**, **prompts**, and a **resource**. Reporting is either **built into specific tools**, **custom GAQL** you supply, or **optional Supabase snapshots** you save after analysis.

## “Saved” queries

- **README / `docs/great-gaql-samples.md`** contain **example GAQL** for copy-paste into `run_gaql` or `execute_gaql_query`. They are documentation only, not pre-executed or stored on the server.
- **Prompts** (`google_ads_workflow`, `gaql_help`) give templated guidance, not live account data.
- **Resource** `gaql://reference` exposes GAQL reference text for agents.

## Google Ads tools (live API)

| Tool | Purpose |
|------|---------|
| `list_accounts` | List Google Ads accounts the credentials can access. **Start here** to get a `customer_id`. |
| `get_account_currency` | Account currency context. |
| `get_campaign_performance` | **Report-style:** campaign metrics over `LAST_N_DAYS`. Optional **`persist_snapshot`** (or env **`AUTO_PERSIST_CAMPAIGN_PERFORMANCE_SNAPSHOTS=1`**) writes **`report_snapshots`** with `report_type` like `campaign_performance_last_7d` / `campaign_performance_last_30d` (rolling windows, not calendar ISO weeks). |
| `get_ad_performance` | **Report-style:** ad-level performance over a date range. |
| `get_ad_creatives` | Creative / ad copy oriented data. |
| `execute_gaql_query` | Run **your** GAQL string against an account. |
| `run_gaql` | Same idea with **table / JSON / CSV** output formatting. |
| `get_image_assets` | List image assets (URLs, metadata). |
| `download_image_asset` | Download a specific image asset. |
| `get_asset_usage` | Where assets are used in the account. |
| `analyze_image_assets` | Image-focused performance-style analysis over a period. |
| `list_resources` | Discover valid GAQL `FROM` resources (not a performance report). |
| `generate_keyword_ideas` | **Keyword Planner:** discover ideas from seed keywords, a page URL, or a site; returns volume, competition, CPC ranges. |
| `get_keyword_metrics` | **Keyword Planner:** historical metrics for an explicit keyword list (search volume, competition, bids). |
| `suggest_geo_targets` | Resolve location names (e.g. India, Mumbai) to `geoTargetConstants/…` resource names. |
| `update_search_campaign` | **Mutate (write):** sparse update of an existing **SEARCH** campaign via REST `campaigns:mutate` — **`status`** (ENABLED/PAUSED) and/or **`name`**. Pre-checks channel type with GAQL. Supports **`validate_only`**. |
| `update_search_campaign_budget_micros` | **Mutate (write):** set the linked **CampaignBudget** daily **`amountMicros`** for a **SEARCH** campaign via REST `campaignBudgets:mutate`. Same guards as `update_search_campaign`. |

### Search campaign mutates (safety switches)

- **`GOOGLE_ADS_DISABLE_MUTATIONS=1`** — all mutate tools return an error (read-only / staging).
- **`GOOGLE_ADS_MUTATE_VALIDATE_ONLY=1`** — force **`validate_only`** on every mutate (dry-run only).

These use the same auth headers as GAQL (`developer-token`, bearer, optional `login-customer-id` retry).

## Campaign edit tools (write / mutate API)

These tools **apply changes immediately** in Google Ads unless the tool is called with `validate_only=true` or env `GOOGLE_ADS_MUTATE_VALIDATE_ONLY=1` is set. Credentials need **edit** access (Standard user on the account, or service account invited with edit). Search campaigns are the primary target; PMax / Demand Gen bidding updates may return clear errors for unsupported fields.

Registered from `campaign_edit_tools.py` and `optimization_actions.py` (imported by `google_ads_server.py`).

### Read-before-write

| Tool | Purpose |
|------|---------|
| `get_campaign_settings` | Campaign name, status, budget, bidding strategy, target CPA/ROAS; resolve by `campaign_id` or fuzzy `campaign_name`. |
| `list_campaign_budgets` | Budget resource IDs and amounts (for reallocation / shared-budget checks). |

### Search launch creation

| Tool | Purpose |
|------|---------|
| `create_campaign_budget` | Create a campaign budget from a daily account-currency amount. Supports `validate_only`. |
| `create_search_campaign` | Create a paused Search campaign linked to an existing campaign budget. Defaults to Google Search only, search partners off, presence geo targeting, and Max Clicks bidding. Supports `validate_only`. |
| `create_campaign_location_targets` | Add campaign-level geo targets by `geoTargetConstants` ID, e.g. India `2356`. Supports `validate_only`. |
| `create_ad_groups` | Create paused Search ad groups under a campaign. Supports optional CPC bid and `validate_only`. |
| `create_keywords` | Create paused positive ad group keywords from `{text, match_type}` rows. Supports `validate_only`. |
| `create_responsive_search_ad` | Create one paused RSA in an ad group from final URL, 3-15 headlines, and 2-4 descriptions. Supports `validate_only`. |
| `create_paused_search_campaign_build` | End-to-end launch helper: budget → paused Search campaign → optional locations → ad groups → keywords → RSAs → campaign negatives. Supports `validate_only`. |

### Campaign-level edits

| Tool | Purpose |
|------|---------|
| `update_campaign_status` | Enable or pause a campaign (`ENABLED` \| `PAUSED`). |
| `update_campaign_budget` | Set daily budget (INR float → account currency micros). Warns if budget is shared across campaigns. |
| `update_campaign_bidding` | Change bidding strategy (`MAXIMIZE_CONVERSIONS`, `TARGET_CPA`, `TARGET_ROAS`, `MANUAL_CPC`, etc.) with optional targets. |
| `rename_campaign` | Rename a campaign. |

### Ad group & negatives

| Tool | Purpose |
|------|---------|
| `update_ad_group_status` | Enable or pause an ad group (by id or name + optional `campaign_id`). |
| `add_negative_keywords` | Add campaign- or ad-group-level negatives; dedupes existing criteria. |
| `add_campaign_negative_keywords_from_search_terms` | Pull waste queries from `search_term_view` and add as campaign negatives. |

### Bulk & orchestration

| Tool | Purpose |
|------|---------|
| `bulk_update_campaigns` | Batch status / budget / target CPA updates for multiple campaigns. |
| `apply_weekly_performance_actions` | **Flagship PM workflow:** classify campaigns (pause waste, reduce/increase budget, optional search-term negatives) and **apply directly**; optional Supabase `save_memory` audit when configured. |
| `analyze_and_apply_campaign_edits` | Apply an explicit action list after human/agent review of analysis output. |

Copy-paste CLI examples: [`campaign-edit-examples.md`](campaign-edit-examples.md).

## Recommended “Analyze → Apply” workflow

1. `list_accounts` → pick `customer_id`.
2. `get_campaign_performance(customer_id, days=7)` — baseline metrics.
3. `analyze_running_campaigns_and_ads(customer_id, campaign_days=7, ad_days=7)` — Markdown brief with heuristic next steps.
4. **`get_campaign_settings`** on campaigns you plan to change (confirm budget, bidding, shared budget).
5. Either:
   - **`apply_weekly_performance_actions`** with thresholds (`min_spend_inr`, `pause_zero_conversion_spenders`, etc.), or
   - Surgical edits: **`update_campaign_status`**, **`update_campaign_budget`**, **`add_negative_keywords`**, **`add_campaign_negative_keywords_from_search_terms`**.
6. Optional: **`save_analysis_text_snapshot`** (narrative) and rely on auto **`save_memory`** from weekly apply when Supabase is configured.

**Important:** Mutations are live. Test on a non-production account or use conservative thresholds first.

## Recommended “give me a report” workflow

1. `list_accounts` → pick `customer_id`.
2. `get_account_currency` if currency matters for interpretation.
3. **`get_campaign_performance`** and/or **`get_ad_performance`** for standard campaign/ad slices.
4. **`run_gaql`** (or `execute_gaql_query`) for anything custom (search terms, labels, segments, etc.).
5. Iterate with tighter `WHERE` / `ORDER BY` as needed.

For GAQL syntax and field rules, see [`gaql-google-ads-query-language.md`](gaql-google-ads-query-language.md) and samples in [`great-gaql-samples.md`](great-gaql-samples.md).

## Recommended keyword research workflow (Keyword Planner)

GAQL cannot generate new keyword ideas or return Planner search volumes for seeds. Use the Keyword Plan tools instead:

1. **`list_accounts`** → pick `customer_id`.
2. **`suggest_geo_targets`** → confirm `geoTargetConstants/2356` (India) or city-level targets (e.g. Mumbai).
3. **`generate_keyword_ideas`** with seed keywords + geo + language (default India + English).
4. **`get_keyword_metrics`** to refresh volumes on a shortlisted keyword list.
5. **(Post-launch)** **`run_gaql`** on `search_term_view` for live query optimization.

Copy-paste CLI examples: [`keyword-plan-examples.md`](keyword-plan-examples.md).

## Recommended Search launch workflow

1. Prepare campaign/ad group/keyword/RSA spec locally.
2. Run `create_paused_search_campaign_build(..., validate_only=true)` to validate the full build without applying changes.
3. Fix any policy, field, or resource errors returned by Google Ads.
4. Run the same tool with `validate_only=false` to create everything paused.
5. Run `get_campaign_settings` and `run_gaql` to confirm campaign, ad groups, keywords, ads, budget, network, and location state before enabling.

## Optional Supabase: memory and report snapshots

When `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are set (see `.env.example` and migrations under [`supabase/migrations/`](../supabase/migrations/)), these tools are registered from `memory_tools.py`:

| Tool | Purpose |
|------|---------|
| `save_client_context` | Upsert client profile (name, aliases, notes, metadata). |
| `recall_client_context` | Load profile + recent memory by `customer_id` or fuzzy name. |
| `save_memory` | Store insights, decisions, audit notes. |
| `search_memory` | Search memory with filters. |
| `save_report_snapshot` | **Persist** metrics JSON + optional summary for **period-over-period** comparison. |
| `list_report_snapshots` | List saved snapshots for a client. |
| `save_analysis_text_snapshot` | Save a **full narrative** analysis (Markdown/plain text) with `analysis_type` (e.g. `campaign_performance_analysis`) and server `created_at`. Table: **`analysis_text_snapshots`** (requires migration **`002_analysis_text_snapshots.sql`**). |
| `list_analysis_text_snapshots` | List narrative analyses for a client, newest first; filter by `analysis_type` and date. |
| `sync_list_accounts_to_supabase` | Runs **`list_accounts`**, parses IDs, **upserts** each into `google_ads_clients` (metadata: `list_accounts_synced_at`). Seeds clients before memory/snapshots. |

They do **not** run arbitrary Google Ads queries by themselves—except **`sync_list_accounts_to_supabase`**, which calls the Ads API (`list_accounts`) then writes to Supabase. Other tools store or recall what you (or GAQL tools) already produced.

### Cross-source analysis (`analysis_tools.py`)

When the server loads, **`analysis_tools.py`** registers tools that **combine** Supabase history with **live** Google Ads pulls (same GAQL slices as `get_campaign_performance` / `get_ad_performance`: top **50** campaigns by cost, top **50** ads by impressions, over `LAST_N_DAYS`).

| Tool | Purpose |
|------|---------|
| `get_account_analysis_context` | Returns **JSON** of recent **`analysis_text_snapshots`** and **`report_snapshots`** for the account (Supabase only; no Ads API). Skips with a message if Supabase is not configured. |
| `analyze_running_campaigns_and_ads` | Returns a **Markdown** brief: optional pasted Supabase context + **live** campaign/ad aggregates, tables, and **rule-based** next steps. Does **not** auto-persist; use **`save_analysis_text_snapshot`** to store the narrative. |
| `analyze_ad_copy` | Returns a **Markdown** RSA **headline/description** brief: performance labels (BEST/GOOD/LOW), top assets by impressions, duplicate copy, over-limit character checks, and recommendations. Uses `ad_group_ad_asset_view` with fallback to `asset_performance_label_view`. Persist with **`save_analysis_text_snapshot`** (`analysis_type`: `ad_copy_analysis`). |

**Prerequisites:** same as memory tools (migrations `001` + `002` for narrative rows). The account should exist in **`google_ads_clients`** (e.g. after **`sync_list_accounts_to_supabase`**) so list queries resolve `client_id`; if missing, the Supabase sections will show “no rows” rather than failing the live API sections.

**Campaign snapshots:** `get_campaign_performance(..., persist_snapshot=True)` or **`scripts/sync_campaign_performance_to_supabase.py --persist`** insert into `report_snapshots` (same table as `save_report_snapshot`). Filter with `list_report_snapshots(..., report_type="campaign_performance_last_7d")` etc. Each successful persist **appends** a new row (no dedupe). Use **`days=7`** for a rolling “weekly” window and **`days=30`** for “monthly” in GAQL `LAST_N_DAYS` terms. The CLI script also accepts **`--preset quarterly|yearly`**, or **`SYNC_CAMPAIGN_PERFORMANCE_DAYS`** in the environment when you omit `--days` / `--preset`.

**If Supabase returns `PGRST205` / “Could not find the table …”:** run the missing migration(s) in the **same** project as `SUPABASE_URL`: at minimum [`001_initial.sql`](../supabase/migrations/001_initial.sql); for narrative analyses also [`002_analysis_text_snapshots.sql`](../supabase/migrations/002_analysis_text_snapshots.sql). Wait for PostgREST to refresh, then retry.

**If inserts fail with `42501` / “row-level security policy”:** the table exists but writes use a key that is subject to RLS. Set `SUPABASE_SERVICE_ROLE_KEY` to the **service_role** secret (**Project Settings → API**), not the anon or publishable key. Our migration enables RLS and denies `anon` / `authenticated`; only the service role bypasses that for server-side scripts. Sync then returns `status: "supabase_permission_denied"`.

**Typical flow:** `list_accounts` → `save_client_context` (or **`sync_list_accounts_to_supabase`** to seed IDs) → **`get_campaign_performance`** with optional persist (or the CLI sync script on a schedule) → `save_memory`, **`save_analysis_text_snapshot`** (written analysis), or `save_report_snapshot` (metrics JSON) → later: `recall_client_context` + `list_report_snapshots` / **`list_analysis_text_snapshots`**.

## Implementation references

- Tool definitions: `google_ads_server.py`
- Keyword Planner: `keyword_plan_tools.py` (imported from `google_ads_server.py`)
- Campaign edits / mutations: `campaign_edit_tools.py`, `optimization_actions.py`, `mutate_helpers.py`
- Memory / snapshots: `memory_tools.py`, `supabase_store.py`
- Cross-source analysis: `analysis_tools.py` (imported from `google_ads_server.py` so tools register on the same MCP instance)

## CLI: calling tools from the terminal (no Cursor agent)

MCP tools in this repo are **async Python functions** on `google_ads_server` (and `memory_tools` for Supabase). They are **not** separate HTTP endpoints when you run them this way—you import the module and call `asyncio.run(...)`.

### Prerequisites

1. **Repository root** as current directory (so `.env` and imports resolve).
2. **Virtualenv** with dependencies: `.venv/bin/python` (create with `python3 -m venv .venv` and `pip install -r requirements.txt` if needed).
3. **Credentials** in `.env` (or exported env vars): OAuth or service account, plus `GOOGLE_ADS_DEVELOPER_TOKEN`, etc.

### Why `python -c "..."` often fails

Use **semicolons** between statements, or a **heredoc**. This is **invalid** (missing semicolons / newlines):

```bash
python -c "import asyncio from google_ads_server import list_accounts print(asyncio.run(list_accounts()))"
```

### Recommended: heredoc (copy-paste friendly)

From the repo root (adjust the `cd` path to your machine):

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads"

.venv/bin/python <<'PY'
import asyncio
from google_ads_server import list_accounts

print(asyncio.run(list_accounts()))
PY
```

### One-liner (valid `python -c`)

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python -c "import asyncio; from google_ads_server import list_accounts; print(asyncio.run(list_accounts()))"
```

### Pick Python: venv or system

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && (test -x .venv/bin/python && .venv/bin/python || python3) <<'PY'
import asyncio
from google_ads_server import list_accounts
print(asyncio.run(list_accounts()))
PY
```

### More tools (examples)

Replace `YOUR_CUSTOMER_ID` with a 10-digit customer id (no dashes).

**Account currency**

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from google_ads_server import get_account_currency

print(asyncio.run(get_account_currency("YOUR_CUSTOMER_ID")))
PY
```

**Campaign performance (last N days)**

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from google_ads_server import get_campaign_performance

print(asyncio.run(get_campaign_performance("YOUR_CUSTOMER_ID", days=14, persist_snapshot=True)))
PY
```

Set **`AUTO_PERSIST_CAMPAIGN_PERFORMANCE_SNAPSHOTS=1`** in `.env` to persist on every fetch without passing `persist_snapshot=True`. Requires the Supabase **secret** key as `SUPABASE_SERVICE_ROLE_KEY`.

**Arbitrary GAQL (`run_gaql`)** — third argument is output `format`: `table`, `json`, or `csv`.

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from google_ads_server import run_gaql

q = """
SELECT campaign.id, campaign.name, metrics.clicks
FROM campaign
WHERE segments.date DURING LAST_7_DAYS
LIMIT 5
"""
print(asyncio.run(run_gaql("YOUR_CUSTOMER_ID", q, format="table")))
PY
```

**`execute_gaql_query`** (same args as `run_gaql` minus format—returns formatted table text):

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from google_ads_server import execute_gaql_query

q = "SELECT customer.id, customer.descriptive_name FROM customer LIMIT 1"
print(asyncio.run(execute_gaql_query("YOUR_CUSTOMER_ID", q)))
PY
```

### OAuth from CLI

Use the dedicated script (browser flow):

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads"
.venv/bin/python scripts/run_oauth_login.py
# Full re-login after clearing token when safe:
.venv/bin/python scripts/run_oauth_login.py --force
```

### Supabase memory tools (optional)

If `SUPABASE_*` is configured, import from `memory_tools` the same way (all async), e.g. `save_client_context`, `recall_client_context`, `save_report_snapshot` — see `memory_tools.py` for parameter names.

**Example — recall client + recent memory**

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from memory_tools import recall_client_context

print(asyncio.run(recall_client_context(customer_id="YOUR_CUSTOMER_ID", memory_limit=10)))
PY
```

**CLI — sync without Cursor**

```bash
cd "/path/to/mcp-google-ads"
.venv/bin/python scripts/sync_list_accounts_to_supabase.py
.venv/bin/python scripts/sync_list_accounts_to_supabase.py --notes "seed from list_accounts"
```

**CLI — campaign performance → Supabase (`report_snapshots`)**

The script sets logging to **WARNING** by default (no OAuth/dotenv INFO noise). Use **`-v` / `--verbose`** for full **`google_ads_server`** (and **`httpx`**) INFO logs. It prints a short line to **stderr** right before **`googleAds:search`** (suppress with **`--quiet`**). The GAQL request uses **`GOOGLE_ADS_REQUEST_TIMEOUT`** seconds (default **120**); raise it if large accounts time out.

```bash
cd "/path/to/mcp-google-ads"
.venv/bin/python scripts/sync_campaign_performance_to_supabase.py --customer-id YOUR_CUSTOMER_ID --preset weekly --persist
.venv/bin/python scripts/sync_campaign_performance_to_supabase.py -c YOUR_CUSTOMER_ID --days 30 --persist --summary "Month end"
.venv/bin/python scripts/sync_campaign_performance_to_supabase.py -c YOUR_CUSTOMER_ID --preset quarterly --persist --json-only
# Default lookback without --days/--preset: set SYNC_CAMPAIGN_PERFORMANCE_DAYS=10 in .env or export it, then:
.venv/bin/python scripts/sync_campaign_performance_to_supabase.py -c YOUR_CUSTOMER_ID --persist
```

**Programmatic** — after you have `list_accounts` text, call `supabase_store.sync_list_accounts_output_to_clients(text, notes=...)`. For campaign rows + table string, use `asyncio.run(google_ads_server.fetch_campaign_performance_table_and_rows(customer_id, days))` then `supabase_store.persist_campaign_performance_snapshot(...)`.

### Alternative: run the MCP server

- **stdio** (for Cursor/Claude spawning the process): `.venv/bin/python google_ads_server.py`
- **HTTP (streamable-http)** for local testing with an MCP URL: `.venv/bin/python main.py` → then point a client at `http://127.0.0.1:8000/mcp` (see README / Railway notes).



//Last 7 days vs last 30 days performance
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
import logging
logging.basicConfig(level=logging.WARNING)

async def main():
    from google_ads_server import fetch_campaign_performance_table_and_rows

    cid = "2696255703"
    for days in (7, 30):
        print(f"\n{'='*80}\nLAST_{days}_DAYS\n{'='*80}")
        d = await fetch_campaign_performance_table_and_rows(cid, days)
        if not d["ok"]:
            print("ERROR:", d["error"])
            continue
        print(d["table"])
        print(f"\n(rows returned: {len(d['rows'])})")

asyncio.run(main())
PY

//save text analysis report date wise
Call save_analysis_text_snapshot with:

customer_id: 2696255703
analysis_type: campaign_performance_analysis
title: e.g. 7d vs 30d — campaign performance (INR)
body: the full text you pasted
metadata (optional): {"currency": "INR", "windows": ["LAST_7_DAYS", "LAST_30_DAYS"]}

cd "/Users/shivpanks/MCP Servers/mcp-google-ads"

.venv/bin/python <<'PY'
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(".") / ".env")

import json
import supabase_store as store

body = r'''<<text>>'''

if not store.is_configured():
    raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env")

row = store.insert_analysis_text_snapshot(
    "2696255703",
    "campaign_performance_analysis",
    body,
    title="7d vs 30d — campaign performance (INR)",
    metadata={"windows": ["LAST_7_DAYS", "LAST_30_DAYS"], "currency": "INR"},
    auto_upsert_client=True,
)
print(json.dumps(row, indent=2, default=str))
PY


Account analysis
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(".") / ".env")

# Ensure MCP side-effects (imports memory_tools + analysis_tools)
import google_ads_server  # noqa: F401
import analysis_tools


async def main():
    out = await analysis_tools.get_account_analysis_context("2696255703")
    print(out)


asyncio.run(main())
PY


Ad copy analysis
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(".") / ".env")
import google_ads_server  # noqa: F401
import analysis_tools

async def main():
    out = await analysis_tools.analyze_ad_copy(
        "YOUR_CUSTOMER_ID",
        days=30,
        # campaign_name_contains="Brand",  # optional
    )
    print(out)

asyncio.run(main())
PY
