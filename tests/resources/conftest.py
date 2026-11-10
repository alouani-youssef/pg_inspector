"""Fixtures for resource tests: resources read through MCPServer, exactly as a client reads them.

Integration tests run against the seeded PostgreSQL from `docker compose up db` and are
skipped when it is unreachable (see tests/tools/conftest.py).
"""

import json
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer

from src.resources import register_resources
from tests.tools.conftest import db  # noqa: F401  (re-exported fixture)


@pytest.fixture
def mcp() -> MCPServer:
    server = MCPServer(name="test")
    register_resources(server)
    return server


async def read_json(mcp: MCPServer, uri: str) -> Any:
    [content] = await mcp.read_resource(uri)
    assert content.mime_type == "application/json"
    assert isinstance(content.content, str)
    return json.loads(content.content)
