"""Streamable HTTP transport: the Starlette ASGI app served by uvicorn (`src.app:app`)."""

from starlette.exceptions import HTTPException

from src.auth import PROTECTED_PATH, ApiKeyMiddleware, validate_api_key
from src.config import settings
from src.routes import http_error
from src.server import mcp

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
