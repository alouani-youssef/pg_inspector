"""Fixtures for tool tests.

Integration tests run against the seeded PostgreSQL from `docker compose up db`
(data/db_init.sql, whose expected values are documented at the bottom of that file).
They are skipped, not failed, when that server is unreachable.
"""

from collections.abc import AsyncIterator

import pytest
from mcp.server.mcpserver import MCPServer

from src.database import database
from src.tools import register_tools


@pytest.fixture
async def db() -> AsyncIterator[None]:
    """Connect the process-wide database for one test (engines are bound to the test's loop)."""
    await database.connect()
    try:
        if not await database.ping():
            pytest.skip("Seeded PostgreSQL is not reachable (run `docker compose up -d db`).")
        yield
    finally:
        await database.disconnect()


@pytest.fixture
def mcp() -> MCPServer:
    """A server with every tool registered, called the way an MCP client would call it."""
    server = MCPServer(name="test")
    register_tools(server)
    return server
