"""Non-MCP HTTP routes (health, readiness, metrics) mounted on the MCP server."""

from collections.abc import Awaitable, Callable

from mcp.server.mcpserver import MCPServer
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# The only non-MCP HTTP routes this server may expose.
ALLOWED_CUSTOM_ROUTES = frozenset({"/health", "/ready", "/metrics"})

# Readiness checks: async callables returning True when a dependency is usable.
# Register e.g. a PostgreSQL connectivity check here once the database layer exists.
ReadinessCheck = Callable[[], Awaitable[bool]]
readiness_checks: dict[str, ReadinessCheck] = {}


async def health(_: Request) -> JSONResponse:
    """Liveness: the process is up and serving HTTP."""
    return JSONResponse({"status": "ok"})


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


async def metrics(_: Request) -> Response:
    """Prometheus metrics in text exposition format."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


ROUTES: list[tuple[str, list[str], Callable[[Request], Awaitable[Response]]]] = [
    ("/health", ["GET"], health),
    ("/ready", ["GET"], ready),
    ("/metrics", ["GET"], metrics),
]


def register_routes(mcp: MCPServer) -> None:
    """Attach the custom routes to the MCP server, rejecting any path outside the allowlist."""
    for path, methods, handler in ROUTES:
        if path not in ALLOWED_CUSTOM_ROUTES:
            raise ValueError(f"Custom route {path!r} is not allowed; allowed: {sorted(ALLOWED_CUSTOM_ROUTES)}")
        mcp.custom_route(path, methods=methods, include_in_schema=False)(handler)
