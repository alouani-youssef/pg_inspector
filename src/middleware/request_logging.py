"""Debug logging of every MCP message: who called, what they invoked, with which inputs, and
what came back.

Runs as SDK `ServerMiddleware`, so it sees the JSON-RPC method and raw params of every inbound
message (tools/call, resources/read, prompts/get, initialize, ...) and the result or error the
handler produced, on both transports. Enabled with `DEBUG_LOG_REQUESTS=true`.

Log lines (logger `pg_inspector.requests`, always stderr):

    → 10.0.0.7 #3 tools/call query_execute args={"sql": "select 1"}
    ← 10.0.0.7 #3 tools/call query_execute ok 12.4ms result={"content": [...], ...}
    ✗ 10.0.0.7 #4 tools/call query_execute failed 3.1ms error=ToolError('...')

Headers are never logged (they carry the API key). Payloads longer than `max_chars` are
truncated, since a query result can be large.
"""

import json
import logging
import time
from collections.abc import Mapping
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from pydantic import BaseModel

logger = logging.getLogger("pg_inspector.requests")


def client_ip(ctx: ServerRequestContext[Any, Any]) -> str:
    """The caller's address: first `X-Forwarded-For` hop if behind a proxy, else the socket
    peer. `stdio` when there is no HTTP request (the client launched this process)."""
    request = ctx.request
    if request is None:
        return "stdio"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = getattr(request, "client", None)
    return client.host if client else "unknown"


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


def _dump(value: Any, max_chars: int) -> str:
    try:
        text = json.dumps(_to_jsonable(value), default=str, ensure_ascii=False)
    except TypeError, ValueError:
        text = repr(value)
    if max_chars and len(text) > max_chars:
        return f"{text[:max_chars]}… (+{len(text) - max_chars} chars)"
    return text


def _describe(method: str, params: Mapping[str, Any] | None) -> tuple[str, Any]:
    """A short label for the call (tool / resource / prompt name) and its inputs."""
    params = params or {}
    if method == "tools/call":
        return f"{method} {params.get('name')}", params.get("arguments") or {}
    if method == "resources/read":
        return f"{method} {params.get('uri')}", None
    if method == "prompts/get":
        return f"{method} {params.get('name')}", params.get("arguments") or {}
    return method, {k: v for k, v in params.items() if k != "_meta"} or None


class RequestLogger:
    """`ServerMiddleware` that logs each inbound message, its inputs, result and duration."""

    def __init__(self, max_chars: int = 4000) -> None:
        self.max_chars = max_chars

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        ip = client_ip(ctx)
        label, inputs = _describe(ctx.method, ctx.params)
        # Notifications have no id and no response.
        rid = "notify" if ctx.request_id is None else f"#{ctx.request_id}"
        args = "" if inputs is None else f" args={_dump(inputs, self.max_chars)}"
        logger.info("→ %s %s %s%s", ip, rid, label, args)

        start = time.perf_counter()
        try:
            result = await call_next(ctx)
        except BaseException as exc:
            elapsed = (time.perf_counter() - start) * 1000
            logger.warning(
                "✗ %s %s %s failed %.1fms error=%r", ip, rid, label, elapsed, exc
            )
            raise

        elapsed = (time.perf_counter() - start) * 1000
        if ctx.request_id is not None:
            # A tool that raised ToolError comes back as a result with isError=true.
            payload = _to_jsonable(result)
            is_error = isinstance(payload, Mapping) and bool(payload.get("isError"))
            logger.info(
                "← %s %s %s %s %.1fms result=%s",
                ip,
                rid,
                label,
                "tool-error" if is_error else "ok",
                elapsed,
                _dump(payload, self.max_chars),
            )
        return result
