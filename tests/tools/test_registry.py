import pytest
from mcp.server.mcpserver import MCPServer

from src.config import Settings
from src.tools import TOOLS, register_tools, select_tools
from src.tools.registry import DISCOVERY_TOOL, META_GROUP, META_TAGS, ToolGroup


def names(specs) -> list[str]:
    return [s.name for s in specs]


# --------------------------------------------------------------------------- select_tools


def test_no_filters_selects_everything():
    assert names(select_tools(TOOLS)) == names(TOOLS)


def test_group_filter():
    assert names(select_tools(TOOLS, groups=["query"])) == ["query_execute", "query_explain"]


def test_include_adds_tool_outside_enabled_groups():
    selected = names(select_tools(TOOLS, groups=["catalog"], include=["query_explain"]))
    assert "query_explain" in selected and "query_execute" not in selected
    assert "catalog_describe_table" in selected


def test_exclude_wins_over_group():
    selected = names(select_tools(TOOLS, exclude=["query_execute"]))
    assert "query_execute" not in selected and "query_explain" in selected


@pytest.mark.parametrize(
    "kwargs",
    [
        {"groups": ["nope"]},
        {"include": ["drop_database"]},
        {"exclude": ["drop_database"]},
        {"include": ["server_ping"], "exclude": ["server_ping"]},
        {"exclude": ["ping"]},  # short names are not accepted,
    ],
)
def test_invalid_configuration_fails_fast(kwargs):
    with pytest.raises(ValueError):
        select_tools(TOOLS, **kwargs)


def test_every_tool_has_a_group():
    assert {s.group for s in TOOLS} == set(ToolGroup)


# --------------------------------------------------------------------------- settings


def test_settings_parse_comma_separated(monkeypatch):
    monkeypatch.setenv("TOOL_GROUPS", "catalog, query")
    monkeypatch.setenv("TOOLS_EXCLUDE", "query_execute")
    monkeypatch.setenv("TOOLS_INCLUDE", "")
    s = Settings(_env_file=None)
    assert s.TOOL_GROUPS == ["catalog", "query"]
    assert s.TOOLS_EXCLUDE == ["query_execute"]
    assert s.TOOLS_INCLUDE == []


# --------------------------------------------------------------------------- registration


async def test_registration_publishes_meta_and_discovery():
    mcp = MCPServer(name="test")
    register_tools(mcp, groups=["catalog"])
    tools = {t.name: t for t in await mcp.list_tools()}

    assert set(tools) == {
        "catalog_list_databases",
        "catalog_list_schemas",
        "catalog_list_tables",
        "catalog_describe_table",
        DISCOVERY_TOOL,
    }
    assert tools["catalog_describe_table"].title == "Catalog: Describe table"
    assert tools["catalog_describe_table"].meta == {META_GROUP: "catalog", META_TAGS: ["structure", "table"]}
    assert tools[DISCOVERY_TOOL].meta[META_GROUP] == "server"


async def test_discover_tools_filters():
    mcp = MCPServer(name="test")
    catalogue = register_tools(mcp, exclude=["server_ping"])
    discover = next(s.fn for s in catalogue if s.name == DISCOVERY_TOOL)

    full = await discover()
    assert [g.name for g in full.groups] == ["server", "catalog", "query"]
    assert "server_ping" not in names(full.tools)
    assert all(t.name.startswith(f"{t.group}_") for t in full.tools)

    assert names((await discover(group=ToolGroup.QUERY)).tools) == ["query_execute", "query_explain"]
    assert names((await discover(tag="table")).tools) == ["catalog_list_tables", "catalog_describe_table"]
    assert names((await discover(search="EXECUTION PLAN")).tools) == ["query_explain"]
