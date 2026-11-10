"""Fixtures for the scenario mocks (data/mocks/*.sql) loaded on top of the seed.

Integration tests: skipped when the seeded PostgreSQL is unreachable (see tests/tools/conftest.py).
"""

from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer

from src.resources import register_resources
from src.tools.queries import execute_query
from tests.tools.conftest import db  # noqa: F401  (re-exported fixture)


@pytest.fixture
def mcp() -> MCPServer:
    server = MCPServer(name="test")
    register_resources(server)
    return server


async def scalar(sql: str) -> Any:
    """Run `sql` through the query_execute tool, as a prompt-driven model would."""
    result = await execute_query(sql)
    [[value]] = result.rows
    return value
