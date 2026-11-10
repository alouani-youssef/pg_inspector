"""Helpers shared by every resource."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from sqlalchemy.ext.asyncio import AsyncConnection

from src.tools.common import db_connection


@asynccontextmanager
async def resource_connection(db: str | None = None) -> AsyncIterator[AsyncConnection]:
    """`db_connection` for resources: the same read-only connection and error mapping, but
    database failures surface as `ResourceError` so the client sees the message instead of
    a generic "Error reading resource"."""
    try:
        async with db_connection(db) as conn:
            yield conn
    except ToolError as exc:
        raise ResourceError(str(exc)) from exc
