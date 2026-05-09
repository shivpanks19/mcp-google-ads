"""
HTTP MCP entrypoint for PaaS (e.g. Railway / Railpack).

Railpack expects main.py or app.py when no framework is detected.
Local stdio usage: run `python google_ads_server.py` instead.
"""
import os

# Railway sets PORT; FastMCP SSE uses FASTMCP_PORT (see FastMCP settings).
if os.environ.get("PORT"):
    os.environ.setdefault("FASTMCP_PORT", os.environ["PORT"])

from google_ads_server import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run(transport="sse")
