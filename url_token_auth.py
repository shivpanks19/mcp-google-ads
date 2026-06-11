"""URL-token access control for hosted MCP HTTP endpoints."""

from __future__ import annotations

import hmac
import os
from urllib.parse import parse_qs


TOKEN_ENV_VAR = "MCP_URL_AUTH_TOKEN"
QUERY_PARAM = "token"
PUBLIC_PATHS = {"/", "/health"}
PROTECTED_PATHS = {"/mcp", "/sse", "/messages"}


def configured_token() -> str:
    """Return the configured URL auth token, or an empty string when disabled."""
    return (os.environ.get(TOKEN_ENV_VAR) or "").strip()


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS


def is_protected_path(path: str) -> bool:
    normalized = "/" + path.strip("/")
    return normalized in PROTECTED_PATHS or any(
        normalized.startswith(f"{protected}/") for protected in PROTECTED_PATHS
    )


def query_token(query_string: bytes) -> str:
    parsed = parse_qs(query_string.decode("utf-8", errors="ignore"), keep_blank_values=True)
    values = parsed.get(QUERY_PARAM) or []
    return values[0].strip() if values else ""


def path_token_and_rewritten_path(path: str) -> tuple[str, str]:
    """
    Support /<token>/mcp style URLs by extracting the leading token and rewriting
    the request path to the MCP endpoint expected by FastMCP.
    """
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2 and f"/{parts[1]}" in PROTECTED_PATHS:
        token = parts[0]
        rewritten = "/" + "/".join(parts[1:])
        return token, rewritten
    return "", path


def token_matches(expected: str, supplied: str) -> bool:
    return bool(expected) and hmac.compare_digest(expected, supplied)


class UrlTokenAuthMiddleware:
    """ASGI middleware that protects MCP endpoints with a URL token."""

    def __init__(self, app, token: str | None = None):
        self.app = app
        self.token = configured_token() if token is None else token.strip()

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not self.token:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if is_public_path(path):
            await self.app(scope, receive, send)
            return

        path_token, rewritten_path = path_token_and_rewritten_path(path)
        protected = is_protected_path(rewritten_path)
        supplied = query_token(scope.get("query_string", b"")) or path_token

        if protected and not token_matches(self.token, supplied):
            await self._send_unauthorized(send)
            return

        if protected and rewritten_path != path:
            scope = dict(scope)
            scope["path"] = rewritten_path
            scope["raw_path"] = rewritten_path.encode("ascii")

        await self.app(scope, receive, send)

    async def _send_unauthorized(self, send) -> None:
        body = b'{"error":"unauthorized"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
