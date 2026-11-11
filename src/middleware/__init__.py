"""MCP middleware wrapping every inbound message (see `MCPServer.middleware`)."""

import logging

from mcp.server.mcpserver import MCPServer

from src.middleware.request_logging import RequestLogger, logger


def register_middleware(mcp: MCPServer, *, log_requests: bool, max_chars: int) -> None:
    """Install the debug request logger when `log_requests` is set."""
    if not log_requests:
        return
    # The root level may be WARNING (stdio) - make sure these INFO lines get through.
    logger.setLevel(logging.INFO)
    mcp.middleware.append(RequestLogger(max_chars=max_chars))


__all__ = ["RequestLogger", "register_middleware"]
