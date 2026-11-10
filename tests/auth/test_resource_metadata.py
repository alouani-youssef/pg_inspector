"""OAuth Protected Resource Metadata (RFC 9728) for /mcp."""

import json

import pytest
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.routing import Route

from src.auth import protected_resource_metadata, resource_metadata_url
from src.config import settings
from src.routes import ALLOWED_CUSTOM_ROUTES, register_routes

PATHS = [
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
]


def get(path: str, host: str = "inspector.local:8000") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("inspector.local", 8000),
            "path": path,
            "root_path": "",
            "query_string": b"",
            "headers": [(b"host", host.encode())],
        }
    )


def endpoint(path: str):
    server = MCPServer(name="test")
    register_routes(server)
    routes = {
        r.path: r for r in server.streamable_http_app().routes if isinstance(r, Route)
    }
    return routes[path].endpoint


def test_metadata_document():
    assert protected_resource_metadata("https://db.example.com/") == {
        "resource": "https://db.example.com/mcp",
        "bearer_methods_supported": ["header"],
        "resource_name": "PostgreSQL Inspector",
    }
    assert (
        resource_metadata_url("https://db.example.com")
        == "https://db.example.com/.well-known/oauth-protected-resource/mcp"
    )


@pytest.mark.parametrize("path", PATHS)
def test_routes_are_allowed(path):
    assert path in ALLOWED_CUSTOM_ROUTES


@pytest.mark.parametrize("path", PATHS)
async def test_endpoint_derives_url_from_request(path, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_URL", "")
    response = await endpoint(path)(get(path))
    assert response.status_code == 200
    assert json.loads(response.body)["resource"] == "http://inspector.local:8000/mcp"


@pytest.mark.parametrize("path", PATHS)
async def test_endpoint_prefers_public_url(path, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_URL", "https://db.example.com")
    response = await endpoint(path)(get(path))
    assert json.loads(response.body)["resource"] == "https://db.example.com/mcp"
