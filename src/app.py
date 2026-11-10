from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer
from starlette.exceptions import HTTPException

from src.auth import PROTECTED_PATH, ApiKeyMiddleware, validate_api_key
from src.config import settings
from src.database import Database, database
from src.resources import register_resources
from src.routes import http_error, readiness_checks, register_routes
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
        "pg-inspector://database/health to check connections, locks, vacuum and wraparound."
    ),
    version="1.0.0",
    lifespan=lifespan,
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

# Non-MCP HTTP routes: /health, /ready, /metrics and the OAuth protected-resource metadata.
register_routes(mcp)
readiness_checks["postgres"] = database.ping

# Standalone Starlette ASGI app (its lifespan starts the MCP session manager).
# MCP endpoint is served at /mcp.
app = mcp.streamable_http_app(streamable_http_path=PROTECTED_PATH)
app.add_exception_handler(HTTPException, http_error)

# Only /mcp requires the API key; /health, /ready and /metrics stay open.
app.add_middleware(
    ApiKeyMiddleware,
    # Validated here so a missing/short key stops the import, not the first request.
    api_key=validate_api_key(settings.API_KEY.get_secret_value()),
    protected_path=PROTECTED_PATH,
    public_url=settings.PUBLIC_URL,
)
