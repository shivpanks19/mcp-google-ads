# Keyword Planner examples (CLI)

These examples call MCP tools directly from the repo root (same pattern as [`mcp-tools-and-reports.md`](mcp-tools-and-reports.md)). Replace `YOUR_CUSTOMER_ID` with a 10-digit Google Ads customer ID.

## Agent workflow

1. **`list_accounts`** → pick `customer_id`
2. **`suggest_geo_targets`** → confirm India (`geoTargetConstants/2356`) or city-level targets
3. **`generate_keyword_ideas`** with seed keywords + geo + language
4. **`get_keyword_metrics`** on a shortlisted list
5. **(Post-launch)** **`run_gaql`** on `search_term_view` for optimization

**Note:** Keyword Planner API methods require a **Basic or Standard** Google Ads developer token. Explorer (test) tokens return `DEVELOPER_TOKEN_NOT_APPROVED`. Geo resolution via `suggest_geo_targets` uses the global `geoTargetConstants:suggest` endpoint (no customer ID in the URL).

## Example seeds (enterprise AV / India B2B)

- `microsoft teams room setup`
- `video conferencing solution enterprise`
- `digital signage mumbai`
- `yealink dealer maharashtra`

---

## Resolve geo targets (India + Mumbai)

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from keyword_plan_tools import suggest_geo_targets

print(asyncio.run(suggest_geo_targets(
    customer_id="YOUR_CUSTOMER_ID",
    location_names=["India", "Mumbai", "Maharashtra"],
    country_code="IN",
)))
PY
```

---

## Generate keyword ideas (India + English)

Enterprise AV / collaboration seeds:

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from keyword_plan_tools import generate_keyword_ideas

print(asyncio.run(generate_keyword_ideas(
    customer_id="YOUR_CUSTOMER_ID",
    seed_keywords=[
        "microsoft teams room setup",
        "video conferencing solution enterprise",
        "digital signage mumbai",
        "yealink dealer maharashtra",
    ],
    geo_targets=["India"],
    language="English",
    network="GOOGLE_SEARCH",
    limit=50,
)))
PY
```

Quick smoke test (two seeds):

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from keyword_plan_tools import generate_keyword_ideas

print(asyncio.run(generate_keyword_ideas(
    customer_id="YOUR_CUSTOMER_ID",
    seed_keywords=["microsoft teams room", "yealink mvc kit"],
    geo_targets=["India"],
    language="English",
    limit=25,
)))
PY
```

---

## Mumbai / Maharashtra geo targeting

Pass multiple geo names or resolved resource names:

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from keyword_plan_tools import generate_keyword_ideas

print(asyncio.run(generate_keyword_ideas(
    customer_id="YOUR_CUSTOMER_ID",
    seed_keywords=["video conferencing mumbai", "meeting room av maharashtra"],
    geo_targets=["Mumbai", "Maharashtra"],
    language="English",
    limit=30,
)))
PY
```

Or use explicit resource names after `suggest_geo_targets`:

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from keyword_plan_tools import generate_keyword_ideas

print(asyncio.run(generate_keyword_ideas(
    customer_id="YOUR_CUSTOMER_ID",
    seed_keywords=["digital signage installation"],
    geo_targets=["geoTargetConstants/2356"],  # India
    limit=20,
)))
PY
```

---

## Historical metrics for a shortlist

After reviewing ideas, refresh metrics for exact keywords:

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from keyword_plan_tools import get_keyword_metrics

print(asyncio.run(get_keyword_metrics(
    customer_id="YOUR_CUSTOMER_ID",
    keywords=[
        "microsoft teams room",
        "yealink mvc kit",
        "video conferencing solution",
    ],
    geo_targets=["India"],
    language="English",
    format="table",
)))
PY
```

---

## JSON output (for agents / pipelines)

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from keyword_plan_tools import generate_keyword_ideas

print(asyncio.run(generate_keyword_ideas(
    customer_id="YOUR_CUSTOMER_ID",
    seed_keywords=["microsoft teams room", "yealink mvc kit"],
    geo_targets=["India"],
    format="json",
    limit=10,
)))
PY
```

---

## Site or URL seeds

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from keyword_plan_tools import generate_keyword_ideas

# Page URL seed
print(asyncio.run(generate_keyword_ideas(
    customer_id="YOUR_CUSTOMER_ID",
    page_url="https://www.example.com/teams-rooms",
    geo_targets=["India"],
    limit=30,
)))
PY
```

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from keyword_plan_tools import generate_keyword_ideas

# Whole-site seed
print(asyncio.run(generate_keyword_ideas(
    customer_id="YOUR_CUSTOMER_ID",
    site_url="example.com",
    geo_targets=["India"],
    limit=30,
)))
PY
```

---

## Post-launch: live search terms (GAQL)

Once campaigns are running, switch to GAQL for actual query performance:

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from google_ads_server import run_gaql

query = """
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
"""
print(asyncio.run(run_gaql("YOUR_CUSTOMER_ID", query)))
PY
```
