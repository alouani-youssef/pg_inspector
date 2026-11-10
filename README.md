# POSTGRESQL INSPECTOR

Light weight PostgreSql Mcp server. It provides the ability to connect to a PostgreSQL database and inspect its schema and data.

## Features

- List databases and tables.
- Inspect table schemas.
- Execute read-only SQL.
- Explain query execution plans.

## Resources

| URI | Content |
| --- | --- |
| `pg-inspector://database/overview` | JSON snapshot of the configured database: server version, size, encoding, connections, per-schema object counts (tables, views, sequences, functions, types), extensions and the 10 largest tables. |
| `pg-inspector://database/dictionary` | Semantic data dictionary: every table and view with comments, columns (type, nullability, default, comment, enum values, the column a foreign key points to), primary/unique keys, checks, `foreign_keys` / `referenced_by` join paths, plus enum and domain types. Capped at 500 relations (`truncated`). |
| `pg-inspector://database/health` | Health snapshot: `status` + plain-language `warnings`, connection usage by state, cache hit and rollback ratios, long-running (>5 min) and blocked sessions, transaction ID wraparound, vacuum backlog, invalid and unused indexes. |

## Prompts

Guided workflows that walk the model through the tools and resources above. They are read-only, and if an operator has filtered out a tool they reference, they tell the model to fall back to `server_discover_tools`.

| Prompt | Arguments | Produces |
| --- | --- | --- |
| `explore_database` | `schema_name`?, `table_name`? | Architecture report: schemas, keys, relationship graph (Mermaid ER diagram), indexes, structural issues. |
| `generate_sql_query` | `business_question` | A SELECT built from the discovered schema, checked with `query_execute` / `query_explain`, with an explanation, assumptions, a sensitive-data check and performance notes. |
| `investigate_database_performance` | `table_name`?, `query`? | Missing indexes, sequential scans, bloat, connection pressure, lock contention, plan analysis. Each recommendation comes with evidence, benefit and trade-offs. |
| `audit_database_security` | none | Roles, privileges, `PUBLIC` grants, `SECURITY DEFINER` functions, RLS and sensitive columns as findings with severity and remediation SQL. Never reads secret values. |
| `investigate_data_quality` | `table_name` | NULLs, duplicates, orphaned references, unexpected values, inconsistent timestamps and numeric outliers, with counts, masked samples and remediation. |

## Transports

| Transport | Start | Use when |
| --- | --- | --- |
| `streamable-http` (default) | `uv run start` | A shared, networked server: MCP at `/mcp` on `HOST:PORT`, API key required, plus `/health`, `/ready`, `/metrics`. |
| `stdio` | `uv run start --transport stdio` | A local client (Claude Desktop, Claude Code, an IDE) launches the server as a subprocess and talks JSON-RPC over stdin/stdout. |

`--transport` overrides the `TRANSPORT` environment variable. Under stdio, the client owns the
process, so there is no listener, `API_KEY` is not needed and the HTTP routes are not served.
All logs (including `DATABASE_ECHO` SQL) go to stderr, because stdout carries the protocol.

Example client config (Claude Desktop `claude_desktop_config.json`, or `.mcp.json` for Claude Code):

```json
{
  "mcpServers": {
    "pg-inspector": {
      "command": "uv",
      "args": ["--directory", "/path/to/pg_inspector", "run", "start", "--transport", "stdio"],
      "env": { "DATABASE_HOST": "127.0.0.1", "DATABASE_USERNAME": "inspector_ro" }
    }
  }
}
```

Settings still come from `.env` in the project directory and from `env`.

## Authentication

The HTTP MCP endpoint (`/mcp`) requires an API key; `/health`, `/ready` and `/metrics` stay open.
Set `API_KEY` in `.env` (at least 16 characters, the server refuses to start otherwise):

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Clients send it as `Authorization: Bearer <key>` (or `X-API-Key: <key>`). A missing or wrong
key gets `401` with `{"error": {"status": 401, "message": "..."}}` and a
`WWW-Authenticate: Bearer resource_metadata="..."` header.

OAuth Protected Resource Metadata ([RFC 9728](https://datatracker.ietf.org/doc/html/rfc9728))
for `/mcp` is published (no key needed) at:

- `/.well-known/oauth-protected-resource`
- `/.well-known/oauth-protected-resource/mcp`

No authorization server is advertised: the bearer token is the static API key. Set
`PUBLIC_URL` (e.g. `https://inspector.example.com`) when running behind a proxy; otherwise
URLs are derived from each request's scheme and `Host`.

## Mock data

`docker compose` seeds the `app` database with `data/db_init.sql` (a clean e-commerce
schema in `public`), then loads scenario mocks from `data/mocks/` (listed in
`data/mocks/load.sql`). Each mock gives the tools, resources and prompts real things to find.
Expected counts are documented at the bottom of each file.

| File | Schema | Scenario |
| --- | --- | --- |
| `10_extensions.sql` | `extensions` | `pg_stat_statements`, `pg_trgm`, `citext`, plus the `search_path` that resolves them unqualified |
| `20_hr_security.sql` | `hr` + roles | `audit_database_security`: PII readable by `PUBLIC`, an unsafe `SECURITY DEFINER` function, over-privileged and expired roles, `WITH GRANT OPTION`, row-level security, and `inspector_ro`, a least-privilege role to run this server as |
| `30_crm_quality.sql` | `crm` | `investigate_data_quality`: orphans (no foreign keys), NULLs, case and whitespace duplicates, off-vocabulary values, impossible timestamps, negative and outlier amounts, a table with no primary key |
| `40_analytics_performance.sql` | `analytics` | `investigate_database_performance` and the health resource: 200k events whose foreign key has only an **invalid** index (sequential scans), duplicate and overlapping indexes, 60% dead tuples, partitioning, `pg_stat_statements` history |

Mocks only run on the first start of an empty volume. After changing them, run
`uv run test --reseed` (this deletes the database's data).

Live session problems can't be seeded, so `uv run mock-activity` creates them. It holds
a session *idle in transaction* on a row lock, a second session *blocked* behind it, and a
*long-running* query, for `--seconds` (default 360, past the health resource's 300s
threshold). Everything is rolled back, so no data changes. It connects as `DATABASE_USERNAME`,
which needs `UPDATE` on `crm.accounts`.

## Running tests

Tests use `pytest`. Integration tests run against the seeded PostgreSQL from
`docker compose` (`data/db_init.sql`); they are skipped if it is unreachable.

```sh
cp .env.example .env         # once
uv run test                  # start the db (if needed) and run all tests
uv run test --check          # + ruff lint/format check and mypy
uv run test --reseed         # recreate the db from data/db_init.sql first (deletes its data)
uv run test --unit           # don't start the db
uv run test --down           # stop the db afterwards
uv run test -k describe tests/tools/test_catalog.py   # other args go to pytest
```
