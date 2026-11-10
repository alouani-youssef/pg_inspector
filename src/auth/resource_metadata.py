"""OAuth 2.0 Protected Resource Metadata (RFC 9728) for the /mcp endpoint.

Only the protected-resource documents are published: this server does not run (or
delegate to) an authorization server, so `authorization_servers` is omitted (it is
optional in RFC 9728) and clients authenticate with the static API key as a bearer token.
"""

from typing import Any

PROTECTED_PATH = "/mcp"
METADATA_PATH = "/.well-known/oauth-protected-resource"
RESOURCE_NAME = "PostgreSQL Inspector"


def resource_url(base_url: str) -> str:
    """The protected resource identifier, e.g. https://host/mcp."""
    return f"{base_url.rstrip('/')}{PROTECTED_PATH}"


def resource_metadata_url(base_url: str) -> str:
    """Where the /mcp metadata lives, advertised in 401 `WWW-Authenticate` headers."""
    return f"{base_url.rstrip('/')}{METADATA_PATH}{PROTECTED_PATH}"


def protected_resource_metadata(base_url: str) -> dict[str, Any]:
    """RFC 9728 metadata for /mcp (bearer token in the Authorization header only)."""
    return {
        "resource": resource_url(base_url),
        "bearer_methods_supported": ["header"],
        "resource_name": RESOURCE_NAME,
    }
