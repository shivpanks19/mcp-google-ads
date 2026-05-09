"""
HTTP MCP entrypoint for PaaS (e.g. Railway / Railpack).

Railpack expects main.py or app.py when no framework is detected.
Local stdio usage: run `python google_ads_server.py` instead.
"""
import os

# Railway / Render / Fly set PORT. FastMCP SSE reads FASTMCP_PORT and FASTMCP_HOST.
# Set before importing the server so FastMCP() picks them up; also mirrored in google_ads_server.py.
if os.environ.get("PORT"):
    os.environ.setdefault("FASTMCP_PORT", os.environ["PORT"])
    os.environ.setdefault("FASTMCP_HOST", "0.0.0.0")

from google_ads_server import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run(transport="sse")
