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

## Authentication

The MCP endpoint (`/mcp`) requires an API key; `/health`, `/ready` and `/metrics` stay open.
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
