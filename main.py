"""
HTTP MCP entrypoint for PaaS (e.g. Railway / Railpack).

Railpack expects main.py or app.py when no framework is detected.
Uses streamable-http so Cursor can POST to /mcp (Cursor remote MCP).
Listen host/port are set in google_ads_server (from PORT on Railway).
Local stdio usage: run `python google_ads_server.py` instead.
"""
from google_ads_server import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
