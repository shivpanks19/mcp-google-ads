"""
MCP tools for organic YouTube video uploads via YouTube Data API v3.

Requires OAuth user credentials with youtube.upload scope — not the Google Ads
service account. Mirror linkedin-mcp: pass videoUrl (HTTPS) or videoFilePath,
plus title/description; confirmPublish gates live uploads.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import socket
import tempfile
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GARequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from pydantic import Field

from google_ads_server import mcp

logger = logging.getLogger("google_ads_server")

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

_youtube_credentials_cache: Optional[Credentials] = None

DEFAULT_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
CHUNK_SIZE = 8 * 1024 * 1024


def reset_youtube_caches() -> None:
    global _youtube_credentials_cache
    _youtube_credentials_cache = None


def _json_response(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


def _load_youtube_json_env() -> Optional[Dict[str, Any]]:
    raw = os.environ.get("YOUTUBE_CREDENTIALS_JSON")
    if raw is None or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"YOUTUBE_CREDENTIALS_JSON must be valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("YOUTUBE_CREDENTIALS_JSON must be a JSON object")
    return data


def _youtube_token_path() -> str:
    explicit = (os.environ.get("YOUTUBE_TOKEN_PATH") or "").strip()
    if explicit:
        return explicit
    home_default = Path.home() / ".config" / "mcp-google-ads" / "youtube_token.json"
    if home_default.parent.exists() or not os.environ.get("PORT"):
        return str(home_default)
    return os.path.join(os.environ.get("TMPDIR", "/tmp"), "youtube_token.json")


def _oauth_client_config() -> Optional[Dict[str, Any]]:
    blob = _load_youtube_json_env()
    if blob and ("installed" in blob or "web" in blob):
        return blob
    client_id = (os.environ.get("YOUTUBE_CLIENT_ID") or os.environ.get("GOOGLE_ADS_CLIENT_ID") or "").strip()
    client_secret = (
        os.environ.get("YOUTUBE_CLIENT_SECRET") or os.environ.get("GOOGLE_ADS_CLIENT_SECRET") or ""
    ).strip()
    if not client_id or not client_secret:
        return None
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        }
    }


def _max_download_bytes() -> int:
    raw = (os.environ.get("YOUTUBE_MAX_DOWNLOAD_BYTES") or "").strip()
    if not raw:
        return DEFAULT_MAX_DOWNLOAD_BYTES
    try:
        value = int(raw)
        return value if value > 0 else DEFAULT_MAX_DOWNLOAD_BYTES
    except ValueError:
        return DEFAULT_MAX_DOWNLOAD_BYTES


def _host_blocks_private(hostname: str) -> bool:
    if not hostname:
        return True
    host = hostname.lower().strip(".")
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        try:
            addr = ip_address(info[4][0])
        except ValueError:
            continue
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            return True
    return False


def _validate_https_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("https",):
        raise ValueError("videoUrl must be an https:// URL")
    if not parsed.netloc:
        raise ValueError("videoUrl is missing a host")
    if _host_blocks_private(parsed.hostname or ""):
        raise ValueError("videoUrl must not point to a private or local host")
    return url.strip()


def _resolve_video_path(
    video_url: Optional[str],
    video_file_path: Optional[str],
) -> Tuple[Path, bool]:
    if video_url and video_file_path:
        raise ValueError("Pass only one of videoUrl or videoFilePath")
    if video_file_path:
        path = Path(video_file_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"videoFilePath not found: {path}")
        return path, False
    if not video_url:
        raise ValueError("Provide videoUrl or videoFilePath")
    safe_url = _validate_https_url(video_url)
    max_bytes = _max_download_bytes()
    suffix = ".mp4"
    if re.search(r"\.(mov|webm|mkv|avi)(?:\?|$)", safe_url, re.I):
        suffix = Path(urlparse(safe_url).path).suffix or suffix
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = Path(tmp.name)
    downloaded = 0
    try:
        with requests.get(safe_url, stream=True, timeout=(30, 600)) as resp:
            resp.raise_for_status()
            cl = resp.headers.get("Content-Length")
            if cl and int(cl) > max_bytes:
                raise ValueError(f"videoUrl Content-Length exceeds limit ({max_bytes} bytes)")
            for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    raise ValueError(f"Download exceeded YOUTUBE_MAX_DOWNLOAD_BYTES ({max_bytes})")
                tmp.write(chunk)
        tmp.close()
    except Exception:
        tmp.close()
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path, True


def _guess_mimetype(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "video/mp4"


def _load_stored_user_token_dict() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return (authorized_user_token_dict, source_label) without network I/O."""
    blob = _load_youtube_json_env()
    if blob:
        if blob.get("type") == "service_account":
            return None, "YOUTUBE_CREDENTIALS_JSON(service_account)"
        if "installed" in blob or "web" in blob:
            return None, "YOUTUBE_CREDENTIALS_JSON(oauth_client_only)"
        return blob, "YOUTUBE_CREDENTIALS_JSON"

    token_path = _youtube_token_path()
    if os.path.isfile(token_path):
        try:
            with open(token_path, encoding="utf-8") as f:
                token_data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None, "YOUTUBE_TOKEN_PATH(invalid)"
        if isinstance(token_data, dict) and "installed" not in token_data and "web" not in token_data:
            return token_data, "YOUTUBE_TOKEN_PATH"
    return None, None


def youtube_auth_readiness() -> Dict[str, Any]:
    """
    Non-secret status for /health and local checks.
    Render only needs refresh_token in YOUTUBE_CREDENTIALS_JSON once; access tokens refresh in RAM.
    """
    token_dict, source = _load_stored_user_token_dict()
    if source and source.endswith("(service_account)"):
        return {
            "configured": True,
            "ready": False,
            "source": source,
            "has_refresh_token": False,
            "message": "Use OAuth user JSON, not the Google Ads service account key.",
        }
    if source and source.endswith("(oauth_client_only)"):
        return {
            "configured": True,
            "ready": False,
            "source": source,
            "has_refresh_token": False,
            "message": "YOUTUBE_CREDENTIALS_JSON is OAuth client config only; run run_youtube_oauth_login.py.",
        }
    if not token_dict:
        return {
            "configured": False,
            "ready": False,
            "source": None,
            "has_refresh_token": False,
            "message": "Set YOUTUBE_CREDENTIALS_JSON on Render or run scripts/run_youtube_oauth_login.py locally.",
        }

    has_refresh = bool(token_dict.get("refresh_token"))
    return {
        "configured": True,
        "ready": has_refresh,
        "source": source,
        "has_refresh_token": has_refresh,
        "token_expiry_present": bool(token_dict.get("expiry") or token_dict.get("token")),
        "refresh_on_server": "automatic (google-auth refresh; no Render env sync required)",
        "message": (
            "OK for hosted uploads."
            if has_refresh
            else "Missing refresh_token — re-run OAuth with scripts/run_youtube_oauth_login.py --force"
        ),
    }


def get_youtube_credentials() -> Credentials:
    global _youtube_credentials_cache
    if _youtube_credentials_cache is not None and _youtube_credentials_cache.valid:
        return _youtube_credentials_cache

    creds: Optional[Credentials] = None
    client_config = _oauth_client_config()
    blob = _load_youtube_json_env()
    token_path = _youtube_token_path()

    if blob and blob.get("type") == "service_account":
        raise ValueError(
            "YouTube uploads require OAuth user credentials, not a service account. "
            "Run scripts/run_youtube_oauth_login.py locally and set YOUTUBE_CREDENTIALS_JSON."
        )

    if blob and "installed" not in blob and "web" not in blob:
        creds = Credentials.from_authorized_user_info(blob, YOUTUBE_SCOPES)
    elif os.path.isfile(token_path):
        try:
            with open(token_path, encoding="utf-8") as f:
                token_data = json.load(f)
            if "installed" in token_data or "web" in token_data:
                client_config = token_data
            else:
                creds = Credentials.from_authorized_user_info(token_data, YOUTUBE_SCOPES)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {token_path}: {exc}") from exc

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(GARequest())
            except RefreshError as exc:
                raise ValueError(f"YouTube OAuth refresh failed: {exc}") from exc
        elif not creds:
            if not client_config:
                raise ValueError(
                    "YouTube OAuth not configured. Set YOUTUBE_CREDENTIALS_JSON, "
                    "YOUTUBE_TOKEN_PATH, or client ID/secret and run run_youtube_oauth_login.py."
                )
            if os.environ.get("PORT") or os.environ.get("RENDER"):
                raise ValueError(
                    "YouTube OAuth browser flow cannot run on this server. "
                    "Authorize locally and set YOUTUBE_CREDENTIALS_JSON."
                )
            flow = InstalledAppFlow.from_client_config(client_config, YOUTUBE_SCOPES)
            creds = flow.run_local_server(
                port=0,
                access_type="offline",
                prompt="consent",
            )

        parent = os.path.dirname(token_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        except OSError as exc:
            logger.warning("Could not persist YouTube token to %s: %s", token_path, exc)

    _youtube_credentials_cache = creds
    return creds


def _default_privacy() -> str:
    value = (os.environ.get("YOUTUBE_DEFAULT_PRIVACY") or "unlisted").strip().lower()
    if value not in ("public", "unlisted", "private"):
        return "unlisted"
    return value


def _execute_upload(
    *,
    video_path: Path,
    title: str,
    description: str,
    tags: Optional[List[str]],
    category_id: Optional[str],
    privacy_status: str,
    made_for_kids: bool,
) -> Dict[str, Any]:
    creds = get_youtube_credentials()
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

    body: Dict[str, Any] = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": bool(made_for_kids),
        },
    }
    if tags:
        body["snippet"]["tags"] = [t.strip()[:100] for t in tags if t.strip()][:500]
    if category_id:
        body["snippet"]["categoryId"] = str(category_id)

    media = MediaFileUpload(
        str(video_path),
        mimetype=_guess_mimetype(video_path),
        resumable=True,
        chunksize=CHUNK_SIZE,
    )
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("YouTube upload progress: %.0f%%", status.progress() * 100)

    video_id = response.get("id")
    return {
        "videoId": video_id,
        "watchUrl": f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
        "privacyStatus": privacy_status,
        "title": title,
        "uploadResponse": response,
    }


def list_youtube_channels() -> Dict[str, Any]:
    creds = get_youtube_credentials()
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    resp = youtube.channels().list(part="snippet,contentDetails,statistics", mine=True).execute()
    items = resp.get("items") or []
    channels = []
    for item in items:
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        channels.append(
            {
                "channelId": item.get("id"),
                "title": snippet.get("title"),
                "customUrl": snippet.get("customUrl"),
                "subscriberCount": stats.get("subscriberCount"),
                "videoCount": stats.get("videoCount"),
            }
        )
    return {"count": len(channels), "channels": channels}


@mcp.tool()
async def list_youtube_channels_tool() -> str:
    """List YouTube channels for the authorized Google account (mine=True)."""
    try:
        return _json_response(list_youtube_channels())
    except Exception as exc:
        logger.exception("list_youtube_channels_tool failed")
        return _json_response({"error": f"{type(exc).__name__}: {exc}"})


@mcp.tool()
async def upload_youtube_video(
    title: Annotated[str, Field(description="Video title (max 100 chars)")],
    video_url: Annotated[
        Optional[str],
        Field(default=None, description="Public HTTPS URL of the video file."),
    ] = None,
    video_file_path: Annotated[
        Optional[str],
        Field(default=None, description="Absolute path to a local video file."),
    ] = None,
    description: Annotated[str, Field(description="Video description.")] = "",
    tags: Annotated[Optional[List[str]], Field(default=None)] = None,
    privacy_status: Annotated[str, Field(description="public, unlisted, or private.")] = "",
    category_id: Annotated[Optional[str], Field(default=None)] = None,
    made_for_kids: Annotated[bool, Field(default=False)] = False,
    confirm_publish: Annotated[
        bool,
        Field(description="Must be true to upload (LinkedIn-style safety gate)."),
    ] = False,
) -> str:
    """Upload a video to YouTube. Requires OAuth with youtube.upload scope."""
    if not confirm_publish:
        return _json_response(
            {
                "error": "Refused: set confirm_publish=true to upload.",
                "hint": "Same safety gate as linkedin-mcp publish_organization_post.",
            }
        )
    privacy = (privacy_status or _default_privacy()).strip().lower()
    if privacy not in ("public", "unlisted", "private"):
        return _json_response({"error": f"Invalid privacy_status: {privacy_status}"})

    temp_path: Optional[Path] = None
    delete_temp = False
    try:
        path, delete_temp = _resolve_video_path(video_url, video_file_path)
        temp_path = path if delete_temp else None
        result = _execute_upload(
            video_path=path,
            title=title,
            description=description,
            tags=tags,
            category_id=category_id,
            privacy_status=privacy,
            made_for_kids=made_for_kids,
        )
        return _json_response(result)
    except Exception as exc:
        logger.exception("upload_youtube_video failed")
        return _json_response({"error": f"{type(exc).__name__}: {exc}"})
    finally:
        if delete_temp and temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


@mcp.tool()
async def publish_youtube_video(
    title: Annotated[str, Field(description="Video title.")],
    video_url: Annotated[Optional[str], Field(default=None)] = None,
    video_file_path: Annotated[Optional[str], Field(default=None)] = None,
    description: Annotated[str, Field(default="")] = "",
    tags: Annotated[Optional[List[str]], Field(default=None)] = None,
    privacy_status: Annotated[str, Field(default="")] = "",
    category_id: Annotated[Optional[str], Field(default=None)] = None,
    made_for_kids: Annotated[bool, Field(default=False)] = False,
    confirm_publish: Annotated[bool, Field(default=False)] = False,
) -> str:
    """Download video_url or use local path, upload to YouTube, return watch URL."""
    return await upload_youtube_video(
        title=title,
        video_url=video_url,
        video_file_path=video_file_path,
        description=description,
        tags=tags,
        privacy_status=privacy_status,
        category_id=category_id,
        made_for_kids=made_for_kids,
        confirm_publish=confirm_publish,
    )
