"""Tools called through MCPServer, exactly as an MCP client reaches them.

Checks the contract a client relies on: input schemas, argument validation, structured
output, and that every *expected* failure is an anticipated `ToolError` (the model sees the
message) rather than a crash (`UnexpectedToolError`: the model sees only a generic error).
"""

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from src.tools import TOOLS
from src.tools.common import READ_ONLY
from src.tools.registry import DISCOVERY_TOOL


async def call(mcp, name: str, **arguments):
    result = await mcp.call_tool(name, arguments)
    assert result.is_error is False
    json.dumps(result.structured_content)  # what goes over the wire must be JSON
    return result.structured_content


async def call_error(mcp, name: str, **arguments) -> str:
    with pytest.raises(ToolError) as info:
        await mcp.call_tool(name, arguments)
    assert not isinstance(info.value, UnexpectedToolError), (
        f"{name} crashed instead of reporting a ToolError: {info.value.__cause__!r}"
    )
    return str(info.value)


# --------------------------------------------------------------------------- schemas (no DB)


async def test_every_tool_is_listed_read_only_with_a_description(mcp):
    tools = {t.name: t for t in await mcp.list_tools()}
    assert set(tools) == {s.name for s in TOOLS} | {DISCOVERY_TOOL}
    for tool in tools.values():
        assert tool.annotations == READ_ONLY
        assert tool.description, f"{tool.name} has no description"
        assert tool.output_schema is not None, f"{tool.name} has no structured output"


async def test_input_schemas(mcp):
    schemas = {t.name: t.input_schema for t in await mcp.list_tools()}

    execute = schemas["query_execute"]
    assert execute["required"] == ["sql"]
    assert execute["properties"]["limit"]["minimum"] == 1
    assert execute["properties"]["limit"]["default"] == 100

    explain = schemas["query_explain"]
    assert explain["properties"]["format"]["enum"] == ["text", "json"]
    assert explain["properties"]["analyze"]["default"] is False

    describe = schemas["catalog_describe_table"]
    assert describe["required"] == ["table"]
    assert describe["properties"]["schema"]["default"] == "public"

    assert schemas["server_ping"]["properties"] == {}


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("query_execute", {}, "sql"),
        ("query_execute", {"sql": "SELECT 1", "limit": 0}, "greater than or equal to 1"),
        ("query_execute", {"sql": "SELECT 1", "limit": -5}, "greater than or equal to 1"),
        ("query_execute", {"sql": "SELECT 1", "limit": "lots"}, "valid integer"),
        ("query_execute", {"sql": None}, "valid string"),
        ("query_explain", {"sql": "SELECT 1", "format": "xml"}, "'text' or 'json'"),
        ("query_explain", {"sql": "SELECT 1", "analyze": "maybe"}, "valid boolean"),
        ("catalog_describe_table", {}, "table"),
        ("catalog_list_schemas", {"include_system": "sure"}, "valid boolean"),
        (DISCOVERY_TOOL, {"group": "admin"}, "server"),
    ],
)
async def test_invalid_arguments_are_rejected_before_running(mcp, tool, arguments, message):
    assert message in await call_error(mcp, tool, **arguments)


async def test_unknown_tool(mcp):
    assert await call_error(mcp, "query_drop_everything") == "Unknown tool: query_drop_everything"


async def test_ping(mcp):
    assert await call(mcp, "server_ping") == {"result": "pong"}


async def test_rejected_sql_reaches_the_client_as_a_tool_error(mcp):
    message = await call_error(mcp, "query_execute", sql="DROP TABLE customers")
    assert "Query rejected (statement_not_allowed): DROP statements" in message


async def test_discovery_lists_what_is_exposed(mcp):
    out = await call(mcp, DISCOVERY_TOOL, tag="sql")
    assert [t["name"] for t in out["tools"]] == ["query_execute", "query_explain"]


# --------------------------------------------------------------------------- end to end (DB)

db = pytest.mark.usefixtures("db")


@db
async def test_catalog_tools_end_to_end(mcp):
    dbs = (await call(mcp, "catalog_list_databases"))["result"]
    assert any(d["is_current"] for d in dbs)

    tables = (await call(mcp, "catalog_list_tables", schema="public"))["result"]
    assert len(tables) == 13

    t = await call(mcp, "catalog_describe_table", table="orders")
    assert t["kind"] == "table"
    assert [c["name"] for c in t["columns"]][:3] == ["id", "customer_id", "status"]
    assert next(c for c in t["columns"] if c["name"] == "status")["type"] == "order_status"
    assert "orders_shipped_after_placed" in {c["name"] for c in t["constraints"]}


@db
async def test_query_tools_end_to_end(mcp):
    out = await call(mcp, "query_execute", sql="SELECT sum(amount) FROM payments WHERE order_id <= 10")
    assert out["truncated"] is False and out["row_count"] == 1
    assert isinstance(out["rows"][0][0], str)  # numeric stays exact

    plan = await call(mcp, "query_explain", sql="SELECT * FROM countries", format="json")
    assert plan["format"] == "json" and plan["analyzed"] is False


@db
@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        ("query_execute", {"sql": "SELECT 1/0"}, "22012: division by zero"),
        ("query_explain", {"sql": "SELECT * FROM nope"}, '42P01: relation "nope" does not exist'),
        ("catalog_describe_table", {"table": "nope"}, "Relation public.nope not found."),
        ("catalog_list_schemas", {"database": "nope"}, "Database 'nope' does not exist."),
    ],
)
async def test_database_failures_are_tool_errors_not_crashes(mcp, tool, arguments, expected):
    assert expected in await call_error(mcp, tool, **arguments)
