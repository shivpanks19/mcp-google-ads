"""Ensure auction insight tools are registered on the FastMCP instance."""

from __future__ import annotations

import asyncio
import unittest


class TestAuctionInsightMcpRegistration(unittest.TestCase):
    def test_tools_registered_on_import(self) -> None:
        import main  # noqa: F401 — loads google_ads_server + register_auction_insight_tools

        from google_ads_server import mcp

        names = asyncio.run(mcp.list_tools())
        tool_names = {t.name for t in names}
        self.assertIn("get_auction_insights", tool_names)
        self.assertIn("get_auction_insights_competitor_ranking", tool_names)


if __name__ == "__main__":
    unittest.main()
