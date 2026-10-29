# YouTube organic uploads (google-ads-py MCP)

Pattern matches **linkedin-mcp** organic video: agents pass **`video_url`** (HTTPS) or **`video_file_path`**, plus metadata, and **`confirm_publish: true`**.

## MCP tools

| Tool | Purpose |
|------|---------|
| `list_youtube_channels_tool` | Channels for the authorized Google account |
| `upload_youtube_video` | Resumable upload; returns `videoId` and `watchUrl` |
| `publish_youtube_video` | Alias of upload for agents |

## Auth

YouTube uploads need **OAuth user** credentials with **`youtube.upload`** scope. The Ads **service account** is not sufficient for a normal channel.

1. Enable **YouTube Data API v3** in Google Cloud.
2. Run `.venv/bin/python scripts/run_youtube_oauth_login.py`
3. On Render, set **`YOUTUBE_CREDENTIALS_JSON`** to the saved token JSON.

See `.env.example` for `YOUTUBE_*` variables.
