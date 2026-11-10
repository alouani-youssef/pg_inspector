"""API key guard for the MCP endpoint.

Pure ASGI middleware (not `BaseHTTPMiddleware`, which buffers and can break the
streamable-HTTP responses MCP relies on). Only paths under `protected_path` are checked;
everything else (/health, /ready, /metrics) passes through untouched.

Clients send the key as `Authorization: Bearer <key>` or `X-API-Key: <key>`.
"""

import hmac

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

MIN_API_KEY_LENGTH = 16


def validate_api_key(api_key: str) -> str:
    """Return the key if it is usable, otherwise raise so the server refuses to start."""
    if not api_key:
        raise RuntimeError(
            "API_KEY is not set. Generate one with "
            '`python -c "import secrets; print(secrets.token_urlsafe(32))"` and add it to .env.'
        )
    if len(api_key) < MIN_API_KEY_LENGTH:
        raise RuntimeError(
            f"API_KEY must be at least {MIN_API_KEY_LENGTH} characters long."
        )
    return api_key


def extract_api_key(headers: Headers) -> str | None:
    """The key the client sent, from `Authorization: Bearer` or `X-API-Key`."""
    authorization = headers.get("authorization")
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
    api_key = headers.get("x-api-key")
    return api_key.strip() if api_key and api_key.strip() else None


class ApiKeyMiddleware:
    """Reject requests to the protected path unless they carry the expected API key."""

    def __init__(
        self, app: ASGIApp, api_key: str, protected_path: str = "/mcp"
    ) -> None:
        self.app = app
        self._expected = validate_api_key(api_key).encode()
        self._path = protected_path.rstrip("/")

    def _is_protected(self, path: str) -> bool:
        return path == self._path or path.startswith(self._path + "/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._is_protected(scope["path"]):
            await self.app(scope, receive, send)
            return

        received = extract_api_key(Headers(scope=scope))
        if received is None:
            await self._unauthorized("Missing API key")(scope, receive, send)
            return
        if not hmac.compare_digest(received.encode(), self._expected):
            await self._unauthorized("Invalid API key")(scope, receive, send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _unauthorized(message: str) -> JSONResponse:
        return JSONResponse(
            {"error": {"status": 401, "message": message}},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
