import pytest

from src.security import (
    SQLValidationError,
    StatementKind,
    validate_explain,
    validate_query,
)


def rejected(fn, sql: str, **kwargs) -> str:
    """Return the rejection code, failing if the SQL was accepted."""
    with pytest.raises(SQLValidationError) as info:
        fn(sql, **kwargs)
    return info.value.code


# --------------------------------------------------------------------------- accepted


@pytest.mark.parametrize(
    ("sql", "kind"),
    [
        ("SELECT 1", StatementKind.SELECT),
        ("select * from customers where id = 1;", StatementKind.SELECT),
        ("TABLE customers", StatementKind.SELECT),
        ("VALUES (1, 'a'), (2, 'b')", StatementKind.SELECT),
        ("WITH c AS (SELECT 1 AS x) SELECT x FROM c", StatementKind.SELECT),
        ("(SELECT 1) UNION ALL (SELECT 2)", StatementKind.SELECT),
        ("SELECT '{\"a\": 1}'::jsonb ->> 'a', now()::date", StatementKind.SELECT),
        ("SELECT current_setting('statement_timeout')", StatementKind.SELECT),
        ("SHOW statement_timeout", StatementKind.SHOW),
        ("SHOW ALL", StatementKind.SHOW),
        ("-- leading comment\n/* block */ SELECT 1 -- trailing", StatementKind.SELECT),
        # Keywords inside strings and identifiers are data, not code.
        ("SELECT 'DROP TABLE x; set_config()' AS \"delete\"", StatementKind.SELECT),
        # Similar names that are allowed.
        (
            "SELECT pg_size_pretty(pg_database_size(current_database()))",
            StatementKind.SELECT,
        ),
        ("SELECT lower(name) FROM products", StatementKind.SELECT),
    ],
)
def test_query_accepts_reads(sql: str, kind: StatementKind) -> None:
    result = validate_query(sql)
    assert result.kind is kind
    assert result.sql == sql


# --------------------------------------------------------------------------- statement shape


@pytest.mark.parametrize(
    "sql", ["", "   ", "\n\t", ";", ";;", "-- only a comment", "/* x */"]
)
def test_rejects_empty(sql: str) -> None:
    assert rejected(validate_query, sql) == "empty"


def test_rejects_too_long() -> None:
    assert rejected(validate_query, "SELECT 1" + " " * 20, max_length=10) == "too_long"


def test_rejects_nul_byte() -> None:
    assert rejected(validate_query, "SELECT 1\x00") == "nul_byte"


@pytest.mark.parametrize(
    "sql", ["SELEC 1", "SELECT FROM WHERE", "SELECT 'unterminated"]
)
def test_rejects_syntax_errors(sql: str) -> None:
    assert rejected(validate_query, sql) == "syntax_error"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; SELECT 2",
        "SELECT 1; DROP TABLE customers",
        "SELECT 1; COMMIT; DELETE FROM orders",
        "SHOW ALL; SET statement_timeout = 0",
    ],
)
def test_rejects_multiple_statements(sql: str) -> None:
    assert rejected(validate_query, sql) == "multiple_statements"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
        "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE",
        "TRUNCATE t",
        "DROP TABLE t",
        "CREATE TABLE t (a int)",
        "ALTER TABLE t ADD COLUMN b int",
        "GRANT ALL ON t TO public",
        "SET statement_timeout = 0",
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
        "RESET ALL",
        "BEGIN",
        "COMMIT",
        "COPY t TO '/tmp/x'",
        "COPY t FROM PROGRAM 'id'",
        "DO $$ BEGIN PERFORM 1; END $$",
        "CALL p()",
        "VACUUM t",
        "LOCK TABLE t",
        "LISTEN chan",
        "NOTIFY chan",
        "PREPARE s AS SELECT 1",
        "EXECUTE s",
        "DECLARE c CURSOR FOR SELECT 1",
        "EXPLAIN SELECT 1",
        "REFRESH MATERIALIZED VIEW m",
        "CREATE EXTENSION dblink",
        "LOAD 'x'",
    ],
)
def test_query_rejects_non_reads(sql: str) -> None:
    assert rejected(validate_query, sql) == "statement_not_allowed"


# --------------------------------------------------------------------------- tree checks


@pytest.mark.parametrize(
    "sql",
    [
        "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
        "WITH i AS (INSERT INTO t VALUES (1) RETURNING *) SELECT 1",
        "WITH u AS (UPDATE t SET a = 1 RETURNING *) TABLE u",
        "SELECT * FROM (SELECT 1) s, LATERAL (WITH d AS (DELETE FROM t RETURNING 1) SELECT 1) x",
    ],
)
def test_rejects_data_modifying_ctes(sql: str) -> None:
    assert rejected(validate_query, sql) == "data_modifying_cte"


@pytest.mark.parametrize(
    "sql",
    ["SELECT 1 INTO new_table", "SELECT * INTO TEMP t FROM customers"],
)
def test_rejects_select_into(sql: str) -> None:
    assert rejected(validate_query, sql) == "select_into"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM t FOR UPDATE",
        "SELECT * FROM t FOR SHARE",
        "SELECT * FROM t FOR NO KEY UPDATE SKIP LOCKED",
        "SELECT * FROM (SELECT * FROM t FOR UPDATE) s",
    ],
)
def test_rejects_row_locking(sql: str) -> None:
    assert rejected(validate_query, sql) == "row_locking"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT set_config('statement_timeout', '0', false)",
        "SELECT pg_catalog.set_config('search_path', 'x', false)",
        "SELECT \"set_config\"('a', 'b', false)",
        "SELECT SET_CONFIG('a', 'b', false)",
        "SELECT pg_sleep(10)",
        "SELECT pg_terminate_backend(123)",
        "SELECT pg_cancel_backend(pid) FROM pg_stat_activity",
        "SELECT pg_reload_conf()",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_read_binary_file('postgresql.conf')",
        "SELECT * FROM pg_ls_dir('.')",
        "SELECT lo_import('/etc/passwd')",
        "SELECT lo_export(1, '/tmp/x')",
        "SELECT pg_advisory_lock(1)",
        "SELECT pg_try_advisory_lock(1)",
        "SELECT query_to_xml('DELETE FROM t RETURNING 1', true, false, '')",
        "SELECT * FROM dblink('dbname=x', 'DROP TABLE t') AS r(a int)",
        "SELECT dblink_exec('DROP TABLE t')",
        "SELECT nextval('seq')",
        "SELECT setval('seq', 1)",
        "SELECT txid_current()",
        "SELECT pg_notify('c', 'x')",
        "SELECT pg_stat_reset()",
        "SELECT pg_switch_wal()",
        "SELECT pg_create_logical_replication_slot('s', 'pgoutput')",
        # Hidden in a subquery, CTE, FROM function, CASE and ORDER BY.
        "SELECT (SELECT pg_sleep(1))",
        "WITH x AS (SELECT set_config('a', 'b', false)) SELECT * FROM x",
        "SELECT * FROM ROWS FROM (pg_sleep(1))",
        "SELECT CASE WHEN true THEN pg_sleep(1) END",
        "SELECT 1 ORDER BY pg_sleep(1)",
        "SELECT * FROM t WHERE id IN (SELECT pg_advisory_lock(1))",
    ],
)
def test_rejects_denied_functions(sql: str) -> None:
    assert rejected(validate_query, sql) == "function_not_allowed"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM pg_authid",
        "SELECT rolpassword FROM pg_catalog.pg_authid",
        "TABLE pg_shadow",
        "SELECT * FROM pg_user_mappings",
        "SELECT * FROM PG_HBA_FILE_RULES",
        "SELECT * FROM t JOIN pg_shadow s ON true",
        "SELECT (SELECT passwd FROM pg_shadow LIMIT 1)",
    ],
)
def test_rejects_credential_catalogs(sql: str) -> None:
    assert rejected(validate_query, sql) == "relation_not_allowed"


# --------------------------------------------------------------------------- EXPLAIN


@pytest.mark.parametrize(
    ("sql", "kind"),
    [
        ("SELECT * FROM orders WHERE customer_id = 5", StatementKind.SELECT),
        ("DELETE FROM orders WHERE id = 1", StatementKind.DELETE),
        ("UPDATE products SET price = 1", StatementKind.UPDATE),
        ("INSERT INTO countries VALUES ('XX', 'X')", StatementKind.INSERT),
    ],
)
def test_explain_without_analyze_allows_dml(sql: str, kind: StatementKind) -> None:
    assert validate_explain(sql, analyze=False).kind is kind


@pytest.mark.parametrize(
    "sql",
    ["DELETE FROM orders", "UPDATE products SET price = 1", "SHOW ALL"],
)
def test_explain_analyze_only_allows_selects(sql: str) -> None:
    assert rejected(validate_explain, sql, analyze=True) == "statement_not_allowed"


@pytest.mark.parametrize("analyze", [False, True])
def test_explain_applies_tree_checks(analyze: bool) -> None:
    assert (
        rejected(validate_explain, "SELECT pg_sleep(5)", analyze=analyze)
        == "function_not_allowed"
    )
    assert (
        rejected(validate_explain, "EXPLAIN SELECT 1", analyze=analyze)
        == "statement_not_allowed"
    )
    assert (
        rejected(validate_explain, "SELECT 1; DROP TABLE t", analyze=analyze)
        == "multiple_statements"
    )
    assert (
        rejected(validate_explain, "SELECT 1) ; DROP TABLE t; --", analyze=analyze)
        == "syntax_error"
    )


def test_explain_rejects_cte_write_in_dml() -> None:
    sql = "WITH d AS (DELETE FROM t RETURNING id) UPDATE x SET a = 1 FROM d"
    assert rejected(validate_explain, sql, analyze=False) == "data_modifying_cte"
