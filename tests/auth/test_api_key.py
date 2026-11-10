"""API key guard: only /mcp requires the key; other routes stay open."""

import json

import pytest
from starlette.types import Message, Receive, Scope, Send

from src.auth import ApiKeyMiddleware, validate_api_key

KEY = "test-key-0123456789abcdef"


async def inner_app(scope: Scope, receive: Receive, send: Send) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def request(
    path: str, headers: dict[str, str] | None = None
) -> tuple[int, dict, bytes]:
    guard = ApiKeyMiddleware(inner_app, api_key=KEY, protected_path="/mcp")
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "scheme": "http",
        "server": ("inspector.local", 80),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await guard(scope, receive, send)
    start = sent[0]
    body = b"".join(m.get("body", b"") for m in sent[1:])
    response_headers = {k.decode(): v.decode() for k, v in start["headers"]}
    return start["status"], response_headers, body


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": f"Bearer {KEY}"},
        {"Authorization": f"bearer {KEY}"},
        {"X-API-Key": KEY},
    ],
)
async def test_correct_key_reaches_mcp(headers):
    status, _, body = await request("/mcp", headers)
    assert status == 200 and body == b"ok"


async def test_missing_key_is_rejected():
    status, headers, body = await request("/mcp")
    assert status == 401
    assert headers["www-authenticate"] == (
        'Bearer resource_metadata="http://inspector.local/.well-known/oauth-protected-resource/mcp"'
    )
    assert json.loads(body) == {"error": {"status": 401, "message": "Missing API key"}}


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer wrong-key"},
        {"Authorization": f"Bearer {KEY}x"},
        {"Authorization": f"Basic {KEY}"},
        {"X-API-Key": KEY[:-1]},
    ],
)
async def test_wrong_key_is_rejected(headers):
    status, _, body = await request("/mcp", headers)
    assert status == 401
    assert json.loads(body)["error"]["status"] == 401


async def test_subpaths_of_mcp_are_protected():
    status, _, _ = await request("/mcp/")
    assert status == 401


async def test_challenge_uses_public_url():
    guard = ApiKeyMiddleware(
        inner_app, api_key=KEY, public_url="https://db.example.com"
    )
    sent = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}
    await guard(scope, None, send)  # type: ignore[arg-type]
    headers = dict(sent[0]["headers"])
    assert headers[b"www-authenticate"] == (
        b'Bearer resource_metadata="https://db.example.com/.well-known/oauth-protected-resource/mcp"'
    )


@pytest.mark.parametrize(
    "path",
    [
        "/health",
        "/ready",
        "/metrics",
        "/mcpx",
        "/.well-known/oauth-protected-resource/mcp",
    ],
)
async def test_other_routes_need_no_key(path):
    status, _, _ = await request(path)
    assert status == 200


async def test_non_http_scopes_pass_through():
    called = []

    async def app(scope, receive, send):
        called.append(scope["type"])

    await ApiKeyMiddleware(app, api_key=KEY)({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert called == ["lifespan"]


@pytest.mark.parametrize("bad", ["", "short"])
def test_server_refuses_empty_or_short_key(bad):
    with pytest.raises(RuntimeError, match="API_KEY"):
        validate_api_key(bad)
    with pytest.raises(RuntimeError, match="API_KEY"):
        ApiKeyMiddleware(inner_app, api_key=bad)
