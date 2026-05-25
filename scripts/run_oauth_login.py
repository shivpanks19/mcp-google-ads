#!/usr/bin/env python3
"""
Run the Google Ads OAuth desktop flow (browser) and save the user token.

Prerequisites (see .env.example):
  - GOOGLE_ADS_AUTH_TYPE=oauth
  - GOOGLE_ADS_CLIENT_ID / GOOGLE_ADS_CLIENT_SECRET, and/or a client JSON at
    GOOGLE_ADS_CREDENTIALS_PATH, and/or OAuth client JSON in GOOGLE_ADS_CREDENTIALS_JSON
  - Not on Railway/headless (OAuth opens a local browser via run_local_server)

Usage (from repo root):
  .venv/bin/python scripts/run_oauth_login.py
  .venv/bin/python scripts/run_oauth_login.py --force   # drop saved user token first (full re-login)
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _token_file_is_user_credential(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    if os.path.basename(path) == "google_ads_token.json":
        return True
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
    parser = argparse.ArgumentParser(description="Run Google Ads OAuth and save token.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Remove saved user token (if detected) so Google shows login again.",
    )
    args = parser.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    # Import after path fix so google_ads_server loads from this repo
    from google_ads_server import _oauth_user_token_path, get_credentials  # noqa: PLC2701

    auth = (os.environ.get("GOOGLE_ADS_AUTH_TYPE") or "oauth").lower().strip()
    if auth != "oauth":
        print(
            "GOOGLE_ADS_AUTH_TYPE is not 'oauth'. Set it to oauth in .env to run this script.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if args.force:
        token_path = _oauth_user_token_path()
        if _token_file_is_user_credential(token_path):
            os.remove(token_path)
            print(f"Removed saved token: {token_path}")
        else:
            print(
                f"--force: no user token removed (path not a safe target: {token_path}). "
                "Delete your token file manually if you need a clean login.",
                file=sys.stderr,
            )

    get_credentials()
    print("OAuth finished. Token saved per GOOGLE_ADS_CREDENTIALS_PATH / _oauth_user_token_path().")


if __name__ == "__main__":
    main()
