"""Prompt templates: workflows that walk the model through the server's tools and resources.

Each function renders one user message. Tool and resource names below must match what the
server registers (`tests/prompts` checks this), so a renamed tool cannot leave a prompt
pointing at nothing. Operators may hide tools with TOOL_GROUPS / TOOLS_INCLUDE /
TOOLS_EXCLUDE, so every prompt tells the model how to cope with a missing one.
"""

from typing import Annotated

from pydantic import Field

from src.resources.dictionary import DICTIONARY_URI
from src.resources.health import HEALTH_URI
from src.resources.overview import OVERVIEW_URI

# Shared rules appended to every prompt.
_GROUND_RULES = """\
Ground rules:
- Read only. Never run INSERT, UPDATE, DELETE, DDL, GRANT/REVOKE or anything else that \
changes the database; the server rejects such statements anyway.
- Some tools may be disabled on this server. If a tool named here is not available, call \
`server_discover_tools` to find the closest alternative, and state in the report what you \
could not check.
- Base every conclusion on evidence you retrieved. Label anything inferred as an assumption.
- Keep ad-hoc queries cheap: select only the columns you need and pass a small `limit`."""


def _blank_to_none(value: str | None) -> str | None:
    """Clients often send optional prompt arguments as empty strings."""
    if value is None:
        return None
    return value.strip() or None


def _render(*sections: str) -> str:
    return "\n\n".join(s.strip("\n") for s in (*sections, _GROUND_RULES) if s)


# --------------------------------------------------------------------------- exploration


def explore_database(
    schema_name: Annotated[
        str | None,
        Field(
            description="Schema to focus on. Omit to explore every non-system schema."
        ),
    ] = None,
    table_name: Annotated[
        str | None,
        Field(description="Table or view to focus on (case-sensitive, unquoted)."),
    ] = None,
) -> str:
    """Explore the database structure and produce an architecture report: schemas, tables,
    keys, relationships, indexes and structural issues."""
    schema_name, table_name = _blank_to_none(schema_name), _blank_to_none(table_name)

    if table_name:
        where = (
            f"`{schema_name}`"
            if schema_name
            else "the schema that contains it (see step 3)"
        )
        focus = (
            f"Focus: the table `{table_name}` in {where}. Map the whole database briefly for "
            "context, then go deep on this table and every table it references or is "
            "referenced by."
        )
    elif schema_name:
        focus = (
            f"Focus: the schema `{schema_name}`. Describe every table in it, and note "
            "relationships that cross into other schemas."
        )
    else:
        focus = (
            "Scope: the whole database. If there are many tables, describe the most central "
            "ones in depth (most referenced, largest) and summarise the rest."
        )

    return _render(
        "You are a senior PostgreSQL database engineer. Your task is to explore the "
        "connected database.",
        focus,
        f"""\
Follow these steps:

1. Database overview: read the resource `{OVERVIEW_URI}` (version, size, per-schema object \
counts, extensions, largest tables).
2. Schemas: call `catalog_list_schemas` to list them with owners and comments.
3. Tables and views: call `catalog_list_tables` (pass `schema` to narrow it) for kinds, \
estimated row counts and sizes.
4. Table structure: call `catalog_describe_table` for each table in scope (columns, types, \
defaults, comments, constraints, indexes, triggers).
5. Keys: from those descriptions, record each table's primary key and unique constraints, \
and every foreign key with its ON DELETE / ON UPDATE behaviour.
6. Relationships: read `{DICTIONARY_URI}` for the `foreign_keys` / `referenced_by` join \
paths, and classify each as one-to-one, one-to-many or many-to-many (junction tables).
7. Indexes: note what each index covers, its type, and whether it is unique or partial.""",
        """\
Produce a structured database exploration report with these sections:

## Database architecture
Engine version, size, extensions, and the overall purpose the schema suggests.

## Schema organization
Each schema in scope, what it holds, and its notable tables.

## Table relationships
The foreign-key graph as a list of `child.column -> parent.column` edges, the central \
entities, junction tables, and a Mermaid `erDiagram` when it stays readable (up to about 15 \
tables).

## Important indexes
Indexes that back keys and frequent access paths, and anything unusual.

## Potential structural issues
For example: tables without a primary key, foreign-key columns with no index leading on \
them, duplicate or overlapping indexes, nullable columns that look mandatory, inconsistent \
naming or types between joined columns, missing comments. For each, name the object and the \
evidence.""",
    )


# --------------------------------------------------------------------------- SQL generation


def generate_sql_query(
    business_question: Annotated[
        str,
        Field(
            description="The question to answer in plain language, e.g. "
            "'Find the top 10 customers by total spending'."
        ),
    ],
) -> str:
    """Turn a business question into a validated, explained PostgreSQL SELECT query,
    discovering the relevant schema first instead of guessing it."""
    return _render(
        "You are an expert PostgreSQL query engineer.",
        f"The user wants:\n\n<business_question>\n{business_question.strip()}\n</business_question>",
        f"""\
Before writing any SQL:

1. Inspect the relevant schemas: call `catalog_list_schemas`.
2. Identify the relevant tables: call `catalog_list_tables` and match names and comments \
to the concepts in the question.
3. Inspect their columns and relationships: call `catalog_describe_table` for each \
candidate table.
4. Retrieve the database documentation: read `{DICTIONARY_URI}` for column comments, enum \
values and the join paths between tables.
5. Identify the business rules that apply: comments, check constraints, enum values, \
soft-delete or status columns (e.g. `deleted_at`, `is_active`, `status`), currencies and \
time zones. If the question is ambiguous (e.g. which date defines "last month", whether \
refunds count as spending), choose the most reasonable reading and record it as an \
assumption.

Then:

6. Write one optimized PostgreSQL SELECT (CTEs allowed). Use explicit JOIN ... ON, \
qualified column names and meaningful aliases; never `SELECT *`.
7. Explain each JOIN condition and why its type (INNER / LEFT) is correct.
8. Explain the filters, grouping and aggregation, including how NULLs and ties are handled.
9. Check whether the result could expose sensitive data (personal data, credentials, \
tokens, payment details). Leave such columns out unless the question needs them, and say so.
10. Validate the query if the query tools are available: run it with `query_execute` and a \
small `limit` to confirm it works and the results look plausible, and call `query_explain` \
to check the plan. Fix and re-run until it succeeds. If the tools are not available, say \
the query has not been executed.""",
        """\
Return:
- **SQL query** in a ```sql block
- **Explanation** of joins, filters and aggregation
- **Tables involved** with the columns used
- **Assumptions** about ambiguous parts of the question
- **Potential performance concerns** with evidence from the plan (sequential scans on large \
tables, missing indexes, expensive sorts) and suggested mitigations""",
    )


# --------------------------------------------------------------------------- performance


def investigate_database_performance(
    table_name: Annotated[
        str | None,
        Field(
            description="Table to focus on (optionally schema-qualified, e.g. sales.orders)."
        ),
    ] = None,
    query: Annotated[
        str | None,
        Field(
            description="A slow SQL statement whose execution plan should be analysed."
        ),
    ] = None,
) -> str:
    """Investigate database performance (indexes, scans, bloat, long-running queries, locks
    and connections) and produce evidence-backed recommendations."""
    table_name, query = _blank_to_none(table_name), _blank_to_none(query)

    focus = []
    if table_name:
        focus.append(
            f"Focus on the table `{table_name}`: its statistics, indexes, the queries that "
            "touch it and the locks held on it. Still report database-wide problems that "
            "affect it."
        )
    if query:
        focus.append(
            f"Analyse this statement's execution plan (step 7):\n\n```sql\n{query}\n```"
        )
    if not focus:
        focus.append("Scope: the whole database. Rank findings by expected impact.")

    plan_step = (
        "7. Execution plan: call `query_explain` on the supplied statement with "
        '`format="json"`. Use `analyze=true` only for a SELECT; it really runs it. Look for '
        "sequential scans on large tables, row estimates that are far off, nested loops over "
        "many rows, sorts or hashes that spill to disk, and filters that discard most rows."
        if query
        else "7. Execution plans: if you find a slow or frequent query in steps 5-6, call "
        "`query_explain` on it."
    )

    return _render(
        "Act as a senior PostgreSQL performance engineer. Investigate the performance of "
        "the connected database.",
        *focus,
        f"""\
Steps:

1. Health: read `{HEALTH_URI}` (connections by state, cache hit and rollback ratios, \
long-running and blocked sessions, vacuum backlog, invalid and unused indexes) and \
`{OVERVIEW_URI}` (size, largest tables, whether `pg_stat_statements` is installed).
2. Table statistics: with `query_execute`, read `pg_stat_user_tables` (`seq_scan`, \
`seq_tup_read`, `idx_scan`, `n_live_tup`, `n_dead_tup`, `n_mod_since_analyze`, \
`last_autovacuum`, `last_autoanalyze`).
3. Large tables: the largest relations from the overview, or `catalog_list_tables`.
4. Indexes: `catalog_describe_table` for the tables in scope, plus `pg_stat_user_indexes` \
(`idx_scan`) and `pg_relation_size(indexrelid)` for usage and size.
5. Sequential scans: tables with a high `seq_scan` and `seq_tup_read` but few `idx_scan` \
and many rows.
6. Long-running queries: `pg_stat_activity` (state, `now() - query_start`, \
`wait_event_type`, `wait_event`), and `pg_stat_statements` ordered by `total_exec_time` \
when that extension is installed.
{plan_step}

Also check:
- Connection pressure: sessions against `max_connections`, and `idle in transaction` sessions.
- Lock contention: `pg_locks` where `NOT granted`, joined to `pg_stat_activity` through \
`pg_blocking_pids(pid)`.
- Bloat indicators: the share of dead tuples (`n_dead_tup` against `n_live_tup`), stale \
vacuum or analyze.""",
        """\
Identify:
- Missing index candidates
- Potentially inefficient queries
- Table bloat indicators
- Connection pressure
- Lock contention

For every recommendation, give:
- **Evidence**: the numbers or plan nodes that support it.
- **Expected benefit**
- **Trade-offs**: write overhead, disk, locking while building (prefer \
`CREATE INDEX CONCURRENTLY`), maintenance.

Present recommendations as SQL for a human to review. Do not run them: do not modify the \
database.""",
    )


# --------------------------------------------------------------------------- security


def audit_database_security() -> str:
    """Audit roles, privileges and public access, and flag sensitive data, reporting each
    finding with severity, evidence and remediation."""
    return _render(
        "Act as a PostgreSQL security auditor. Audit the connected database.",
        f"""\
Inspect, using `query_execute` on the system catalogs:

1. Database roles: `pg_roles` (`rolsuper`, `rolcreaterole`, `rolcreatedb`, `rolcanlogin`, \
`rolreplication`, `rolbypassrls`, `rolconnlimit`, `rolvaliduntil`) and memberships from \
`pg_auth_members`, including membership in powerful predefined roles such as \
`pg_read_server_files`, `pg_write_server_files`, `pg_execute_server_program` and \
`pg_write_all_data`.
2. Role privileges: what each login role can do on this database \
(`has_database_privilege(role, current_database(), 'CREATE')` and `'TEMP'`), and \
`pg_default_acl` for default privileges.
3. Schema permissions: `has_schema_privilege` for USAGE and CREATE per role, schema \
owners from `catalog_list_schemas`, and especially CREATE on `public`.
4. Table-level permissions: `information_schema.role_table_grants` and \
`information_schema.column_privileges`, and table owners. Note which tables enable row-level \
security (`pg_class.relrowsecurity`, `relforcerowsecurity`) and their policies in \
`pg_policies`.
5. Public access: grants to `PUBLIC` on the database, schemas, tables, sequences and \
functions (`aclexplode(...)` with grantee 0), and `SECURITY DEFINER` functions \
(`pg_proc.prosecdef`), especially ones without a pinned `search_path` (`proconfig`).
6. Dangerous or excessive privileges: superusers or roles with CREATEROLE, BYPASSRLS or \
REPLICATION beyond what is needed, login roles that own objects, WITH GRANT OPTION grants, \
and broad grants such as `ALL` to application roles.
7. Sensitive columns and tables: use `{DICTIONARY_URI}` and `catalog_describe_table` to find \
columns whose names, types or comments suggest personal or secret data (email, phone, \
address, birth date, national ID, password, hash, token, secret, api_key, card, iban, \
salary), then check who can read them.

Credential catalogs such as `pg_authid` and `pg_shadow` are blocked by the server; do not \
try to work around that.""",
        """\
Report each finding as:
- **Finding**
- **Severity**: Critical / High / Medium / Low / Info
- **Affected object**: role, schema, table or column
- **Evidence**: the grant, attribute or catalog row that shows it
- **Recommended remediation**: as SQL for a human to review (e.g. `REVOKE ...`)

Order the findings by severity and start with a short summary.

Never expose passwords, password hashes, secrets or the contents of sensitive columns: \
refer to columns by name only and never select their values. Do not modify permissions.""",
    )


# --------------------------------------------------------------------------- data quality


def investigate_data_quality(
    table_name: Annotated[
        str,
        Field(
            description="Table to investigate (optionally schema-qualified, e.g. sales.orders)."
        ),
    ],
) -> str:
    """Check a table for data quality problems (NULLs, duplicates, broken references,
    unexpected values, inconsistent timestamps) using read-only queries."""
    return _render(
        "Act as a database quality engineer.",
        f"Investigate the table `{table_name.strip()}`.",
        f"""\
First understand the table: call `catalog_describe_table` (columns, types, constraints, \
indexes, incoming foreign keys) and read `{DICTIONARY_URI}` for comments and enum values, \
so you know what "valid" means for each column. Use the estimated row count to keep queries \
cheap: on very large tables prefer `TABLESAMPLE SYSTEM (1)` and say that results are \
estimates.

Then check, with read-only queries through `query_execute`:
- **NULL values**: the NULL count and share for each column, especially columns that look \
mandatory but are nullable.
- **Duplicate records**: duplicates on natural or business keys (e.g. email, external IDs, \
names plus dates) that no unique constraint protects, and fully duplicated rows.
- **Invalid references**: values in reference-like columns (`*_id`) with no matching parent \
row, including columns that have no foreign key declared.
- **Unexpected values**: values outside the documented set, empty or whitespace-only \
strings, inconsistent casing or formats (emails, phones, codes).
- **Inconsistent timestamps**: future dates, dates before the system could have existed, \
`updated_at < created_at`, end before start, unexpected time-zone mixes.
- **Suspicious numeric values**: negatives where impossible, zeros, extreme outliers \
(compare against percentiles with `percentile_cont`), totals that disagree with their \
parts.""",
        """\
Return a table of issues and then details. For each issue:
- **Data quality issue** and the column(s) involved
- **Number of affected records** and the share of the table
- **Sample evidence**: at most 5 primary keys or example values; mask personal data
- **Possible causes**
- **Suggested remediation**: a constraint, a validation in the application, or a cleanup \
query for a human to review. Do not run fixes.""",
    )
