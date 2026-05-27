"""
HTTP MCP entrypoint for PaaS (Render, Railway, etc.).

Uses streamable-http so clients can POST to /mcp (Cursor remote MCP).
Listen host/port come from google_ads_server (PORT env on Render → 0.0.0.0).

Local stdio: python google_ads_server.py
Local HTTP:   python main.py  → http://127.0.0.1:8000/mcp
"""
from starlette.requests import Request
from starlette.responses import JSONResponse

from google_ads_server import mcp  # noqa: E402


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
    return JSONResponse(
        {
            "service": "mcp-google-ads",
            "mcp_endpoint": "/mcp",
            "health": "/health",
            "docs": "https://github.com/shivpanks19/mcp-google-ads",
        }
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
