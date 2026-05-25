#!/usr/bin/env python3
"""
Fetch campaign performance (same GAQL as get_campaign_performance) and optionally save to Supabase.

Requires:
  - .env with Google Ads credentials + GOOGLE_ADS_DEVELOPER_TOKEN
  - SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (secret key) when using --persist

Usage (repo root):
  .venv/bin/python scripts/sync_campaign_performance_to_supabase.py --customer-id 1234567890 --days 7
  .venv/bin/python scripts/sync_campaign_performance_to_supabase.py --customer-id 1234567890 --preset monthly --persist
  .venv/bin/python scripts/sync_campaign_performance_to_supabase.py -c 1234567890 --preset weekly --persist --summary "Q2 check-in"

Lookback resolution (first match wins):
  1) --days N (any positive integer up to 365; overrides --preset for the GAQL window)
  2) else --preset weekly|monthly|quarterly|yearly
  3) else env SYNC_CAMPAIGN_PERFORMANCE_DAYS (integer, same cap)
  4) else default 30

Set SYNC_CAMPAIGN_PERFORMANCE_DAYS in your shell or .env so you can run with only -c and --persist.

By default this script sets the ``google_ads_server`` (and ``httpx``) loggers to WARNING so you only see
the progress line on stderr and the report on stdout. Use ``-v`` / ``--verbose`` for full INFO logs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

_PRESET_DAYS = {
    "weekly": 7,
    "monthly": 30,
    "quarterly": 90,
    "yearly": 365,
}
_MAX_LOOKBACK = 365


def resolve_lookback_days(
    days: int | None,
    preset: str | None,
    *,
    default: int = 30,
    max_days: int = _MAX_LOOKBACK,
) -> int:
    """Resolve LAST_N_DAYS from CLI and optional env (used when days and preset are unset)."""
    if days is not None:
        n = int(days)
    elif preset:
        n = _PRESET_DAYS[preset]
    else:
        raw = (os.environ.get("SYNC_CAMPAIGN_PERFORMANCE_DAYS") or "").strip()
        if raw.isdigit():
            n = int(raw)
        else:
            n = int(default)
    if n < 1:
        n = 1
    if n > max_days:
        n = max_days
    return n


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch campaign performance and optionally persist report_snapshots to Supabase.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s -c 1234567890 --days 14\n"
            "  %(prog)s -c 1234567890 --preset quarterly --persist\n"
            "  export SYNC_CAMPAIGN_PERFORMANCE_DAYS=10\n"
            "  %(prog)s -c 1234567890 --persist\n"
            "  %(prog)s -c 1234567890 --days 7 -v\n"
        ),
    )
    parser.add_argument(
        "-c",
        "--customer-id",
        required=True,
        help="Google Ads customer ID (10 digits, dashes optional)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Lookback days for LAST_N_DAYS (1–%d). Overrides --preset when set. "
            "If omitted, use --preset or env SYNC_CAMPAIGN_PERFORMANCE_DAYS or default 30."
            % _MAX_LOOKBACK
        ),
    )
    parser.add_argument(
        "--preset",
        choices=tuple(_PRESET_DAYS.keys()),
        default=None,
        help=(
            "Rolling window shorthand: weekly=7, monthly=30, quarterly=90, yearly=365 (not calendar ISO). "
            "Ignored when --days is set."
        ),
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Insert report_snapshots after fetch (requires Supabase env vars).",
    )
    parser.add_argument(
        "--summary",
        default=None,
        help="Optional summary text stored on the snapshot row",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Print only the JSON result (table + persist outcome), not the ASCII table first.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print the progress line to stderr right before googleAds:search.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show google_ads_server / httpx INFO logs (default is quiet: WARNING and above only).",
    )
    args = parser.parse_args()

    # Configure logging *before* importing google_ads_server so its import-time INFO lines respect this.
    _log_fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    if args.verbose:
        if not logging.root.handlers:
            logging.basicConfig(level=logging.INFO, format=_log_fmt)
        else:
            logging.getLogger().setLevel(logging.INFO)
        logging.getLogger("google_ads_server").setLevel(logging.INFO)
        logging.getLogger("httpx").setLevel(logging.INFO)
    else:
        if not logging.root.handlers:
            logging.basicConfig(level=logging.WARNING, format=_log_fmt)
        else:
            logging.getLogger().setLevel(logging.WARNING)
        logging.getLogger("google_ads_server").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)

    days = resolve_lookback_days(args.days, args.preset)

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    # Import after logging.basicConfig so google_ads_server's own basicConfig is a no-op and levels stick.
    import google_ads_server as gas  # noqa: F401
    from google_ads_server import fetch_campaign_performance_table_and_rows

    async def run() -> dict:
        import supabase_store as store

        if not args.quiet:
            print(
                f"Fetching campaign performance via googleAds:search (LAST_{days}_DAYS), "
                f"customer {args.customer_id}...",
                file=sys.stderr,
                flush=True,
            )

        data = await fetch_campaign_performance_table_and_rows(args.customer_id, days)
        out: dict = {
            "ok": data["ok"],
            "days": days,
            "preset": args.preset,
            "days_source": (
                "cli_days"
                if args.days is not None
                else ("cli_preset" if args.preset else "env_or_default")
            ),
        }
        if not data["ok"]:
            out["error"] = data["error"]
            return out
        out["table"] = data["table"]
        out["row_count"] = len(data["rows"])
        if args.persist:
            if not store.is_configured():
                out["persist"] = {
                    "status": "skipped",
                    "message": "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set.",
                }
            elif not data["rows"]:
                out["persist"] = {
                    "status": "skipped",
                    "message": "No API rows to snapshot.",
                }
            else:
                out["persist"] = store.persist_campaign_performance_snapshot(
                    customer_id=data["formatted_customer_id"],
                    days=days,
                    api_results=data["rows"],
                    summary=args.summary,
                )
        return out

    result = asyncio.run(run())
    if args.json_only:
        print(json.dumps(result, indent=2, default=str))
    else:
        if result.get("table"):
            print(result["table"])
        if args.persist and "persist" in result:
            print("\n---\nPersist:", json.dumps(result["persist"], indent=2, default=str))

    if not result.get("ok"):
        raise SystemExit(1)
    if args.persist and result.get("persist", {}).get("status") not in (None, "ok", "skipped"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
