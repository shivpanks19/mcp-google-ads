# YouTube organic uploads (google-ads-py MCP)

LinkedIn-style agent flow: **`video_url`** (HTTPS) or **`video_file_path`**, metadata, **`confirm_publish: true`**.

**Auth model:** OAuth **user** token with `refresh_token` in **`YOUTUBE_CREDENTIALS_JSON`**. The server **refreshes short-lived access tokens automatically** — no LinkedIn-style Render env sync required.

---

## One-time setup (Google Cloud)

1. Open [Google Cloud Console](https://console.cloud.google.com/) → same project as your OAuth client (or create one).
2. **APIs & Services → Library** → enable **YouTube Data API v3**.
3. **APIs & Services → OAuth consent screen** → add your Google account as a test user (if app is in Testing).
4. **Credentials → OAuth 2.0 Client ID** → type **Desktop** (or reuse existing desktop client).
5. In `mcp-google-ads/.env` set (if not already):
   - `GOOGLE_ADS_CLIENT_ID` / `GOOGLE_ADS_CLIENT_SECRET`  
   - or `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET`

---

## One-time setup (local OAuth → Render secret)

From repo root on your Mac:

```bash
cd "/Users/shivpanks/MCP Servers/mcp-google-ads"
source .venv/bin/activate   # or .venv/bin/python directly

# First login (browser opens — use the Google account that owns the YouTube channel)
.venv/bin/python scripts/run_youtube_oauth_login.py

# If you already logged in but JSON has no refresh_token:
.venv/bin/python scripts/run_youtube_oauth_login.py --force
```

Token file default: `~/.config/mcp-google-ads/youtube_token.json`

Export **one line** for Render:

```bash
.venv/bin/python scripts/export_youtube_token_for_render.py > /tmp/youtube-render-secret.json
# Copy the single JSON line (not the stderr comment) into Render
```

---

## Render

1. **Dashboard → mcp-google-ads → Environment**
2. Add secret **`YOUTUBE_CREDENTIALS_JSON`** = entire authorized-user JSON **on one line** (from export script).
3. Optional: **`YOUTUBE_DEFAULT_PRIVACY=unlisted`**
4. Save → wait for deploy (auto from `main`).

**Verify (no MCP auth token needed on /health):**

```bash
curl -s "https://YOUR-RENDER-HOST/health" | jq '.youtube_auth'
```

Expect:

```json
{
  "configured": true,
  "ready": true,
  "has_refresh_token": true,
  "message": "OK for hosted uploads."
}
```

5. In Cursor, call **`list_youtube_channels_tool`** — should list the channel for that Google account.

---

## MCP tools

| Tool | Purpose |
|------|---------|
| `list_youtube_channels_tool` | Channels for the authorized account |
| `upload_youtube_video` | Upload → `videoId`, `watchUrl` |
| `publish_youtube_video` | Same as upload |

Example:

```json
{
  "title": "Hook / title",
  "description": "Body + links",
  "video_url": "https://cdn.example.com/video.mp4",
  "privacy_status": "unlisted",
  "confirm_publish": true
}
```

---

## Troubleshooting

| Symptom | Fix |
|--------|-----|
| `/health` → `ready: false`, no refresh_token | `run_youtube_oauth_login.py --force`, re-export to Render |
| `YOUTUBE_CREDENTIALS_JSON is a service account` | Paste **user** token JSON, not `GOOGLE_ADS_CREDENTIALS_JSON` |
| `RefreshError` | Token revoked or wrong client — re-run OAuth, update Render secret |
| Upload works locally, not on Render | Confirm `YOUTUBE_CREDENTIALS_JSON` is set on **Render**, not only in local `.env` |

---

## Why not LinkedIn-style hosted OAuth?

Google refresh tokens stay valid for months/years when stored in env; only **access** tokens expire (~1h) and refresh in process. Hosted OAuth on Render is optional convenience, not required for reliability.
