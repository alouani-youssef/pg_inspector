"""The database overview resource against the seeded database."""

import pytest

from src.resources.overview import OVERVIEW_URI
from tests.resources.conftest import read_json

pytestmark = pytest.mark.usefixtures("db")


async def test_overview_of_the_seeded_database(mcp):
    out = await read_json(mcp, OVERVIEW_URI)

    db = out["database"]
    assert db["name"] == "app"
    assert db["size_bytes"] > 0
    assert db["connections"] >= 1
    assert int(db["server_version"].split(".")[0]) >= 13

    [public] = out["schemas"]
    assert public["name"] == "public"
    assert public["comment"].startswith("pg_inspector seed")
    assert (public["tables"], public["views"], public["materialized_views"]) == (
        10,
        2,
        1,
    )
    assert public["foreign_tables"] == 0
    assert public["sequences"] >= 1 and public["functions"] >= 1
    assert public["types"] >= 3  # order_status enum + email and positive_money domains

    assert "plpgsql" in {e["name"] for e in out["extensions"]}

    largest = out["largest_relations"]
    assert 0 < len(largest) <= 10
    sizes = [r["total_size_bytes"] for r in largest]
    assert sizes == sorted(sizes, reverse=True)
    assert {"order_items", "orders"} <= {r["name"] for r in largest[:3]}
