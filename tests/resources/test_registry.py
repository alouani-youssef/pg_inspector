"""Every resource is listed with the metadata a client displays, and fails cleanly."""

import pytest
from mcp.server.mcpserver.exceptions import ResourceError, UnexpectedResourceError
from sqlalchemy import text

from src.resources import RESOURCES, dictionary, health, overview


async def test_every_resource_is_listed_as_json_with_a_description(mcp):
    listed = {str(r.uri): r for r in await mcp.list_resources()}
    assert list(listed) == [
        "pg-inspector://database/overview",
        "pg-inspector://database/dictionary",
        "pg-inspector://database/health",
    ]
    for spec in RESOURCES:
        resource = listed[spec.uri]
        assert resource.name == spec.name
        assert resource.title == spec.title
        assert resource.mime_type == "application/json"
        assert resource.description and "\n" not in resource.description


@pytest.mark.usefixtures("db")
@pytest.mark.parametrize(
    ("module", "query", "uri"),
    [
        (overview, "_EXTENSIONS", overview.OVERVIEW_URI),
        (dictionary, "_ENUMS", dictionary.DICTIONARY_URI),
        (health, "_BY_STATE", health.HEALTH_URI),
    ],
)
async def test_database_failure_is_a_resource_error_not_a_crash(
    mcp, monkeypatch, module, query, uri
):
    monkeypatch.setattr(module, query, text("SELECT 1/0"))
    with pytest.raises(ResourceError) as info:
        await mcp.read_resource(uri)
    assert not isinstance(info.value, UnexpectedResourceError), info.value.__cause__
    assert "22012: division by zero" in str(info.value)
