from collections.abc import Awaitable, Callable

from mcp.server.mcpserver import MCPServer
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# The MCP Server Meta Data
mcp = MCPServer(
    name="postgres-inspector",
    title="PostgreSQL Inspector",
    description="An MCP server for PostgreSQL database inspection.",
    instructions="Use this server to inspect databases, schemas, tables and execute read-only queries.",
    version="1.0.0",
)

# The only non-MCP HTTP routes this server may expose.
ALLOWED_CUSTOM_ROUTES = frozenset({"/health", "/ready", "/metrics"})


def custom_route(path: str, methods: list[str]):
    """Register a custom HTTP route, rejecting any path outside the allowlist."""
    if path not in ALLOWED_CUSTOM_ROUTES:
        raise ValueError(f"Custom route {path!r} is not allowed; allowed: {sorted(ALLOWED_CUSTOM_ROUTES)}")
    return mcp.custom_route(path, methods=methods, include_in_schema=False)


# Readiness checks: async callables returning True when a dependency is usable.
# Register e.g. a PostgreSQL connectivity check here once the database layer exists.
ReadinessCheck = Callable[[], Awaitable[bool]]
readiness_checks: dict[str, ReadinessCheck] = {}


@mcp.tool()
def ping() -> str:
    """Health check tool."""
    return "pong"


@custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    """Liveness: the process is up and serving HTTP."""
    return JSONResponse({"status": "ok"})


@custom_route("/ready", methods=["GET"])
async def ready(_: Request) -> JSONResponse:
    """Readiness: every registered dependency check passes."""
    results: dict[str, bool] = {}
    for name, check in readiness_checks.items():
        try:
            results[name] = await check()
        except Exception:
            results[name] = False
    is_ready = all(results.values())
    return JSONResponse(
        {"status": "ready" if is_ready else "not_ready", "checks": results},
        status_code=200 if is_ready else 503,
    )


@custom_route("/metrics", methods=["GET"])
async def metrics(_: Request) -> Response:
    """Prometheus metrics in text exposition format."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def http_error(request: Request, exc: Exception) -> Response:
    """Return JSON for unknown routes (404), wrong methods (405) and other HTTP errors."""
    assert isinstance(exc, HTTPException)
    if exc.status_code == 404:
        message = f"Route {request.url.path!r} not found"
    elif exc.status_code == 405:
        message = f"Method {request.method} not allowed on {request.url.path!r}"
    else:
        message = exc.detail
    return JSONResponse(
        {"error": {"status": exc.status_code, "message": message}},
        status_code=exc.status_code,
        headers=exc.headers,  # keeps the Allow header on 405
    )


# Standalone Starlette ASGI app (its lifespan starts the MCP session manager).
# MCP endpoint is served at /mcp.
app = mcp.streamable_http_app(streamable_http_path="/mcp")
app.add_exception_handler(HTTPException, http_error)
