"""Query tools against the seeded database: result accuracy, limits, type mapping and errors."""

import hashlib
import json
import uuid

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import text

from src.config import settings
from src.database import database
from src.tools.common import db_connection
from src.tools.queries import execute_query, explain_query

# --------------------------------------------------------------------------- rejected before the DB
# Validation runs before any connection is taken, so these need no database.


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("-- only a comment", "empty"),
        ("SELEC 1", "syntax_error"),
        ("SELECT 1; SELECT 2", "multiple_statements"),
        ("DELETE FROM orders", "statement_not_allowed"),
        ("INSERT INTO coupons VALUES ('X', 10, NULL)", "statement_not_allowed"),
        ("DROP TABLE customers", "statement_not_allowed"),
        ("SET statement_timeout = 0", "statement_not_allowed"),
        (
            "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
            "data_modifying_cte",
        ),
        ("SELECT pg_sleep(10)", "function_not_allowed"),
        ("SELECT set_config('search_path', 'x', false)", "function_not_allowed"),
        ("SELECT * FROM pg_authid", "relation_not_allowed"),
        ("SELECT * INTO copy FROM customers", "select_into"),
        ("SELECT * FROM customers FOR UPDATE", "row_locking"),
        ("SELECT '\x00'", "nul_byte"),
    ],
)
async def test_execute_query_rejects(sql, code):
    with pytest.raises(ToolError, match=rf"^Query rejected \({code}\): "):
        await execute_query(sql)


async def test_execute_query_rejects_sql_over_the_configured_length(monkeypatch):
    monkeypatch.setattr(settings, "QUERY_MAX_LENGTH", 10)
    with pytest.raises(
        ToolError, match=r"^Query rejected \(too_long\): SQL is 15 characters"
    ):
        await execute_query("SELECT 1 + 1000")


@pytest.mark.parametrize(
    ("sql", "analyze", "code"),
    [
        (
            "DELETE FROM orders",
            True,
            "statement_not_allowed",
        ),  # ANALYZE would really delete
        ("UPDATE products SET price = 0", True, "statement_not_allowed"),
        ("SHOW work_mem", False, "statement_not_allowed"),
        ("CREATE TABLE x (id int)", False, "statement_not_allowed"),
        ("SELECT 1; SELECT 2", False, "multiple_statements"),
        ("SELECT pg_sleep(1)", True, "function_not_allowed"),
    ],
)
async def test_explain_rejects(sql, analyze, code):
    with pytest.raises(ToolError, match=rf"^Query rejected \({code}\): "):
        await explain_query(sql, analyze=analyze)


# --------------------------------------------------------------------------- execute_query (DB)

db = pytest.mark.usefixtures("db")


@db
async def test_count_is_exact():
    r = await execute_query("SELECT count(*) FROM orders")
    assert [c.model_dump() for c in r.columns] == [{"name": "count", "type": "int8"}]
    assert r.rows == [[2000]]
    assert (r.row_count, r.truncated) == (1, False)


@db
async def test_aggregates_match_seed():
    r = await execute_query(
        "SELECT status::text, count(*) FROM orders GROUP BY 1 ORDER BY 1"
    )
    assert r.rows == [
        ["cancelled", 400],
        ["delivered", 400],
        ["paid", 400],
        ["pending", 400],
        ["shipped", 400],
    ]
    r = await execute_query(
        "SELECT count(*) FILTER (WHERE is_active), count(*) FILTER (WHERE NOT is_active) FROM customers"
    )
    assert r.rows == [[180, 20]]
    assert (await execute_query("SELECT count(*) FROM active_products")).rows == [[43]]
    assert (await execute_query("SELECT count(*) FROM payments")).rows == [[1200]]
    assert (await execute_query("SELECT count(*) FROM monthly_revenue")).rows == [[11]]


@db
async def test_limit_truncates_and_keeps_order():
    r = await execute_query("SELECT id FROM customers ORDER BY id", limit=10)
    assert r.rows == [[i] for i in range(1, 11)]
    assert (r.row_count, r.truncated) == (10, True)


@db
async def test_limit_equal_to_result_size_is_not_truncated():
    r = await execute_query("SELECT code FROM countries ORDER BY code", limit=5)
    assert r.rows == [["DE"], ["ES"], ["FR"], ["MA"], ["US"]]
    assert (r.row_count, r.truncated) == (5, False)


@db
async def test_default_limit_is_100():
    r = await execute_query("SELECT id FROM orders")
    assert (r.row_count, r.truncated) == (100, True)


@db
async def test_limit_is_capped_by_query_max_rows(monkeypatch):
    monkeypatch.setattr(settings, "QUERY_MAX_ROWS", 3)
    r = await execute_query("SELECT id FROM customers ORDER BY id", limit=500)
    assert (r.row_count, r.truncated) == (3, True)


@db
async def test_empty_result_still_describes_columns():
    r = await execute_query("SELECT * FROM coupons")
    assert [c.name for c in r.columns] == ["code", "percent_off", "valid_until"]
    assert [c.type for c in r.columns] == ["text", "int2", "date"]
    assert (r.rows, r.row_count, r.truncated) == ([], 0, False)


@db
async def test_values_table_and_show_are_supported():
    assert (await execute_query("VALUES (1, 'a'), (2, 'b')")).rows == [
        [1, "a"],
        [2, "b"],
    ]
    assert (await execute_query("TABLE countries", limit=1)).row_count == 1
    show = await execute_query("SHOW transaction_read_only")
    assert show.rows == [["on"]]


@db
async def test_postgres_types_are_json_safe():
    r = await execute_query("""
        SELECT p.price, p.tags, p.attributes, p.created_at, pay.id AS payment_id,
               interval '2 days' AS span, NULL::text AS nothing, true AS flag,
               '\\xdead'::bytea AS raw, int4range(1, 5) AS rng, '10.0.0.1'::inet AS ip,
               'NaN'::float8 AS nan
        FROM products p, payments pay
        WHERE p.id = 1 AND pay.order_id = 1
    """)
    row = dict(zip([c.name for c in r.columns], r.rows[0], strict=True))
    assert row["price"] == "17.50"  # numeric keeps its exact text
    assert row["tags"] == ["bestseller"]
    assert row["attributes"] == {
        "color": "white",
        "weight_g": 100,
    }  # jsonb decoded, not text
    assert row["created_at"] == "2024-06-02T00:00:00+00:00"
    assert row["payment_id"] == str(uuid.UUID(hashlib.md5(b"payment-1").hexdigest()))
    assert row["span"] == "P2D"
    assert row["nothing"] is None and row["flag"] is True
    assert row["raw"] == "\\xdead"
    assert row["rng"] == "[1,5)"
    assert row["ip"] == "10.0.0.1"
    assert row["nan"] == "nan"
    json.dumps(r.model_dump())  # the whole payload must serialise


@db
@pytest.mark.parametrize(
    ("sql", "prefix"),
    [
        ("SELECT 1/0", "22012: division by zero"),
        (
            "SELECT * FROM missing_table",
            '42P01: relation "missing_table" does not exist',
        ),
        ("SELECT nme FROM customers", '42703: column "nme" does not exist'),
        ("SELECT 'abc'::int", "22P02: invalid input syntax for type integer"),
        ("SELECT id FROM orders o JOIN customers c USING (id) WHERE x", "42703"),
    ],
)
async def test_runtime_errors_carry_sqlstate(sql, prefix):
    with pytest.raises(ToolError) as info:
        await execute_query(sql)
    assert type(info.value) is ToolError
    assert str(info.value).startswith(prefix)


@db
async def test_error_hint_is_forwarded():
    with pytest.raises(ToolError, match=r"HINT: "):
        await execute_query("SELECT frist_name FROM customers")


@db
async def test_connection_is_healthy_after_an_error():
    for _ in range(
        settings.DATABASE_POOL_SIZE + 2
    ):  # cycle through every pooled connection
        with pytest.raises(ToolError):
            await execute_query("SELECT 1/0")
        assert (await execute_query("SELECT 1")).rows == [[1]]


@db
async def test_session_refuses_writes_even_if_one_slipped_past_validation():
    # The validator cannot see inside user functions; the READ ONLY transaction is the backstop.
    with pytest.raises(
        ToolError, match="^25006: cannot execute INSERT in a read-only transaction"
    ):
        async with db_connection() as conn:
            await conn.execute(text("INSERT INTO coupons VALUES ('X', 10, NULL)"))
    assert (await execute_query("SELECT count(*) FROM coupons")).rows == [[0]]


@db
async def test_statement_timeout_is_enforced(monkeypatch):
    await database.disconnect()
    monkeypatch.setattr(settings, "DATABASE_STATEMENT_TIMEOUT_MS", 100)
    await database.connect()
    with pytest.raises(
        ToolError, match="^57014: canceling statement due to statement timeout"
    ):
        await execute_query("SELECT count(*) FROM generate_series(1, 100000000)")


# --------------------------------------------------------------------------- explain_query (DB)


@db
async def test_explain_text():
    r = await explain_query("SELECT * FROM orders WHERE customer_id = 1")
    assert (r.analyzed, r.format) == (False, "text")
    assert isinstance(r.plan, str)
    assert "orders_customer_idx" in r.plan  # selective predicate uses the index
    assert "Output:" in r.plan  # VERBOSE
    assert "actual time" not in r.plan


@db
async def test_explain_json():
    r = await explain_query("SELECT * FROM customers", format="json")
    assert r.format == "json"
    plan = r.plan if isinstance(r.plan, list) else json.loads(r.plan)
    root = plan[0]["Plan"]
    assert root["Node Type"] == "Seq Scan"
    assert root["Relation Name"] == "customers"
    assert root["Plan Rows"] == 200


@db
async def test_explain_analyze_reports_actual_rows():
    r = await explain_query(
        "SELECT * FROM orders WHERE customer_id = 1", analyze=True, format="json"
    )
    assert r.analyzed is True
    plan = r.plan if isinstance(r.plan, list) else json.loads(r.plan)
    assert plan[0]["Plan"]["Actual Rows"] == 10
    assert "Execution Time" in plan[0]
    assert "Shared Hit Blocks" in plan[0]["Plan"]  # BUFFERS


@db
async def test_explain_dml_plans_without_executing():
    r = await explain_query("DELETE FROM orders WHERE id < 100")
    assert "Delete on public.orders" in r.plan
    assert (await execute_query("SELECT count(*) FROM orders")).rows == [[2000]]


@db
async def test_explain_runtime_error():
    with pytest.raises(ToolError, match='^42P01: relation "nope" does not exist'):
        await explain_query("SELECT * FROM nope")
