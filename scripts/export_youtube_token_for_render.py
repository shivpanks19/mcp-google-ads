#!/usr/bin/env python3
"""Print authorized-user token JSON as one line for Render YOUTUBE_CREDENTIALS_JSON."""
from __future__ import annotations

import json
import os
import sys


def main() -> None:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from youtube_tools import _load_stored_user_token_dict, youtube_auth_readiness

    status = youtube_auth_readiness()
    token_dict, source = _load_stored_user_token_dict()
    if not token_dict:
        print(json.dumps(status, indent=2), file=sys.stderr)
        raise SystemExit("No YouTube user token found. Run: .venv/bin/python scripts/run_youtube_oauth_login.py")
    if not status.get("has_refresh_token"):
        print(json.dumps(status, indent=2), file=sys.stderr)
        raise SystemExit("Token missing refresh_token. Re-run with --force on run_youtube_oauth_login.py")

    print(json.dumps(token_dict, separators=(",", ":")))
    print(f"# source={source} — paste the line above into Render YOUTUBE_CREDENTIALS_JSON", file=sys.stderr)


if __name__ == "__main__":
    main()
