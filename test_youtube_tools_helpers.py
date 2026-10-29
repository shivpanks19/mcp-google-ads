"""Unit tests for YouTube MCP helpers (no network / no OAuth)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from youtube_tools import (
    _default_privacy,
    _resolve_video_path,
    _validate_https_url,
    reset_youtube_caches,
)


class YouTubeHelpersTest(unittest.TestCase):
    def test_validate_https_url_rejects_http(self):
        with self.assertRaises(ValueError):
            _validate_https_url("http://example.com/v.mp4")

    def test_validate_https_url_accepts_https(self):
        url = _validate_https_url("https://cdn.example.com/clips/demo.mp4")
        self.assertTrue(url.startswith("https://"))

    def test_resolve_local_path(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(b"fake")
            path = tmp.name
        try:
            resolved, delete = _resolve_video_path(None, path)
            self.assertEqual(resolved, Path(path).resolve())
            self.assertFalse(delete)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_resolve_rejects_both_sources(self):
        with self.assertRaises(ValueError):
            _resolve_video_path("https://example.com/a.mp4", "/tmp/x.mp4")

    def test_default_privacy_fallback(self):
        reset_youtube_caches()
        self.assertEqual(_default_privacy(), "unlisted")


if __name__ == "__main__":
    unittest.main()
