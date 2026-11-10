"""MCP tools exposed by the server. Register them all with `register_tools(mcp)`."""

from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

from src.tools.common import READ_ONLY
from src.tools.databases import list_databases
from src.tools.queries import execute_query, explain_query
from src.tools.schemas import list_schemas
from src.tools.tables import describe_table, list_tables

# (function, human-readable title). The tool name is the function name.
TOOLS: list[tuple[Callable[..., Any], str]] = [
    (list_databases, "List databases"),
    (list_schemas, "List schemas"),
    (list_tables, "List tables"),
    (describe_table, "Describe table"),
    (execute_query, "Execute read-only query"),
    (explain_query, "Explain query plan"),
]


def register_tools(mcp: MCPServer) -> None:
    """Register every inspector tool on the MCP server as a read-only tool."""
    for fn, title in TOOLS:
        mcp.add_tool(fn, title=title, annotations=READ_ONLY)


__all__ = ["TOOLS", "register_tools"]
