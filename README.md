# POSTGRESQL INSPECTOR

Light weight PostgreSql Mcp server. It provides the ability to connect to a PostgreSQL database and inspect its schema and data.

## Features

- List databases and tables.
- Inspect table schemas.
- Execute read-only SQL.
- Explain query execution plans.

## Authentication

The MCP endpoint (`/mcp`) requires an API key; `/health`, `/ready` and `/metrics` stay open.
Set `API_KEY` in `.env` (at least 16 characters, the server refuses to start otherwise):

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Clients send it as `Authorization: Bearer <key>` (or `X-API-Key: <key>`). A missing or wrong
key gets `401` with `{"error": {"status": 401, "message": "..."}}`.

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
