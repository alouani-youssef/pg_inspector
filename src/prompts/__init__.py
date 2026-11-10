"""MCP prompts exposed by the server. Register them with `register_prompts(mcp)`."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import MCPServer

from src.prompts.templates import (
    audit_database_security,
    explore_database,
    generate_sql_query,
    investigate_data_quality,
    investigate_database_performance,
)


@dataclass(frozen=True, slots=True)
class PromptSpec:
    fn: Callable[..., Any]
    title: str

    @property
    def name(self) -> str:
        return self.fn.__name__

    @property
    def description(self) -> str:
        doc = (self.fn.__doc__ or "").strip()
        return " ".join(doc.split("\n\n", 1)[0].split())


# The full catalogue, in listing order.
PROMPTS: list[PromptSpec] = [
    PromptSpec(explore_database, "Explore the database"),
    PromptSpec(generate_sql_query, "Generate and validate a SQL query"),
    PromptSpec(investigate_database_performance, "Investigate database performance"),
    PromptSpec(audit_database_security, "Audit database security"),
    PromptSpec(investigate_data_quality, "Investigate data quality"),
]


def register_prompts(mcp: MCPServer) -> list[PromptSpec]:
    """Register every prompt in `PROMPTS`. Returns the specs that were registered."""
    for spec in PROMPTS:
        mcp.prompt(name=spec.name, title=spec.title, description=spec.description)(
            spec.fn
        )
    return PROMPTS


__all__ = ["PROMPTS", "PromptSpec", "register_prompts"]
