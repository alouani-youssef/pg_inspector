"""Tool catalogue: domain groups, tags, startup filtering and the discovery tool.

Every tool belongs to exactly one domain group and carries free-form tags. MCP has no
native grouping, so the group is made visible where clients actually display it: the tool
name is prefixed with it (`catalog_list_tables`) and so is the title (`Catalog: List tables`). Operators
choose what the server exposes with `TOOL_GROUPS`, `TOOLS_INCLUDE` and `TOOLS_EXCLUDE`;
clients discover what is exposed through each tool's `_meta` and the `server_discover_tools` tool.
"""

from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, Field


class ToolGroup(StrEnum):
    """Domain a tool belongs to."""

    SERVER = "server"
    CATALOG = "catalog"
    QUERY = "query"


GROUP_DESCRIPTIONS: dict[ToolGroup, str] = {
    ToolGroup.SERVER: "Server diagnostics and tool discovery.",
    ToolGroup.CATALOG: "Browse databases, schemas, tables and their structure.",
    ToolGroup.QUERY: "Run read-only SQL and inspect execution plans.",
}

# `_meta` keys published on every tool (namespaced per the MCP `_meta` convention).
META_GROUP = "pg-inspector/group"
META_TAGS = "pg-inspector/tags"

# The discovery tool is always exposed, whatever the filters say.
DISCOVERY_TOOL = "server_discover_tools"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    fn: Callable[..., Any]
    title: str
    group: ToolGroup
    tags: frozenset[str] = field(default_factory=frozenset)
    # Name within the group; defaults to the function name.
    short_name: str | None = None

    @property
    def name(self) -> str:
        """Exposed tool name: `<group>_<short name>`."""
        return f"{self.group.value}_{self.short_name or self.fn.__name__}"

    @property
    def display_title(self) -> str:
        return f"{self.group.value.capitalize()}: {self.title}"

    @property
    def description(self) -> str:
        doc = (self.fn.__doc__ or "").strip()
        return doc.split("\n\n", 1)[0].replace("\n", " ")

    @property
    def meta(self) -> dict[str, Any]:
        return {META_GROUP: self.group.value, META_TAGS: sorted(self.tags)}


def select_tools(
    specs: Sequence[ToolSpec],
    *,
    groups: Iterable[str] | None = None,
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
) -> list[ToolSpec]:
    """Return the specs to expose, in catalogue order.

    A tool is exposed when its group is enabled (`groups` None/empty = every group) or it is
    named in `include`, and it is not named in `exclude`. Unknown group or tool names raise
    `ValueError` so a typo in configuration fails at startup instead of silently hiding tools.
    """
    known_names = {s.name for s in specs}
    group_set = _validated(groups or (), {g.value for g in ToolGroup}, "tool group")
    include_set = _validated(include, known_names, "tool")
    exclude_set = _validated(exclude, known_names, "tool")
    if both := include_set & exclude_set:
        raise ValueError(f"Tools both included and excluded: {sorted(both)}")

    return [
        s
        for s in specs
        if (not group_set or s.group in group_set or s.name in include_set)
        and s.name not in exclude_set
    ]


def _validated(values: Iterable[str], known: set[str], kind: str) -> set[str]:
    chosen = {v.strip() for v in values if v.strip()}
    if unknown := chosen - known:
        raise ValueError(f"Unknown {kind}(s) {sorted(unknown)}; known: {sorted(known)}")
    return chosen


# --------------------------------------------------------------------------- discovery


class ToolSummary(BaseModel):
    name: str
    title: str
    group: str
    tags: list[str]
    description: str


class GroupSummary(BaseModel):
    name: str
    description: str
    tools: list[str]


class ToolCatalogue(BaseModel):
    groups: list[GroupSummary] = Field(description="Enabled groups with their tool names.")
    tools: list[ToolSummary] = Field(description="Tools matching the filters.")


def make_discover_tools(exposed: Sequence[ToolSpec]) -> Callable[..., Awaitable[ToolCatalogue]]:
    """Build the discovery tool over the specs actually exposed by this server."""

    async def discover_tools(
        group: Annotated[
            ToolGroup | None, Field(description="Only tools in this domain group.")
        ] = None,
        tag: Annotated[
            str | None, Field(description="Only tools carrying this tag.")
        ] = None,
        search: Annotated[
            str | None,
            Field(description="Case-insensitive text matched against name, title and description."),
        ] = None,
    ) -> ToolCatalogue:
        """List the tools this server exposes, grouped by domain, optionally filtered by group, tag or text."""
        needle = (search or "").casefold()
        matches = [
            s
            for s in exposed
            if (group is None or s.group == group)
            and (tag is None or tag in s.tags)
            and (not needle or needle in f"{s.name} {s.title} {s.description}".casefold())
        ]
        groups = [
            GroupSummary(
                name=g.value,
                description=GROUP_DESCRIPTIONS[g],
                tools=[s.name for s in exposed if s.group == g],
            )
            for g in ToolGroup
            if any(s.group == g for s in exposed)
        ]
        return ToolCatalogue(
            groups=groups,
            tools=[
                ToolSummary(
                    name=s.name,
                    title=s.display_title,
                    group=s.group.value,
                    tags=sorted(s.tags),
                    description=s.description,
                )
                for s in matches
            ],
        )

    return discover_tools
