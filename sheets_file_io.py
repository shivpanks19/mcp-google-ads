"""
Load tabular data for Google Sheets uploads without shipping huge JSON through MCP tool args.

Supports local file paths (when MCP runs beside the workspace), base64/gzip payloads
(for Cursor Cloud → remote Render MCP), and markdown table parsing.
"""

from __future__ import annotations

import base64
import csv
import gzip
import io
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

DEFAULT_ALLOWED_PATHS = "/tmp,/workspace,/Users"
DEFAULT_MAX_CELL_CHARS = 49_000
DEFAULT_BATCH_ROWS = 500


def max_cell_chars() -> int:
    raw = os.environ.get("GOOGLE_SHEETS_MAX_CELL_CHARS", str(DEFAULT_MAX_CELL_CHARS)).strip()
    try:
        return max(100, int(raw))
    except ValueError:
        return DEFAULT_MAX_CELL_CHARS


def batch_row_limit() -> int:
    raw = os.environ.get("GOOGLE_SHEETS_APPEND_BATCH_ROWS", str(DEFAULT_BATCH_ROWS)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_BATCH_ROWS


def allowed_path_roots() -> List[Path]:
    raw = os.environ.get("GOOGLE_SHEETS_ALLOWED_PATHS", DEFAULT_ALLOWED_PATHS)
    roots: List[Path] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            roots.append(Path(part).expanduser().resolve())
    return roots or [Path("/tmp").resolve()]


def resolve_allowed_file_path(file_path: str) -> Path:
    """Resolve path and ensure it sits under an allowlisted root."""
    if not file_path or not str(file_path).strip():
        raise ValueError("file_path is required")
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    for root in allowed_path_roots():
        if path == root or root in path.parents:
            return path
    raise ValueError(
        f"file_path not under allowed roots ({', '.join(str(r) for r in allowed_path_roots())}): {path}"
    )


def truncate_cell(value: Any, *, max_chars: Optional[int] = None) -> Any:
    limit = max_chars if max_chars is not None else max_cell_chars()
    if value is None:
        return ""
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def truncate_rows(rows: List[List[Any]], *, max_chars: Optional[int] = None) -> List[List[Any]]:
    return [[truncate_cell(c, max_chars=max_chars) for c in row] for row in rows]


def decode_base64_payload(data_base64: str, *, gzip_compressed: bool = False) -> bytes:
    raw = base64.b64decode(data_base64, validate=False)
    if gzip_compressed:
        raw = gzip.decompress(raw)
    return raw


def load_rows_from_file(path: Path, fmt: str) -> List[List[Any]]:
    fmt_norm = (fmt or "auto").strip().lower()
    if fmt_norm == "auto":
        suffix = path.suffix.lower()
        if suffix == ".csv":
            fmt_norm = "csv"
        elif suffix == ".ndjson":
            fmt_norm = "ndjson"
        elif suffix in (".md", ".markdown"):
            fmt_norm = "markdown"
        else:
            fmt_norm = "json_rows"

    text = path.read_text(encoding="utf-8")

    if fmt_norm == "json_rows":
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("json_rows file must be a JSON array of rows")
        return _normalize_rows(parsed)

    if fmt_norm == "ndjson":
        rows: List[List[Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            rows.append(item if isinstance(item, list) else [item])
        return _normalize_rows(rows)

    if fmt_norm == "csv":
        reader = csv.reader(io.StringIO(text))
        return _normalize_rows(list(reader))

    if fmt_norm == "markdown":
        tables = parse_markdown_tables(text)
        if not tables:
            raise ValueError("No markdown tables found in file")
        if len(tables) > 1:
            raise ValueError(
                f"File has {len(tables)} markdown tables; use push_markdown_tables_to_sheet or split the file"
            )
        t0 = tables[0]
        out: List[List[Any]] = []
        if t0.get("title"):
            out.append([t0["title"]])
        if t0.get("headers"):
            out.append(t0["headers"])
        out.extend(t0.get("rows") or [])
        return _normalize_rows(out)

    raise ValueError(f"Unsupported format: {fmt}")


def load_rows_from_base64(
    data_base64: str,
    fmt: str,
    *,
    gzip_compressed: bool = False,
) -> List[List[Any]]:
    raw_bytes = decode_base64_payload(data_base64, gzip_compressed=gzip_compressed)
    text = raw_bytes.decode("utf-8")
    fmt_norm = (fmt or "json_rows").strip().lower()

    if fmt_norm == "json_rows":
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("json_rows payload must be a JSON array of rows")
        return _normalize_rows(parsed)

    if fmt_norm == "ndjson":
        rows: List[List[Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            rows.append(item if isinstance(item, list) else [item])
        return _normalize_rows(rows)

    if fmt_norm == "csv":
        reader = csv.reader(io.StringIO(text))
        return _normalize_rows(list(reader))

    if fmt_norm == "markdown":
        tables = parse_markdown_tables(text)
        if not tables:
            raise ValueError("No markdown tables found in payload")
        if len(tables) > 1:
            raise ValueError("Payload has multiple markdown tables; use push_markdown_tables_to_sheet")
        t0 = tables[0]
        out: List[List[Any]] = []
        if t0.get("title"):
            out.append([t0["title"]])
        if t0.get("headers"):
            out.append(t0["headers"])
        out.extend(t0.get("rows") or [])
        return _normalize_rows(out)

    raise ValueError(f"Unsupported format: {fmt}")


def _normalize_rows(rows: List[Any]) -> List[List[Any]]:
    out: List[List[Any]] = []
    for row in rows:
        if isinstance(row, list):
            out.append(["" if c is None else c for c in row])
        else:
            out.append(["" if row is None else row])
    return out


def _split_markdown_row(line: str) -> List[str]:
    """Split a markdown table row on | while respecting \\| escapes."""
    cells: List[str] = []
    buf: List[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    if line.startswith("|"):
        cells = cells[1:]
    if line.endswith("|") and cells:
        cells = cells[:-1]
    return cells


def _is_separator_row(line: str) -> bool:
    stripped = line.strip()
    if "|" not in stripped:
        return False
    return bool(re.fullmatch(r"[|\s:\-]+", stripped))


def parse_markdown_tables(markdown: str) -> List[Dict[str, Any]]:
    """
    Extract markdown pipe tables. Handles escaped pipes (\\|) in cells.
    Associates a preceding ##/### heading as table title when present.
    """
    lines = markdown.splitlines()
    tables: List[Dict[str, Any]] = []
    i = 0
    last_heading: Optional[str] = None

    while i < len(lines):
        line = lines[i]
        heading = re.match(r"^#{1,6}\s+(.+)$", line.strip())
        if heading:
            last_heading = heading.group(1).strip()
            i += 1
            continue

        if "|" not in line:
            i += 1
            continue

        header_line = line
        if i + 1 >= len(lines) or not _is_separator_row(lines[i + 1]):
            i += 1
            continue

        headers = _split_markdown_row(header_line)
        i += 2  # skip header + separator
        rows: List[List[str]] = []
        while i < len(lines):
            row_line = lines[i]
            if not row_line.strip() or "|" not in row_line:
                break
            if _is_separator_row(row_line):
                i += 1
                continue
            rows.append(_split_markdown_row(row_line))
            i += 1

        if headers:
            tables.append({"title": last_heading, "headers": headers, "rows": rows})
            last_heading = None

    return tables


def split_rows_into_batches(rows: List[List[Any]], batch_size: Optional[int] = None) -> List[List[List[Any]]]:
    size = batch_size or batch_row_limit()
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def estimate_payload_chars(rows: List[List[Any]]) -> int:
    return len(json.dumps(rows, default=str))
