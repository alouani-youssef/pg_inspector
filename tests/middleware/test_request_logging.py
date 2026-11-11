"""The debug request logger: client IP, labels, inputs, results, errors, truncation."""

import logging
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver import MCPServer

from src.middleware import RequestLogger, register_middleware
from src.middleware.request_logging import client_ip


def make_ctx(method="tools/call", params=None, request_id=1, request=None):
    return SimpleNamespace(
        method=method, params=params, request_id=request_id, request=request
    )


def http_request(host="10.0.0.7", headers=None):
    return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers or {})


def test_client_ip_sources():
    assert client_ip(make_ctx()) == "stdio"
    assert client_ip(make_ctx(request=http_request())) == "10.0.0.7"
    forwarded = http_request(headers={"x-forwarded-for": "203.0.113.5, 10.0.0.1"})
    assert client_ip(make_ctx(request=forwarded)) == "203.0.113.5"


async def test_logs_tool_call_inputs_and_result(caplog):
    caplog.set_level(logging.INFO, "pg_inspector.requests")
    ctx = make_ctx(
        params={"name": "query_execute", "arguments": {"sql": "select 1"}},
        request=http_request(),
    )

    async def call_next(_):
        return {"content": [], "isError": False}

    assert await RequestLogger()(ctx, call_next) == {"content": [], "isError": False}
    sent, received = caplog.messages
    assert sent == '→ 10.0.0.7 #1 tools/call query_execute args={"sql": "select 1"}'
    assert received.startswith("← 10.0.0.7 #1 tools/call query_execute ok ")
    assert 'result={"content": [], "isError": false}' in received


async def test_marks_tool_errors_and_truncates(caplog):
    caplog.set_level(logging.INFO, "pg_inspector.requests")

    async def call_next(_):
        return {"content": [{"type": "text", "text": "x" * 100}], "isError": True}

    await RequestLogger(max_chars=20)(make_ctx(params={"name": "t"}), call_next)
    assert " tool-error " in caplog.messages[1]
    assert "… (+" in caplog.messages[1]


async def test_logs_and_reraises_exceptions(caplog):
    caplog.set_level(logging.INFO, "pg_inspector.requests")

    async def call_next(_):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await RequestLogger()(
            make_ctx(method="resources/read", params={"uri": "x://y"}), call_next
        )
    assert caplog.messages[0] == "→ stdio #1 resources/read x://y"
    assert "failed" in caplog.messages[1] and "boom" in caplog.messages[1]


def test_registered_only_when_enabled():
    off, on = MCPServer(name="off"), MCPServer(name="on")
    register_middleware(off, log_requests=False, max_chars=10)
    register_middleware(on, log_requests=True, max_chars=10)
    assert not any(isinstance(m, RequestLogger) for m in off.middleware)
    assert any(isinstance(m, RequestLogger) for m in on.middleware)
