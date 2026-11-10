"""Prompts are listed with their arguments, render through MCPServer the way a client gets
them, and only reference tools and resources the server actually registers."""

import re

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.types import GetPromptResult, TextContent

from src.prompts import PROMPTS, register_prompts
from src.resources import RESOURCES
from src.tools import register_tools

EXPECTED_ARGUMENTS = {
    "explore_database": {"schema_name": False, "table_name": False},
    "generate_sql_query": {"business_question": True},
    "investigate_database_performance": {"table_name": False, "query": False},
    "audit_database_security": {},
    "investigate_data_quality": {"table_name": True},
}


@pytest.fixture
def mcp() -> MCPServer:
    server = MCPServer(name="test")
    register_prompts(server)
    return server


async def render(
    mcp: MCPServer, name: str, arguments: dict[str, str] | None = None
) -> str:
    result = await mcp.get_prompt(name, arguments)
    assert isinstance(result, GetPromptResult)
    [message] = result.messages
    assert message.role == "user"
    assert isinstance(message.content, TextContent)
    return message.content.text


async def test_every_prompt_is_listed_with_its_arguments(mcp):
    listed = {p.name: p for p in await mcp.list_prompts()}
    assert list(listed) == list(EXPECTED_ARGUMENTS)
    for spec in PROMPTS:
        prompt = listed[spec.name]
        assert prompt.title == spec.title
        assert prompt.description and "\n" not in prompt.description
        args = {a.name: a.required for a in prompt.arguments or []}
        assert args == EXPECTED_ARGUMENTS[spec.name]
        assert all(a.description for a in prompt.arguments or [])


async def test_prompts_reference_only_registered_tools_and_resources(mcp):
    server = MCPServer(name="tools")
    register_tools(server)
    tools = {t.name for t in await server.list_tools()}
    resources = {spec.uri for spec in RESOURCES}

    for name, args in EXPECTED_ARGUMENTS.items():
        text = await render(
            mcp, name, {a: "x" for a, required in args.items() if required}
        )
        named_tools = set(re.findall(r"`((?:server|catalog|query)_[a-z_]+)`", text))
        assert named_tools <= tools, f"{name} names unknown tools {named_tools - tools}"
        named_uris = set(re.findall(r"pg-inspector://[a-z/]+", text))
        assert named_uris <= resources, f"{name} names unknown resources"
        assert "server_discover_tools" in text  # fallback when a tool is filtered out


async def test_explore_database_focus_follows_the_arguments(mcp):
    whole = await render(mcp, "explore_database")
    assert "whole database" in whole

    schema = await render(mcp, "explore_database", {"schema_name": "sales"})
    assert "schema `sales`" in schema

    table = await render(
        mcp, "explore_database", {"schema_name": "sales", "table_name": "orders"}
    )
    assert "table `orders` in `sales`" in table


async def test_blank_optional_arguments_are_treated_as_omitted(mcp):
    assert await render(
        mcp, "explore_database", {"schema_name": "", "table_name": " "}
    ) == (await render(mcp, "explore_database"))
    assert await render(
        mcp, "investigate_database_performance", {"table_name": "", "query": ""}
    ) == await render(mcp, "investigate_database_performance")


async def test_arguments_are_embedded_in_the_prompt(mcp):
    sql = await render(
        mcp,
        "generate_sql_query",
        {"business_question": "Find the top 10 customers by total spending."},
    )
    assert "Find the top 10 customers by total spending." in sql
    assert "`query_execute`" in sql and "`query_explain`" in sql

    perf = await render(
        mcp,
        "investigate_database_performance",
        {"query": "SELECT * FROM orders", "table_name": "orders"},
    )
    assert "```sql\nSELECT * FROM orders\n```" in perf
    assert "table `orders`" in perf

    quality = await render(
        mcp, "investigate_data_quality", {"table_name": "sales.orders"}
    )
    assert "table `sales.orders`" in quality


async def test_required_arguments_are_enforced(mcp):
    with pytest.raises(ValueError, match="business_question"):
        await mcp.get_prompt("generate_sql_query", {})
    with pytest.raises(ValueError, match="table_name"):
        await mcp.get_prompt("investigate_data_quality", {})


async def test_security_audit_never_asks_for_secrets(mcp):
    text = await render(mcp, "audit_database_security")
    assert "Never expose passwords" in text
    assert "Do not modify permissions" in text
