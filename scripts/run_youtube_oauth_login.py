#!/usr/bin/env python3
"""Run YouTube OAuth (youtube.upload) and save the user token."""
from __future__ import annotations

import argparse
import json
import os
import sys


def _is_user_token_file(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    if "installed" in data or "web" in data:
        return False
    return "refresh_token" in data or "token" in data


def main() -> None:
    parser = argparse.ArgumentParser(description="Run YouTube OAuth and save token.")
    parser.add_argument("--force", action="store_true", help="Remove saved YouTube token first.")
    args = parser.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from youtube_tools import _youtube_token_path, get_youtube_credentials, reset_youtube_caches

    if args.force:
        token_path = _youtube_token_path()
        if _is_user_token_file(token_path):
            os.remove(token_path)
            print(f"Removed saved token: {token_path}")
        reset_youtube_caches()

    get_youtube_credentials()
    token_path = _youtube_token_path()
    print(f"YouTube OAuth finished. Token saved to: {token_path}")
    print("For hosted MCP, set YOUTUBE_CREDENTIALS_JSON to that file contents (one line).")


if __name__ == "__main__":
    main()
