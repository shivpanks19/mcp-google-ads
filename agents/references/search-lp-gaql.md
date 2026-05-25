# Search campaigns ↔ landing pages (GAQL reference)

Use these patterns to **map Search traffic to final URLs**, audit **message match**, and find **LP-level waste**. Replace date windows as needed (`LAST_7_DAYS`, `LAST_30_DAYS`, or `BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD'`).

**Prerequisites**

- `customer_id`: 10 digits, no dashes.
- For **MCC-linked clients**, set `GOOGLE_ADS_LOGIN_CUSTOMER_ID` when required.
- RSA / Demand Gen–only accounts: avoid `expanded_text_ad.*` fields; use `ad_group_ad.ad.final_urls` and `ad_group_ad.ad.type`.

---

## 1) Search campaigns inventory

```sql
SELECT
  campaign.id,
  campaign.name,
  campaign.status,
  campaign.advertising_channel_type,
  campaign.bidding_strategy_type
FROM campaign
WHERE campaign.advertising_channel_type = 'SEARCH'
ORDER BY campaign.name
LIMIT 200
```

---

## 2) Enabled Search ads → final URLs + recent performance

Aggregates metrics per row (include `segments.date` only if you need daily grain; it multiplies rows).

```sql
SELECT
  campaign.id,
  campaign.name,
  ad_group.id,
  ad_group.name,
  ad_group_ad.resource_name,
  ad_group_ad.status,
  ad_group_ad.ad.id,
  ad_group_ad.ad.name,
  ad_group_ad.ad.type,
  ad_group_ad.ad.final_urls,
  metrics.impressions,
  metrics.clicks,
  metrics.conversions,
  metrics.cost_micros
FROM ad_group_ad
WHERE campaign.advertising_channel_type = 'SEARCH'
  AND campaign.status = 'ENABLED'
  AND ad_group.status = 'ENABLED'
  AND ad_group_ad.status = 'ENABLED'
  AND segments.date DURING LAST_7_DAYS
ORDER BY metrics.cost_micros DESC
LIMIT 100
```

**Notes**

- `final_urls` is a **list** in the JSON response.
- For RSA headlines/descriptions use `ad_group_ad.ad.responsive_search_ad.*` in a separate, narrower query if needed (field compatibility varies by API version).

---

## 3) Expanded final URLs (account-level LP performance)

```sql
SELECT
  expanded_landing_page_view.expanded_final_url,
  metrics.impressions,
  metrics.clicks,
  metrics.conversions,
  metrics.cost_micros
FROM expanded_landing_page_view
WHERE segments.date DURING LAST_7_DAYS
ORDER BY metrics.cost_micros DESC
LIMIT 100
```

Use this to see **which normalized URLs** consumed spend, independent of a single ad row.

---

## 4) Search terms (intent vs LP) — high spend

```sql
SELECT
  campaign.name,
  ad_group.name,
  search_term_view.search_term,
  search_term_view.status,
  metrics.impressions,
  metrics.clicks,
  metrics.conversions,
  metrics.cost_micros
FROM search_term_view
WHERE segments.date DURING LAST_7_DAYS
  AND campaign.advertising_channel_type = 'SEARCH'
ORDER BY metrics.cost_micros DESC
LIMIT 200
```

---

## 5) Keywords with LP relevance / QS (Search)

```sql
SELECT
  campaign.name,
  ad_group.name,
  ad_group_criterion.keyword.text,
  ad_group_criterion.keyword.match_type,
  ad_group_criterion.quality_info.quality_score,
  metrics.impressions,
  metrics.clicks,
  metrics.conversions,
  metrics.cost_micros
FROM keyword_view
WHERE campaign.advertising_channel_type = 'SEARCH'
  AND campaign.status = 'ENABLED'
  AND ad_group.status = 'ENABLED'
  AND ad_group_criterion.status = 'ENABLED'
  AND segments.date DURING LAST_7_DAYS
ORDER BY metrics.cost_micros DESC
LIMIT 200
```

---

## Safety

- Prefer **`LIMIT`** + **`ORDER BY`** on exploratory queries.
- **Read-only** in this document; mutates belong in dedicated MCP tools / offline reviews.

---

## Test run (live MCP, `run_gaql`)

**Account:** `6491446793` (display `649-144-6793`)  
**Window:** `LAST_7_DAYS` where applicable  
**Result:** All five query families returned **HTTP 200** — patterns are valid for this account / API version.

**Observed mappings (high level)**

- **Search inventory:** 4 live `ENABLED` Search campaigns (`Hexa | Competitor ads (Ekin)…`, `Hexa_Search_Ads_E_B…`, `Hexa | IFP…`, `Hexa | North India`) plus paused/removed history rows.
- **`ad_group_ad` → `final_urls`:** RSA ads consistently land on  
  `https://products.ekin.net.in/interactive-display/interactive-flat-panel-ai-royal-series/`  
  (top spend rows: `Hexa_Search_Ads_E_B`, Competitor, IFP).
- **`expanded_landing_page_view`:** Same product URL with **tracking templates** (UTM + `gad_campaignid`); secondary hosts **`https://ekin.net.in/`** and **`https://ekin.net.in/interactive-display/`** appear with meaningful cost — worth **CRO / consistency** review vs product LP.
- **`search_term_view`:** High spend on **brand + category** (`ekin smart board`, `teachmint`, `digital board`, competitor names); some **low-intent / off-topic** terms (`whiteboard com free`, `hikvision tv 65 inch`, `google jamboard`) — good **negative-keyword** candidates after human review.
- **`keyword_view`:** `teachmint` PHRASE shows **QS 3** with heavy spend; `ekin smart board` PHRASE **QS 8** — aligns LP/ RSA work with QS diagnostics.
