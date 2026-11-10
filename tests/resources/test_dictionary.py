"""The semantic dictionary resource against the seeded database."""

import pytest

from src.resources import dictionary
from src.resources.dictionary import DICTIONARY_URI
from tests.resources.conftest import read_json

pytestmark = pytest.mark.usefixtures("db")

STATUSES = ["pending", "paid", "shipped", "delivered", "cancelled"]


async def relations(mcp) -> dict[str, dict]:
    out = await read_json(mcp, DICTIONARY_URI)
    assert out["truncated"] is False
    return {f"{r['schema_name']}.{r['name']}": r for r in out["relations"]}


async def test_every_user_relation_is_described(mcp):
    rels = await relations(mcp)
    assert len([k for k in rels if k.startswith("public.")]) == 13
    assert rels["public.active_products"]["kind"] == "view"
    assert rels["public.monthly_revenue"]["kind"] == "materialized view"
    assert rels["public.countries"]["comment"] == "ISO-3166 alpha-2 countries"
    assert rels["public.orders"]["estimated_rows"] == 2000


async def test_columns_carry_meaning(mcp):
    rels = await relations(mcp)
    customers = {c["name"]: c for c in rels["public.customers"]["columns"]}
    assert customers["email"]["comment"] == "Login email, unique per customer"
    assert customers["email"]["nullable"] is False
    assert customers["country_code"]["references"] == "public.countries.code"

    orders = {c["name"]: c for c in rels["public.orders"]["columns"]}
    assert orders["status"]["type"] == "order_status"
    assert orders["status"]["enum_values"] == STATUSES
    assert orders["customer_id"]["references"] == "public.customers.id"
    assert "references" not in orders["id"]  # exclude_none keeps the payload small


async def test_keys_checks_and_join_paths(mcp):
    rels = await relations(mcp)

    items = rels["public.order_items"]
    assert items["primary_key"] == ["order_id", "line_no"]
    assert "CHECK (quantity > 0)" in items["checks"]
    assert {
        (f["columns"][0], f["references_table"]) for f in items["foreign_keys"]
    } == {
        ("order_id", "public.orders"),
        ("product_id", "public.products"),
    }

    assert ["product_id", "customer_id"] in rels["public.reviews"]["unique"]

    orders_in = {r["from_table"] for r in rels["public.orders"]["referenced_by"]}
    assert {"public.order_items", "public.payments"} <= orders_in

    categories = rels["public.categories"]  # self-referencing foreign key
    assert {
        "from_table": "public.categories",
        "from_columns": ["parent_id"],
        "columns": ["id"],
    }.items() <= (categories["referenced_by"][0].items())


async def test_enums_and_domains(mcp):
    out = await read_json(mcp, DICTIONARY_URI)
    [status] = [e for e in out["enums"] if e["name"] == "order_status"]
    assert status["values"] == STATUSES
    assert status["comment"] == "Lifecycle of an order"

    domains = {d["name"]: d for d in out["domains"]}
    assert domains["email"]["base_type"] == "text"
    assert domains["positive_money"]["base_type"] == "numeric(12,2)"
    assert domains["positive_money"]["checks"] == ["CHECK (VALUE >= 0::numeric)"]


async def test_truncated_past_the_relation_cap(mcp, monkeypatch):
    monkeypatch.setattr(dictionary, "MAX_RELATIONS", 2)
    out = await read_json(mcp, DICTIONARY_URI)
    assert out["truncated"] is True
    # Relations are ordered by schema then name: the first two are in `analytics`.
    assert [r["name"] for r in out["relations"]] == ["daily_metrics", "events"]
    # Foreign keys from relations that were cut are not reported as incoming join paths:
    # analytics.events references analytics.sessions, which was cut.
    assert out["relations"][1]["foreign_keys"]
    assert all(r["referenced_by"] == [] for r in out["relations"])
