# Campaign edit examples (CLI)

These examples call **write** MCP tools from the repo root. Changes apply **immediately** in Google Ads — use a test account or conservative thresholds first.

**Requirements:** OAuth or service account with **edit** access on the target account. Import `google_ads_server` first so side-effect modules register.

Replace `YOUR_CUSTOMER_ID` with a 10-digit customer ID (no dashes). Example account used in samples: **`2696255703`**.

---

## Agent workflow (Analyze → Apply)

1. **`list_accounts`** → pick `customer_id`
2. **`get_campaign_performance`** (`days=7`) — baseline
3. **`analyze_running_campaigns_and_ads`** (`campaign_days=7`, `ad_days=7`) — Markdown recommendations
4. **`get_campaign_settings`** — confirm budget / bidding before edits
5. **`apply_weekly_performance_actions`** or surgical mutate tools
6. Optional: **`save_analysis_text_snapshot`** + Supabase audit from weekly apply

See also [`mcp-tools-and-reports.md`](mcp-tools-and-reports.md).

---

## Read campaign settings

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(".") / ".env")
import google_ads_server  # noqa: F401
import campaign_edit_tools as cet

async def main():
    print(await cet.get_campaign_settings(
        "2696255703",
        campaign_name="Brand Search",  # exact name; or use campaign_id=
    ))

asyncio.run(main())
PY
```

---

## Pause a wasteful campaign

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(".") / ".env")
import google_ads_server  # noqa: F401
import campaign_edit_tools as cet

async def main():
    print(await cet.update_campaign_status(
        "2696255703",
        campaign_id="12345678901",  # replace with real id
        status="PAUSED",
    ))

asyncio.run(main())
PY
```

---

## Update daily budget (INR)

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(".") / ".env")
import google_ads_server  # noqa: F401
import campaign_edit_tools as cet

async def main():
    print(await cet.update_campaign_budget(
        "2696255703",
        campaign_id="12345678901",
        daily_budget=500.0,  # INR; converted to account currency micros
    ))

asyncio.run(main())
PY
```

If the budget is **shared** across campaigns, the tool returns a warning — consider `list_campaign_budgets` first.

---

## Add campaign negative keywords

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(".") / ".env")
import google_ads_server  # noqa: F401
import campaign_edit_tools as cet

async def main():
    print(await cet.add_negative_keywords(
        "2696255703",
        keywords=["free download", "jobs"],
        level="campaign",
        campaign_id="12345678901",
        match_type="PHRASE",
    ))

asyncio.run(main())
PY
```

---

## Add negatives from waste search terms (7d)

Pulls queries with spend above threshold and zero conversions, then adds as campaign negatives:

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(".") / ".env")
import google_ads_server  # noqa: F401
import campaign_edit_tools as cet

async def main():
    print(await cet.add_campaign_negative_keywords_from_search_terms(
        "2696255703",
        campaign_id="12345678901",
        days=7,
        min_cost_inr=200.0,
        max_conversions=0,
    ))

asyncio.run(main())
PY
```

---

## Weekly performance automation (account 2696255703)

Classifies campaigns and applies pause / budget changes directly. When Supabase is configured, writes a **`save_memory`** audit entry.

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(".") / ".env")
import google_ads_server  # noqa: F401
import optimization_actions as oa

async def main():
    print(await oa.apply_weekly_performance_actions(
        "2696255703",
        days=7,
        pause_zero_conversion_spenders=True,
        min_spend_inr=500.0,
        min_clicks_for_pause=30,
        reduce_high_cpa_budget=True,
        max_cpa_multiplier=2.0,
        add_search_term_negatives=False,  # set True to auto-add ST negatives
        shift_budget_pct=0,  # e.g. 10 to move 10% from worst to best CPA
    ))

asyncio.run(main())
PY
```

---

## Apply explicit actions after review

Use when an agent (or human) has already decided the action list from `analyze_running_campaigns_and_ads`:

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(".") / ".env")
import google_ads_server  # noqa: F401
import optimization_actions as oa

async def main():
    actions = [
        {"type": "pause", "campaign_id": "12345678901"},
        {"type": "budget", "campaign_id": "98765432109", "daily_budget": 800.0},
    ]
    print(await oa.analyze_and_apply_campaign_edits("2696255703", actions=actions))

asyncio.run(main())
PY
```

---

## Bulk update multiple campaigns

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads" && .venv/bin/python <<'PY'
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(".") / ".env")
import google_ads_server  # noqa: F401
import campaign_edit_tools as cet

async def main():
    ops = [
        {"campaign_id": "111", "status": "PAUSED"},
        {"campaign_id": "222", "daily_budget": 300.0},
    ]
    print(await cet.bulk_update_campaigns("2696255703", operations=ops))

asyncio.run(main())
PY
```

---

## Live integration tests (optional)

Unit tests mock the API. To run **real** mutations against a test account only:

```bash
export RUN_LIVE_GOOGLE_ADS_MUTATION_TESTS=1
.venv/bin/python -m unittest test_campaign_edit_tools -v
```

Do not enable this flag on production accounts.
