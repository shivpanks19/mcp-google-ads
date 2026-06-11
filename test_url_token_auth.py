import asyncio
import unittest

from url_token_auth import UrlTokenAuthMiddleware


async def echo_app(scope, _receive, send):
    body = scope["path"].encode("ascii")
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-length", str(len(body)).encode("ascii"))],
        }
    )
    await send({"type": "http.response.body", "body": body})


def call_app(app, path="/mcp", query_string=b""):
    messages = []
    scope = {"type": "http", "path": path, "query_string": query_string}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    return messages


def response_status(messages):
    return next(message["status"] for message in messages if message["type"] == "http.response.start")


def response_body(messages):
    return b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )


class UrlTokenAuthMiddlewareTest(unittest.TestCase):
    def test_auth_disabled_allows_mcp(self):
        messages = call_app(UrlTokenAuthMiddleware(echo_app, token=""))

        self.assertEqual(response_status(messages), 200)
        self.assertEqual(response_body(messages), b"/mcp")

    def test_public_paths_do_not_require_token(self):
        app = UrlTokenAuthMiddleware(echo_app, token="secret")

        self.assertEqual(response_status(call_app(app, path="/")), 200)
        self.assertEqual(response_status(call_app(app, path="/health")), 200)

    def test_mcp_rejects_missing_or_wrong_token(self):
        app = UrlTokenAuthMiddleware(echo_app, token="secret")

        self.assertEqual(response_status(call_app(app)), 401)
        self.assertEqual(response_status(call_app(app, query_string=b"token=wrong")), 401)

    def test_mcp_allows_matching_query_token(self):
        app = UrlTokenAuthMiddleware(echo_app, token="secret")
        messages = call_app(app, query_string=b"token=secret")

        self.assertEqual(response_status(messages), 200)
        self.assertEqual(response_body(messages), b"/mcp")

    def test_mcp_allows_path_token_and_rewrites_path(self):
        app = UrlTokenAuthMiddleware(echo_app, token="secret")
        messages = call_app(app, path="/secret/mcp")

        self.assertEqual(response_status(messages), 200)
        self.assertEqual(response_body(messages), b"/mcp")


if __name__ == "__main__":
    unittest.main()
