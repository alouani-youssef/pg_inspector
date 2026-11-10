"""The database overview resource against the seeded database."""

import pytest

from src.resources.overview import OVERVIEW_URI
from tests.resources.conftest import read_json
from tests.tools.conftest import USER_SCHEMAS

pytestmark = pytest.mark.usefixtures("db")


async def test_overview_of_the_seeded_database(mcp):
    out = await read_json(mcp, OVERVIEW_URI)

    db = out["database"]
    assert db["name"] == "app"
    assert db["size_bytes"] > 0
    assert db["connections"] >= 1
    assert int(db["server_version"].split(".")[0]) >= 13

    schemas = {s["name"]: s for s in out["schemas"]}
    assert sorted(schemas) == USER_SCHEMAS
    public = schemas["public"]
    assert public["comment"].startswith("pg_inspector seed")
    assert (public["tables"], public["views"], public["materialized_views"]) == (
        10,
        2,
        1,
    )
    assert public["foreign_tables"] == 0
    assert public["sequences"] >= 1 and public["functions"] >= 1
    assert public["types"] >= 3  # order_status enum + email and positive_money domains

    extensions = {e["name"]: e for e in out["extensions"]}
    assert {"plpgsql", "pg_stat_statements", "pg_trgm", "citext"} <= set(extensions)
    assert extensions["pg_stat_statements"]["schema_name"] == "extensions"

    largest = out["largest_relations"]
    assert 0 < len(largest) <= 10
    sizes = [r["total_size_bytes"] for r in largest]
    assert sizes == sorted(sizes, reverse=True)
    assert largest[0]["name"] == "events"  # analytics.events, 200k rows (data/mocks)
    assert {"order_items", "orders"} <= {r["name"] for r in largest}
