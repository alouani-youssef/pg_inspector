"""Catalog tools against the seeded database (see the bottom of data/db_init.sql)."""

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.config import settings
from src.tools.databases import list_databases
from src.tools.schemas import list_schemas
from src.tools.tables import describe_table, list_tables

pytestmark = pytest.mark.usefixtures("db")

TABLES = {
    "categories",
    "countries",
    "coupons",
    "customers",
    "order_items",
    "orders",
    "payments",
    "product_price_changes",
    "products",
    "reviews",
}
VIEWS = {"active_products", "customer_order_summary"}
MATVIEWS = {"monthly_revenue"}


# --------------------------------------------------------------------------- list_databases


async def test_list_databases():
    dbs = {d.name: d for d in await list_databases()}

    assert settings.DATABASE_DB in dbs and "postgres" in dbs
    assert not {"template0", "template1"} & set(dbs), "templates must be hidden"
    assert [d.name for d in dbs.values() if d.is_current] == [settings.DATABASE_DB]

    current = dbs[settings.DATABASE_DB]
    assert current.owner == settings.DATABASE_USERNAME
    assert current.encoding == "UTF8"
    assert current.size_bytes is not None and current.size_bytes > 0


async def test_list_databases_is_sorted():
    names = [d.name for d in await list_databases()]
    assert names == sorted(names)


# --------------------------------------------------------------------------- list_schemas


async def test_list_schemas_defaults_to_configured_database():
    # The tool documents "Defaults to the server's configured database".
    schemas = await list_schemas()
    assert [s.name for s in schemas] == ["public"]


async def test_list_schemas_user_schemas_only():
    schemas = await list_schemas(database=settings.DATABASE_DB)
    assert [s.name for s in schemas] == ["public"]
    assert schemas[0].comment == "pg_inspector seed: e-commerce tables, reporting views and audit log"


async def test_list_schemas_include_system():
    names = {s.name for s in await list_schemas(include_system=True, database=settings.DATABASE_DB)}
    assert {"public", "pg_catalog", "information_schema", "pg_toast"} <= names


async def test_list_schemas_of_another_database():
    names = [s.name for s in await list_schemas(database="postgres")]
    assert names == ["public"]


@pytest.mark.parametrize("name", ["does_not_exist", "template0", "APP"])
async def test_list_schemas_rejects_unknown_or_unconnectable_database(name):
    with pytest.raises(ToolError, match=f"Database '{name}' does not"):
        await list_schemas(database=name)


# --------------------------------------------------------------------------- list_tables


async def test_list_tables_returns_every_relation_with_its_kind():
    tables = await list_tables()
    by_kind: dict[str, set[str]] = {}
    for t in tables:
        assert t.schema_name == "public"
        by_kind.setdefault(t.kind, set()).add(t.name)

    assert by_kind == {"table": TABLES, "view": VIEWS, "materialized view": MATVIEWS}
    assert [t.name for t in tables] == sorted(t.name for t in tables)


async def test_list_tables_estimates_and_sizes():
    tables = {t.name: t for t in await list_tables(schema="public")}

    # Fresh ANALYZE in the seed: estimates match the real counts.
    assert tables["customers"].estimated_rows == 200
    assert tables["orders"].estimated_rows == 2000
    assert tables["order_items"].estimated_rows == 4001
    assert tables["customers"].total_size_bytes > 0
    assert tables["active_products"].total_size_bytes == 0  # plain views have no storage
    assert tables["customers"].comment == "Registered shoppers"
    assert tables["reviews"].comment is None


async def test_list_tables_for_a_system_schema():
    names = {t.name for t in await list_tables(schema="pg_catalog")}
    assert {"pg_class", "pg_namespace"} <= names


async def test_list_tables_unknown_schema_is_empty():
    assert await list_tables(schema="nope") == []


# --------------------------------------------------------------------------- describe_table


async def test_describe_customers_columns():
    t = await describe_table("customers")
    assert (t.schema_name, t.name, t.kind) == ("public", "customers", "table")
    assert t.comment == "Registered shoppers"
    assert t.estimated_rows == 200
    assert t.view_definition is None

    cols = {c.name: c for c in t.columns}
    assert list(cols) == [
        "id", "email", "first_name", "last_name", "full_name",
        "country_code", "is_active", "preferences", "created_at",
    ]  # fmt: skip
    assert cols["id"].type == "bigint" and cols["id"].identity == "always"
    assert cols["id"].default is None and not cols["id"].nullable
    assert cols["email"].type == "email"  # the domain, not its base type
    assert cols["email"].comment == "Login email, unique per customer"
    assert cols["full_name"].generated == "((first_name || ' '::text) || last_name)"
    assert cols["full_name"].default is None and cols["full_name"].nullable
    assert cols["country_code"].type == "character(2)"
    assert cols["is_active"].default == "true"
    assert cols["preferences"].default == "'{}'::jsonb"
    assert cols["created_at"].type == "timestamp with time zone"
    assert cols["created_at"].default == "now()"


async def test_describe_customers_constraints_indexes_and_references():
    t = await describe_table("customers", schema="public")

    assert [(c.type, c.name) for c in t.constraints] == [
        ("primary key", "customers_pkey"),
        ("unique", "customers_email_key"),
        ("foreign key", "customers_country_code_fkey"),
    ]
    fk = t.constraints[2]
    assert fk.definition == "FOREIGN KEY (country_code) REFERENCES countries(code)"

    indexes = {i.name: i for i in t.indexes}
    assert t.indexes[0].name == "customers_pkey" and t.indexes[0].is_primary
    assert set(indexes) == {
        "customers_pkey",
        "customers_email_key",
        "customers_country_idx",
        "customers_lower_last_idx",
        "customers_preferences_gin",
    }
    assert indexes["customers_email_key"].is_unique and not indexes["customers_email_key"].is_primary
    assert "USING gin (preferences)" in indexes["customers_preferences_gin"].definition
    assert all(i.is_valid and i.size_bytes > 0 for i in t.indexes)

    assert {(r.from_table, r.name) for r in t.referenced_by} == {
        ("orders", "orders_customer_id_fkey"),
        ("reviews", "reviews_customer_id_fkey"),
    }
    assert t.triggers == []


async def test_describe_products_triggers_checks_and_partial_index():
    t = await describe_table("products")

    assert {(tr.name, tr.enabled) for tr in t.triggers} == {
        ("products_price_audit", True),
        ("products_touch_updated_at", True),
    }
    assert "AFTER UPDATE OF price" in next(
        tr.definition for tr in t.triggers if tr.name == "products_price_audit"
    )
    checks = [c.definition for c in t.constraints if c.type == "check"]
    assert checks == ["CHECK (stock >= 0)"]

    partial = next(i for i in t.indexes if i.name == "products_active_idx")
    assert partial.definition.endswith("WHERE (NOT discontinued)")

    cols = {c.name: c for c in t.columns}
    assert cols["id"].identity == "by default"
    assert cols["tags"].type == "text[]"
    assert cols["sku"].type == "character varying(32)"
    assert cols["price"].type == "positive_money"


async def test_describe_composite_primary_key_and_generated_column():
    t = await describe_table("order_items")
    pk = next(c for c in t.constraints if c.type == "primary key")
    assert pk.definition == "PRIMARY KEY (order_id, line_no)"
    cols = {c.name: c for c in t.columns}
    assert cols["line_total"].generated == "((quantity)::numeric * (unit_price)::numeric)"
    assert {c.name for c in t.constraints if c.type == "foreign key"} == {
        "order_items_order_id_fkey",
        "order_items_product_id_fkey",
    }


async def test_describe_self_referencing_table():
    t = await describe_table("categories")
    assert [r.from_table for r in t.referenced_by] == ["categories", "products"]


async def test_describe_empty_table():
    t = await describe_table("coupons")
    assert t.comment == "Intentionally empty in the seed data"
    assert [c.name for c in t.columns] == ["code", "percent_off", "valid_until"]


async def test_describe_view():
    t = await describe_table("active_products")
    assert t.kind == "view"
    assert [c.name for c in t.columns] == ["id", "sku", "name", "category", "price", "stock"]
    assert t.view_definition is not None and "NOT p.discontinued" in t.view_definition
    assert t.constraints == [] and t.indexes == [] and t.triggers == []


async def test_describe_materialized_view():
    t = await describe_table("monthly_revenue")
    assert t.kind == "materialized view"
    assert t.view_definition is not None and "date_trunc" in t.view_definition
    assert [i.name for i in t.indexes] == ["monthly_revenue_month_idx"]
    assert t.indexes[0].is_unique


@pytest.mark.parametrize(
    ("table", "schema"),
    [
        ("nope", "public"),
        ("Customers", "public"),  # names are case-sensitive
        ('"customers"', "public"),  # and unquoted
        ("customers", "pg_catalog"),  # wrong schema
        ("customers_pkey", "public"),  # an index is not a relation we describe
        ("order_status", "public"),  # nor is a type
    ],
)
async def test_describe_unknown_relation(table, schema):
    with pytest.raises(ToolError, match=r"not found\. Use catalog_list_tables"):
        await describe_table(table, schema=schema)


async def test_describe_handles_injection_attempts_as_plain_names():
    with pytest.raises(ToolError, match="not found"):
        await describe_table("customers'; DROP TABLE customers; --")
    assert (await describe_table("customers")).name == "customers"
