# Deploy on Render

Host the Google Ads MCP server as a **Web Service** with **streamable HTTP** so Cursor (or other MCP clients) can connect remotely.

## Prerequisites

1. [Render](https://render.com) account
2. This repo pushed to GitHub (`shivpanks19/mcp-google-ads`)
3. **Google Ads service account** JSON (OAuth with browser does **not** work on Render)
4. Google Ads **developer token** (Basic/Standard for Keyword Planner)
5. Service account email invited in Google Ads: **Tools & settings → Access and security → Users** with **Standard** (edit) access if you use campaign mutate tools on Render
6. **Google Sheets API** enabled in the same GCP project as the service account; E-mail leads spreadsheet **shared** with the service account email (Viewer or Editor)
7. **Google Search Console API** enabled in the same GCP project; add the service account email as a user on each GSC property (Settings → Users and permissions)

### Google Search Console setup

1. GCP Console → same project as `GOOGLE_ADS_CREDENTIALS_JSON` → enable **Google Search Console API**.
2. In [Search Console](https://search.google.com/search-console) → property → **Settings → Users and permissions** → add the service account `client_email` (e.g. `google-ads-sa@crm-demo-2fc0c.iam.gserviceaccount.com`).
3. Note the **exact** property URL (`https://example.com/` vs `sc-domain:example.com`) — MCP tools require an exact match.
4. After deploy, call MCP tool `list_search_console_sites` to verify, then `get_search_console_analytics(site_url=..., dimension="query")`.

GSC uses **`webmasters.readonly`** scope by default on the same service account JSON. Set `GSC_SCOPE=full` on Render only if you need sitemap submit (not exposed as MCP tool yet).

**Search Console MCP tools:** `list_search_console_sites`, `get_search_console_analytics`, `get_search_console_page_query_map`, `inspect_search_console_url`, `list_search_console_sitemaps`.

### Google Analytics 4 setup

1. GCP Console → same project as `GOOGLE_ADS_CREDENTIALS_JSON` → enable **Google Analytics Data API** and **Google Analytics Admin API**.
2. In GA4 → **Admin → Property access management** → add the service account `client_email` as **Viewer**.
3. Set `GOOGLE_GA4_PROPERTY_ID` on Render (numeric ID from Admin → Property settings, e.g. `123456789`).
4. After deploy, call `list_ga4_properties` to verify, then `get_ga4_landing_page_performance` or `get_ga4_traffic_acquisition`.

GA4 uses **`analytics.readonly`** scope on the same service account JSON.

**GA4 MCP tools:** `list_ga4_properties`, `run_ga4_report`, `get_ga4_landing_page_performance`, `get_ga4_traffic_acquisition`, `get_ga4_organic_vs_paid`, `list_ga4_conversion_events`.

### Google Sheets setup

1. GCP Console → same project as `GOOGLE_ADS_CREDENTIALS_JSON` → enable **Google Sheets API** (and optionally **Google Drive API**).
2. Open the spreadsheet → **Share** → add the service account `client_email` from the JSON (e.g. `google-ads-sa@your-project.iam.gserviceaccount.com`) as **Editor** (required for `write_sheet_report` / `append_sheet_rows`; Viewer is read-only).
3. Set `GOOGLE_SHEETS_SPREADSHEET_ID` in Render (ID from the sheet URL, not the full URL).
4. After deploy, call MCP tool `read_email_leads` or `list_sheet_tabs` to verify.

Sheets uses **`spreadsheets`** scope (read/write) on the same JSON — separate from Ads `adwords` scope. Do not reuse `get_credentials()` for Sheets calls.

**Sheets MCP tools:** `list_sheet_tabs`, `read_sheet_range`, `write_sheet_range`, `append_sheet_rows`, `append_sheet_rows_from_file`, `append_sheet_rows_base64`, `push_markdown_tables_to_sheet`, `clear_sheet_tab`, `create_sheet_tab_tool`, `write_sheet_report`, `read_email_leads`.

For **large reports** (Cursor Cloud → remote MCP), prefer `push_markdown_tables_to_sheet(markdown_base64_gz=...)` or `append_sheet_rows_base64(gzip_compressed=true)` so the agent passes a compressed payload instead of megabyte JSON rows. Server-side batching splits API calls automatically.

Optional env:

| Variable | Default | Purpose |
|----------|---------|---------|
| `GOOGLE_SHEETS_ALLOWED_PATHS` | `/tmp,/workspace,/Users` | Roots for `*_from_file` tools |
| `GOOGLE_SHEETS_APPEND_BATCH_ROWS` | `500` | Rows per Sheets API batch |
| `GOOGLE_SHEETS_MAX_CELL_CHARS` | `49000` | Truncate long RSA/ad copy cells |

---

## Option A — Render Dashboard (matches your screenshot)

### 1. Create Web Service

1. [Render Dashboard](https://dashboard.render.com) → **New +** → **Web Service**
2. Connect **GitHub** → select **`shivpanks19/mcp-google-ads`**
3. Use these settings:

| Field | Value |
|-------|--------|
| **Name** | `mcp-google-ads` |
| **Region** | Oregon (or nearest) |
| **Branch** | `main` |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python main.py` |
| **Health Check Path** | `/health` |

Do **not** use the default `gunicorn your_application.wsgi` — this project is FastMCP + uvicorn via `main.py`.

### 2. Environment variables

In **Environment** → **Add Environment Variable**, set:

#### Required

| Variable | Value |
|----------|--------|
| `PYTHON_VERSION` | `3.11.9` |
| `GOOGLE_ADS_AUTH_TYPE` | `service_account` |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Your developer token |
| `GOOGLE_ADS_CREDENTIALS_JSON` | **Entire** service account key JSON on **one line** |
| `MCP_URL_AUTH_TOKEN` | Secret token required in the hosted MCP URL |

**`GOOGLE_ADS_CREDENTIALS_JSON` example (format only — use your real key):**

```json
{"type":"service_account","project_id":"your-project","private_key_id":"...","private_key":"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n","client_email":"ads-mcp@your-project.iam.gserviceaccount.com","client_id":"...","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs","client_x509_cert_url":"...","universe_domain":"googleapis.com"}
```

Paste as a **single line** in Render (no line breaks). Mark as **Secret**.

#### Optional

| Variable | When to set |
|----------|-------------|
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | **Required for MCC child accounts.** Manager ID `1698765209` (Hexanovate MCC). Without this, `generate_keyword_ideas` may return `CUSTOMER_NOT_FOUND` while `run_gaql` still works. |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | Spreadsheet ID for E-mail leads tools (`read_email_leads`, `read_sheet_range`). Share the sheet with the service account email. |
| `GOOGLE_SHEETS_EMAIL_LEADS_TAB` | Tab name (default `E-mail leads`) |
| `GOOGLE_SHEETS_LEAD_PIPELINE_TAB` | Optional tab name for future Lead Pipeline tools |
| `GOOGLE_ADS_REQUEST_TIMEOUT` | GAQL timeout seconds (default `120`) |
| `SUPABASE_URL` | Supabase memory / snapshots |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase **service_role** key (secret) |
| `AUTO_PERSIST_CAMPAIGN_PERFORMANCE_SNAPSHOTS` | `1` to auto-persist campaign snapshots |

Leave **`GOOGLE_ADS_CREDENTIALS_PATH`** unset on Render (use JSON env var only).

### 3. Deploy

Click **Create Web Service**. First deploy takes a few minutes.

### 4. Verify

```bash
curl https://YOUR-SERVICE.onrender.com/health
# {"status":"ok","service":"mcp-google-ads"}

curl https://YOUR-SERVICE.onrender.com/
# JSON with mcp_endpoint: /mcp
```

MCP URL for Cursor remote MCP:

```text
https://YOUR-SERVICE.onrender.com/mcp?token=YOUR_MCP_URL_AUTH_TOKEN
```

If your MCP client does not preserve query strings reliably, use the path-token
form instead:

```text
https://YOUR-SERVICE.onrender.com/YOUR_MCP_URL_AUTH_TOKEN/mcp
```

---

## Option B — Blueprint (`render.yaml`)

1. **New +** → **Blueprint**
2. Connect repo (includes `render.yaml` at repo root)
3. Set **secret** env vars in the dashboard after create (`GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CREDENTIALS_JSON`, etc.)

---

## Cursor remote MCP config

In Cursor MCP settings (remote / HTTP):

```json
{
  "mcpServers": {
    "google-ads-render": {
      "url": "https://YOUR-SERVICE.onrender.com/mcp?token=YOUR_MCP_URL_AUTH_TOKEN"
    }
  }
}
```

Exact JSON shape depends on your Cursor version; use the deployed authenticated **`/mcp`** URL.

---

## Security notes

- Set `MCP_URL_AUTH_TOKEN` as a Render **Secret**. When it is set, `/mcp` rejects requests without the matching URL token; `/` and `/health` remain public for discovery and health checks.
- Never commit `.env`, `credentials.json`, or service account keys to Git.
- Use Render **Secret** type for tokens and JSON keys.
- Restrict who can see the authenticated Render URL; it can call Google Ads with your service account permissions.
- **Campaign edit tools** (`update_campaign_status`, `apply_weekly_performance_actions`, etc.) work on Render the same as locally, but only if the service account has **edit** access on the target accounts. Read-only invites will fail mutate calls with permission errors.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Build fails on matplotlib/pandas | Optional deps in `requirements.txt`; upgrade Render plan or pin lighter versions if needed |
| `OAuth needs a local browser` | Set `GOOGLE_ADS_AUTH_TYPE=service_account` and `GOOGLE_ADS_CREDENTIALS_JSON` |
| `USER_PERMISSION_DENIED` | Invite service account email in Google Ads; set `GOOGLE_ADS_LOGIN_CUSTOMER_ID` for MCC |
| `CUSTOMER_NOT_FOUND` on Keyword Planner only | Set `GOOGLE_ADS_LOGIN_CUSTOMER_ID=1698765209` and redeploy; verify `GET /health` shows `"login_customer_id_configured": true` |
| Sheets `403 Permission denied` | Share spreadsheet with service account email; enable Sheets API in GCP |
| Sheets empty / wrong tab | Set `GOOGLE_SHEETS_EMAIL_LEADS_TAB` exactly (case-sensitive, default `E-mail leads`) |
| `invalid_scope` on Sheets | Sheets tools use `spreadsheets.readonly`, not `adwords` — use `read_sheet_range` / `read_email_leads`, not Ads credentials |
| `DEVELOPER_TOKEN_NOT_APPROVED` | Upgrade developer token to Basic/Standard in Google Ads API Center |
| Health check fails | Ensure **Health Check Path** is `/health` and **Start Command** is `python main.py` |
| Free tier sleeps | First request after idle may be slow (~30s); use Starter plan for always-on |

---

## Local parity (before deploying)

```bash
cd "/path/to/mcp-google-ads"
export PORT=8000
export GOOGLE_ADS_AUTH_TYPE=service_account
export GOOGLE_ADS_CREDENTIALS_JSON='{"type":"service_account",...}'
export GOOGLE_ADS_DEVELOPER_TOKEN=your_token
export GOOGLE_ADS_LOGIN_CUSTOMER_ID=1698765209
export GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id
python main.py
# curl http://127.0.0.1:8000/health
# MCP tools: list_sheet_tabs, read_sheet_range, read_email_leads
```
