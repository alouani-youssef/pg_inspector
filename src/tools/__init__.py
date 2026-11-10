"""MCP tools exposed by the server. Register them with `register_tools(mcp)`."""

from collections.abc import Iterable

from mcp.server.mcpserver import MCPServer

from src.tools.common import READ_ONLY
from src.tools.databases import list_databases
from src.tools.queries import execute_query, explain_query
from src.tools.registry import (
    ToolGroup,
    ToolSpec,
    make_discover_tools,
    select_tools,
)
from src.tools.schemas import list_schemas
from src.tools.server import ping
from src.tools.tables import describe_table, list_tables

# The full catalogue, in listing order. Exposed name: `<group>_<short name>` (see ToolSpec.name).
TOOLS: list[ToolSpec] = [
    ToolSpec(ping, "Ping", ToolGroup.SERVER, frozenset({"health"})),
    ToolSpec(
        list_databases,
        "List databases",
        ToolGroup.CATALOG,
        frozenset({"database", "list"}),
    ),
    ToolSpec(
        list_schemas, "List schemas", ToolGroup.CATALOG, frozenset({"schema", "list"})
    ),
    ToolSpec(
        list_tables, "List tables", ToolGroup.CATALOG, frozenset({"table", "list"})
    ),
    ToolSpec(
        describe_table,
        "Describe table",
        ToolGroup.CATALOG,
        frozenset({"table", "structure"}),
    ),
    ToolSpec(
        execute_query,
        "Execute read-only query",
        ToolGroup.QUERY,
        frozenset({"sql", "read"}),
        "execute",
    ),
    ToolSpec(
        explain_query,
        "Explain query plan",
        ToolGroup.QUERY,
        frozenset({"sql", "performance"}),
        "explain",
    ),
]


def register_tools(
    mcp: MCPServer,
    *,
    groups: Iterable[str] | None = None,
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
) -> list[ToolSpec]:
    """Register the selected tools (see `select_tools`) plus `discover_tools`, all read-only.
    Returns the specs that were registered."""
    exposed = select_tools(TOOLS, groups=groups, include=include, exclude=exclude)
    # The discovery tool lists itself, so it sees the final list (filled in below).
    catalogue: list[ToolSpec] = []
    discovery = ToolSpec(
        make_discover_tools(catalogue),
        "Discover tools",
        ToolGroup.SERVER,
        frozenset({"discovery"}),
        "discover_tools",
    )
    catalogue.extend([*exposed, discovery])

    for spec in catalogue:
        mcp.add_tool(
            spec.fn,
            name=spec.name,
            title=spec.display_title,
            annotations=READ_ONLY,
            meta=spec.meta,
        )
    return catalogue


__all__ = ["TOOLS", "ToolGroup", "ToolSpec", "register_tools", "select_tools"]
