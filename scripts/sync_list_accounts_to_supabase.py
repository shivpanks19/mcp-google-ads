#!/usr/bin/env python3
"""
Call list_accounts (Google Ads API) and upsert accessible customer IDs into Supabase.

Requires:
  - .env with Google Ads credentials + GOOGLE_ADS_DEVELOPER_TOKEN
  - SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
  - supabase/migrations/001_initial.sql applied in your Supabase project

Usage (repo root):
  .venv/bin/python scripts/sync_list_accounts_to_supabase.py
  .venv/bin/python scripts/sync_list_accounts_to_supabase.py --notes "weekly seed"
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync list_accounts → Supabase google_ads_clients.")
    parser.add_argument("--notes", default=None, help="Optional notes on each upserted client row")
    args = parser.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    import asyncio

    from google_ads_server import list_accounts
    import supabase_store as store

    listing = asyncio.run(list_accounts())
    out = store.sync_list_accounts_output_to_clients(listing, notes=args.notes)
    print(json.dumps(out, indent=2, default=str))
    if out.get("status") not in ("ok",):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
