"""Non-MCP HTTP routes (health, readiness, metrics) mounted on the MCP server."""

from collections.abc import Awaitable, Callable

from mcp.server.mcpserver import MCPServer
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.auth import METADATA_PATH, PROTECTED_PATH, protected_resource_metadata
from src.config import settings

OAUTH_RESOURCE_ROUTES = (METADATA_PATH, METADATA_PATH + PROTECTED_PATH)

# The only non-MCP HTTP routes this server may expose.
ALLOWED_CUSTOM_ROUTES = frozenset(
    {"/health", "/ready", "/metrics", *OAUTH_RESOURCE_ROUTES}
)

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
        # Checks are arbitrary callables: any failure means "not ready", never a 500.
        except Exception:  # noqa: BLE001
            results[name] = False
    is_ready = all(results.values())
    return JSONResponse(
        {"status": "ready" if is_ready else "not_ready", "checks": results},
        status_code=200 if is_ready else 503,
    )


async def metrics(_: Request) -> Response:
    """Prometheus metrics in text exposition format."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def oauth_protected_resource(request: Request) -> JSONResponse:
    """RFC 9728 metadata describing /mcp (served at the root and /mcp-suffixed well-known paths)."""
    base_url = settings.PUBLIC_URL or str(request.base_url)
    return JSONResponse(protected_resource_metadata(base_url))


ROUTES: list[tuple[str, list[str], Callable[[Request], Awaitable[Response]]]] = [
    ("/health", ["GET"], health),
    ("/ready", ["GET"], ready),
    ("/metrics", ["GET"], metrics),
    *((path, ["GET"], oauth_protected_resource) for path in OAUTH_RESOURCE_ROUTES),
]


def register_routes(mcp: MCPServer) -> None:
    """Attach the custom routes to the MCP server, rejecting any path outside the allowlist."""
    for path, methods, handler in ROUTES:
        if path not in ALLOWED_CUSTOM_ROUTES:
            raise ValueError(
                f"Custom route {path!r} is not allowed; allowed: {sorted(ALLOWED_CUSTOM_ROUTES)}"
            )
        mcp.custom_route(path, methods=methods, include_in_schema=False)(handler)
