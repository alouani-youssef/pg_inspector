from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer
from starlette.exceptions import HTTPException

from src.database import Database, database
from src.routes import http_error, readiness_checks, register_routes


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
    instructions="Use this server to inspect databases, schemas, tables and execute read-only queries.",
    version="1.0.0",
    lifespan=lifespan,
)


@mcp.tool()
def ping() -> str:
    """Health check tool."""
    return "pong"


# Non-MCP HTTP routes: /health, /ready, /metrics.
register_routes(mcp)
readiness_checks["postgres"] = database.ping

# Standalone Starlette ASGI app (its lifespan starts the MCP session manager).
# MCP endpoint is served at /mcp.
app = mcp.streamable_http_app(streamable_http_path="/mcp")
app.add_exception_handler(HTTPException, http_error)
