"""
HTTP MCP entrypoint for PaaS (Render, Railway, etc.).

Uses streamable-http so clients can POST to /mcp (Cursor remote MCP).
Set MCP_URL_AUTH_TOKEN to require /mcp?token=... or /<token>/mcp.
Listen host/port come from google_ads_server (PORT env on Render → 0.0.0.0).

Local stdio: python google_ads_server.py
Local HTTP:   python main.py  → http://127.0.0.1:8000/mcp
"""
from starlette.requests import Request
from starlette.responses import JSONResponse

from google_ads_server import mcp  # noqa: E402
from url_token_auth import TOKEN_ENV_VAR, UrlTokenAuthMiddleware, configured_token  # noqa: E402


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    """Render / load-balancer health check (no auth)."""
    import os

    login_raw = (os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or "").strip()
    login_digits = "".join(c for c in login_raw if c.isdigit())
    sheets_id = (os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID") or "").strip()
    return JSONResponse(
        {
            "status": "ok",
            "service": "mcp-google-ads",
            "auth_type": os.environ.get("GOOGLE_ADS_AUTH_TYPE", ""),
            "login_customer_id_configured": bool(login_digits),
            "sheets_spreadsheet_configured": bool(sheets_id),
        }
    )


@mcp.custom_route("/", methods=["GET"])
async def root(_request: Request) -> JSONResponse:
    auth_enabled = bool(configured_token())
    return JSONResponse(
        {
            "service": "mcp-google-ads",
            "mcp_endpoint": "/mcp",
            "auth": {
                "enabled": auth_enabled,
                "token_env_var": TOKEN_ENV_VAR,
                "url_formats": ["/mcp?token=...", "/<token>/mcp"] if auth_enabled else [],
            },
            "health": "/health",
            "docs": "https://github.com/shivpanks19/mcp-google-ads",
        }
    )


def build_app():
    """Build the hosted HTTP app, wrapping MCP routes with URL-token auth."""
    app_factory = getattr(mcp, "streamable_http_app", None)
    if app_factory is None:
        raise RuntimeError("Installed MCP package does not expose FastMCP.streamable_http_app()")
    return UrlTokenAuthMiddleware(app_factory())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        build_app(),
        host=getattr(mcp.settings, "host", "127.0.0.1"),
        port=getattr(mcp.settings, "port", 8000),
        log_level=getattr(mcp.settings, "log_level", "INFO").lower(),
    )
