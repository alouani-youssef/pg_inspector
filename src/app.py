from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

mcp = MCPServer(name="pg-inspector")


@mcp.tool()
def ping() -> str:
    """Health check tool."""
    return "pong"


@mcp.custom_route("/", methods=["GET"])
async def root(_: Request) -> JSONResponse:
    return JSONResponse({"message": "MCP is super cool"})


# Standalone Starlette ASGI app (its lifespan starts the MCP session manager).
# MCP endpoint is served at /mcp.
app = mcp.streamable_http_app(streamable_http_path="/mcp")
