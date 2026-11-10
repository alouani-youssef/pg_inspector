import uvicorn

from src.config import settings


def run_http() -> None:
    """Serve MCP over streamable HTTP at /mcp (API key required), plus /health, /ready, /metrics."""
    uvicorn.run(
        "src.app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.RELOAD,
    )
