"""Each scenario mock gives a prompt something real to find, through the tools and resources
that prompt tells the model to use. Expected values are documented in data/mocks/*.sql."""

import pytest

from src.resources.dictionary import DICTIONARY_URI
from src.tools.queries import execute_query, explain_query
from src.tools.tables import describe_table, list_tables
from tests.mocks.conftest import scalar
from tests.resources.conftest import read_json

pytestmark = pytest.mark.usefixtures("db")


async def dictionary(mcp) -> dict[str, dict]:
    out = await read_json(mcp, DICTIONARY_URI)
    return {f"{r['schema_name']}.{r['name']}": r for r in out["relations"]}


# --------------------------------------------------------------------------- extensions


async def test_pg_stat_statements_has_the_slow_lookup():
    # Unqualified: the database's search_path includes the `extensions` schema.
    calls = await scalar(
        "SELECT sum(calls) FROM pg_stat_statements "
        "WHERE query LIKE 'SELECT count(*) FROM analytics.events WHERE session_id = $1%'"
    )
    assert int(calls) >= 3


# --------------------------------------------------------------------------- security (hr)


async def test_pii_is_readable_by_public():
    assert await scalar(
        "SELECT has_table_privilege('public', 'hr.employees', 'SELECT')"
    )
    assert not await scalar(
        "SELECT has_table_privilege('public', 'hr.app_users', 'SELECT')"
    )
    assert await scalar("SELECT has_schema_privilege('public', 'public', 'CREATE')")


async def test_dangerous_roles():
    result = await execute_query(
        "SELECT rolname, rolcanlogin, rolcreaterole, rolbypassrls, rolreplication, "
        "rolvaliduntil < now() AS expired "
        "FROM pg_roles WHERE rolname IN ('legacy_admin', 'etl_loader', 'intern', 'inspector_ro') "
        "ORDER BY rolname"
    )
    assert result.rows == [
        ["etl_loader", True, False, False, True, None],
        ["inspector_ro", True, False, False, False, None],
        ["intern", True, False, False, False, True],
        ["legacy_admin", True, True, True, False, None],
    ]
    members = await execute_query(
        "SELECT r.rolname FROM pg_auth_members m "
        "JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
        "WHERE u.rolname = 'etl_loader' ORDER BY 1"
    )
    assert members.rows == [["pg_execute_server_program"], ["pg_read_server_files"]]
    assert await scalar(
        "SELECT tableowner FROM pg_tables WHERE tablename = 'app_users'"
    ) == ("legacy_admin")


async def test_grants_with_grant_option_are_inherited():
    assert (
        await scalar(
            "SELECT count(*) FROM information_schema.role_table_grants "
            "WHERE grantee = 'reporting' AND table_schema = 'hr' AND is_grantable = 'YES'"
        )
        > 0
    )
    assert await scalar(
        "SELECT has_table_privilege('analyst', 'hr.app_users', 'DELETE')"
    )


async def test_security_definer_functions():
    result = await execute_query(
        "SELECT p.proname, p.proconfig IS NULL AS unpinned_search_path, "
        "has_function_privilege('public', p.oid, 'EXECUTE') AS public_execute "
        "FROM pg_proc p WHERE p.prosecdef AND p.pronamespace = 'hr'::regnamespace ORDER BY 1"
    )
    assert result.rows == [
        ["employee_directory", False, False],
        ["reset_password", True, True],
    ]


async def test_row_level_security():
    assert await scalar(
        "SELECT relrowsecurity FROM pg_class WHERE oid = 'hr.salary_reviews'::regclass"
    )
    assert await scalar("SELECT count(*) FROM pg_policies WHERE schemaname = 'hr'") == 1


async def test_sensitive_columns_are_documented(mcp):
    rels = await dictionary(mcp)
    employees = {c["name"]: c for c in rels["hr.employees"]["columns"]}
    assert all(
        "PII" in employees[c]["comment"]
        for c in ("personal_phone", "birth_date", "national_id")
    )
    app_users = {c["name"]: c for c in rels["hr.app_users"]["columns"]}
    assert "secret" in app_users["password_hash"]["comment"]


# --------------------------------------------------------------------------- data quality (crm)


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        # Orphans: reference-like columns without a foreign key.
        (
            (
                "SELECT count(*) FROM crm.contacts c "
                "WHERE NOT EXISTS (SELECT 1 FROM crm.accounts a WHERE a.id = c.account_id)"
            ),
            20,
        ),
        (
            (
                "SELECT count(*) FROM crm.opportunities o "
                "WHERE NOT EXISTS (SELECT 1 FROM crm.accounts a WHERE a.id = o.account_id)"
            ),
            9,
        ),
        # NULLs and blanks.
        ("SELECT count(*) FROM crm.contacts WHERE email IS NULL", 25),
        ("SELECT count(*) FROM crm.contacts WHERE trim(first_name) = ''", 5),
        ("SELECT count(*) FROM crm.accounts WHERE industry = ''", 4),
        # Duplicates no unique constraint protects.
        (
            (
                "SELECT count(*) FROM (SELECT lower(trim(name)) FROM crm.accounts "
                "GROUP BY 1 HAVING count(*) > 1) d"
            ),
            5,
        ),
        (
            (
                "SELECT count(*) FROM (SELECT lower(email) FROM crm.contacts "
                "WHERE email IS NOT NULL GROUP BY 1 HAVING count(*) > 1) d"
            ),
            10,
        ),
        ("SELECT count(*) - count(DISTINCT l) FROM crm.leads l", 15),
        # Unexpected values.
        ("SELECT count(*) FROM crm.contacts WHERE email NOT LIKE '%@%'", 13),
        (
            (
                "SELECT count(*) FROM crm.opportunities "
                "WHERE stage NOT IN ('prospecting', 'qualification', 'proposal', 'won', 'lost')"
            ),
            50,
        ),
        (
            "SELECT count(*) FROM crm.accounts WHERE country_code NOT IN (SELECT code FROM countries)",
            6,
        ),
        (
            "SELECT count(*) FROM crm.opportunities WHERE currency <> upper(currency)",
            57,
        ),
        # Inconsistent timestamps.
        ("SELECT count(*) FROM crm.contacts WHERE created_at > now()", 4),
        ("SELECT count(*) FROM crm.accounts WHERE updated_at < created_at", 4),
        ("SELECT count(*) FROM crm.opportunities WHERE closed_at < created_at", 11),
        # Suspicious numbers.
        ("SELECT count(*) FROM crm.opportunities WHERE amount < 0", 10),
        (
            (
                "SELECT count(*) FROM crm.opportunities WHERE amount > 100 * (SELECT "
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY amount) FROM crm.opportunities)"
            ),
            4,
        ),
        (
            "SELECT count(*) FROM crm.opportunities WHERE probability NOT BETWEEN 0 AND 100",
            5,
        ),
    ],
)
async def test_planted_data_quality_issues(sql, expected):
    assert await scalar(sql) == expected


async def test_structural_gaps_are_visible(mcp):
    leads = await describe_table("leads", schema="crm")
    assert [c for c in leads.constraints if c.type == "primary key"] == []
    assert "USING gin (company gin_trgm_ops)" in leads.indexes[0].definition

    rels = await dictionary(mcp)
    contacts = {c["name"]: c for c in rels["crm.contacts"]["columns"]}
    assert "references" not in contacts["account_id"]  # no foreign key declared
    assert rels["crm.contacts"]["foreign_keys"] == []


# --------------------------------------------------------------------------- performance (analytics)


async def test_foreign_key_without_a_usable_index():
    events = await describe_table("events", schema="analytics")
    assert events.estimated_rows == 200_000
    indexes = {i.name: i for i in events.indexes}
    assert indexes["events_session_id_key"].is_valid is False
    assert all(i.is_valid for n, i in indexes.items() if n != "events_session_id_key")
    assert "analytics.sessions" in next(
        c.definition for c in events.constraints if c.type == "foreign key"
    )

    plan = await explain_query(
        "SELECT count(*) FROM analytics.events WHERE session_id = 42", format="json"
    )
    assert "Seq Scan" in str(plan.plan)


async def test_duplicate_and_overlapping_indexes():
    indexes = {
        i.name: i.definition
        for i in (await describe_table("events", schema="analytics")).indexes
    }

    def strip(name: str) -> str:
        return indexes[name].split(" USING ", 1)[1]

    assert strip("events_occurred_at_idx") == strip("events_occurred_at_dup_idx")
    assert strip("events_type_occurred_idx").startswith(
        strip("events_type_idx").rstrip(")")
    )


async def test_dead_tuples_and_partitions():
    stats = await execute_query(
        "SELECT n_live_tup, n_dead_tup FROM pg_stat_user_tables "
        "WHERE relid = 'analytics.page_views'::regclass"
    )
    assert stats.rows == [[20_000, 30_000]]

    tables = {t.name: t for t in await list_tables(schema="analytics")}
    assert tables["daily_metrics"].kind == "partitioned table"
    assert tables["session_summary"].kind == "materialized view"
    assert await scalar("SELECT count(*) FROM analytics.daily_metrics") == 300
    assert await scalar("SELECT count(*) FROM analytics.daily_metrics_default") == 30


async def test_table_without_primary_key_or_indexes():
    raw = await describe_table("raw_imports", schema="analytics")
    assert raw.constraints == [] and raw.indexes == []
    assert {c.type for c in raw.columns} == {"text"}
