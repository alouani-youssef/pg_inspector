"""MCP resources exposed by the server. Register them with `register_resources(mcp)`."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import Annotations

from src.resources.dictionary import DICTIONARY_URI, database_dictionary
from src.resources.health import HEALTH_URI, database_health
from src.resources.overview import OVERVIEW_URI, database_overview

JSON = "application/json"


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    fn: Callable[..., Any]
    uri: str
    name: str
    title: str
    mime_type: str = JSON
    annotations: Annotations | None = None

    @property
    def description(self) -> str:
        doc = (self.fn.__doc__ or "").strip()
        return " ".join(doc.split("\n\n", 1)[0].split())


# The full catalogue, in listing order.
RESOURCES: list[ResourceSpec] = [
    ResourceSpec(
        database_overview,
        OVERVIEW_URI,
        "database_overview",
        "Database overview",
        annotations=Annotations(audience=["user", "assistant"], priority=1.0),
    ),
    ResourceSpec(
        database_dictionary,
        DICTIONARY_URI,
        "database_dictionary",
        "Database semantic dictionary",
        annotations=Annotations(audience=["assistant"], priority=0.9),
    ),
    ResourceSpec(
        database_health,
        HEALTH_URI,
        "database_health",
        "Database health snapshot",
        annotations=Annotations(audience=["user", "assistant"], priority=0.7),
    ),
]


def register_resources(mcp: MCPServer) -> list[ResourceSpec]:
    """Register every resource in `RESOURCES`. Returns the specs that were registered."""
    for spec in RESOURCES:
        mcp.resource(
            spec.uri,
            name=spec.name,
            title=spec.title,
            description=spec.description,
            mime_type=spec.mime_type,
            annotations=spec.annotations,
        )(spec.fn)
    return RESOURCES


__all__ = ["RESOURCES", "ResourceSpec", "register_resources"]
