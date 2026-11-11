"""The transport-agnostic MCP server: metadata, lifespan, tools, resources, prompts and routes.

Transports wrap this object: `src.app` serves it over streamable HTTP, `src.transports.stdio`
over stdin/stdout. Nothing here requires the API key, so stdio mode runs without one.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer

from src.config import settings
from src.database import Database, database
from src.middleware import register_middleware
from src.prompts import register_prompts
from src.resources import register_resources
from src.routes import readiness_checks, register_routes
from src.tools import register_tools


@asynccontextmanager
async def lifespan(_: MCPServer) -> AsyncIterator[Database]:
    """Open the PostgreSQL pool on startup and close it on shutdown.
    Tools can reach it via `ctx.request_context.lifespan_context`."""
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


# The MCP Server Meta Data
mcp = MCPServer(
    name="postgres-inspector",
    title="PostgreSQL Inspector",
    description="An MCP server for PostgreSQL database inspection.",
    instructions=(
        "Use this server to inspect databases, schemas, tables and execute read-only queries. "
        "Tool names are prefixed by domain (server_, catalog_, query_); "
        "call server_discover_tools to browse them by group, tag or keyword. "
        "Resources: read pg-inspector://database/overview first (version, size, per-schema "
        "object counts, extensions, largest tables); pg-inspector://database/dictionary before "
        "writing SQL (columns, comments, enum values, keys and join paths); and "
        "pg-inspector://database/health to check connections, locks, vacuum and wraparound. "
        "Prompts provide guided workflows: explore_database, generate_sql_query, "
        "investigate_database_performance, audit_database_security, investigate_data_quality."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# Debug logging of every call: client IP, tool, inputs, result. Off unless DEBUG_LOG_REQUESTS.
register_middleware(
    mcp,
    log_requests=settings.DEBUG_LOG_REQUESTS,
    max_chars=settings.DEBUG_LOG_MAX_CHARS,
)

# Inspector tools grouped by domain, filtered by TOOL_GROUPS / TOOLS_INCLUDE / TOOLS_EXCLUDE.
register_tools(
    mcp,
    groups=settings.TOOL_GROUPS,
    include=settings.TOOLS_INCLUDE,
    exclude=settings.TOOLS_EXCLUDE,
)

# Read-only resources: database overview, semantic dictionary, health snapshot.
register_resources(mcp)

# Guided workflows: exploration, SQL generation, performance, security and data quality.
register_prompts(mcp)

# Non-MCP HTTP routes: /health, /ready, /metrics and the OAuth protected-resource metadata.
# They are only served by the HTTP transport; registering them is harmless under stdio.
register_routes(mcp)
readiness_checks["postgres"] = database.ping
