"""Tests for sheets_file_io (markdown parsing, path allowlist, batching)."""

from __future__ import annotations

import base64
import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path

import sheets_file_io as sio


class TestMarkdownParser(unittest.TestCase):
    def test_simple_table(self) -> None:
        md = """
## Campaign performance

| Campaign | Clicks | Cost |
| --- | ---: | ---: |
| Hexa Search | 100 | 5000 |
| Search EB | 80 | 3200 |
"""
        tables = sio.parse_markdown_tables(md)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["title"], "Campaign performance")
        self.assertEqual(tables[0]["headers"], ["Campaign", "Clicks", "Cost"])
        self.assertEqual(len(tables[0]["rows"]), 2)

    def test_escaped_pipe_in_cell(self) -> None:
        md = """
| Asset | Text |
| --- | --- |
| RSA 1 | Headline with \\| pipe |
"""
        tables = sio.parse_markdown_tables(md)
        self.assertEqual(tables[0]["rows"][0][1], "Headline with | pipe")

    def test_multiple_tables(self) -> None:
        md = """
| A | B |
| --- | --- |
| 1 | 2 |

| X | Y |
| --- | --- |
| 9 | 8 |
"""
        self.assertEqual(len(sio.parse_markdown_tables(md)), 2)


class TestFileAllowlist(unittest.TestCase):
    def test_allows_file_under_tmp(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, dir="/tmp") as f:
            json.dump([["a", "b"]], f)
            path = f.name
        try:
            os.environ["GOOGLE_SHEETS_ALLOWED_PATHS"] = "/tmp"
            resolved = sio.resolve_allowed_file_path(path)
            self.assertEqual(resolved, Path(path).resolve())
        finally:
            os.unlink(path)


class TestBase64Payload(unittest.TestCase):
    def test_gzip_roundtrip(self) -> None:
        rows = [["Campaign", "Clicks"], ["A", 1], ["B", 2]]
        raw = json.dumps(rows).encode("utf-8")
        compressed = gzip.compress(raw)
        b64 = base64.b64encode(compressed).decode("ascii")
        loaded = sio.load_rows_from_base64(b64, "json_rows", gzip_compressed=True)
        self.assertEqual(loaded, rows)


class TestBatching(unittest.TestCase):
    def test_split_rows(self) -> None:
        rows = [[i] for i in range(5)]
        chunks = sio.split_rows_into_batches(rows, 2)
        self.assertEqual(chunks, [[ [0], [1] ], [ [2], [3] ], [ [4] ]])


if __name__ == "__main__":
    unittest.main()
