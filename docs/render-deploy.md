# Deploy on Render

Host the Google Ads MCP server as a **Web Service** with **streamable HTTP** so Cursor (or other MCP clients) can connect remotely.

## Prerequisites

1. [Render](https://render.com) account
2. This repo pushed to GitHub (`shivpanks19/mcp-google-ads`)
3. **Google Ads service account** JSON (OAuth with browser does **not** work on Render)
4. Google Ads **developer token** (Basic/Standard for Keyword Planner)
5. Service account email invited in Google Ads: **Tools & settings → Access and security → Users** with **Standard** (edit) access if you use campaign mutate tools on Render

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

**`GOOGLE_ADS_CREDENTIALS_JSON` example (format only — use your real key):**

```json
{"type":"service_account","project_id":"your-project","private_key_id":"...","private_key":"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n","client_email":"ads-mcp@your-project.iam.gserviceaccount.com","client_id":"...","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs","client_x509_cert_url":"...","universe_domain":"googleapis.com"}
```

Paste as a **single line** in Render (no line breaks). Mark as **Secret**.

#### Optional

| Variable | When to set |
|----------|-------------|
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | MCC manager ID (10 digits, no dashes) when accessing client accounts under an MCC |
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
https://YOUR-SERVICE.onrender.com/mcp
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
      "url": "https://YOUR-SERVICE.onrender.com/mcp"
    }
  }
}
```

Exact JSON shape depends on your Cursor version; use the deployed **`/mcp`** URL.

---

## Security notes

- The service is **public** unless you add auth in front of it (Render private networking, API gateway, or FastMCP auth).
- Never commit `.env`, `credentials.json`, or service account keys to Git.
- Use Render **Secret** type for tokens and JSON keys.
- Restrict who can use the Render URL; it can call Google Ads with your service account permissions.
- **Campaign edit tools** (`update_campaign_status`, `apply_weekly_performance_actions`, etc.) work on Render the same as locally, but only if the service account has **edit** access on the target accounts. Read-only invites will fail mutate calls with permission errors.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Build fails on matplotlib/pandas | Optional deps in `requirements.txt`; upgrade Render plan or pin lighter versions if needed |
| `OAuth needs a local browser` | Set `GOOGLE_ADS_AUTH_TYPE=service_account` and `GOOGLE_ADS_CREDENTIALS_JSON` |
| `USER_PERMISSION_DENIED` | Invite service account email in Google Ads; set `GOOGLE_ADS_LOGIN_CUSTOMER_ID` for MCC |
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
python main.py
# curl http://127.0.0.1:8000/health
```
