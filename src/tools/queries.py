"""Query tools: run read-only SQL and explain execution plans."""

from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from src.config import settings
from src.tools.common import db_connection, require_statement, to_json

QUERY_STATEMENTS = frozenset({"select", "with", "values", "table", "show"})
# EXPLAIN without ANALYZE never executes the statement, so DML plans are safe to inspect;
# with ANALYZE the read-only transaction still rejects any write.
EXPLAIN_STATEMENTS = frozenset(
    {"select", "with", "values", "table", "insert", "update", "delete", "merge"}
)


class ResultColumn(BaseModel):
    name: str
    type: str


class QueryResult(BaseModel):
    columns: list[ResultColumn]
    rows: list[list[Any]] = Field(
        description="Row values in the same order as `columns`."
    )
    row_count: int
    truncated: bool = Field(description="True when more rows exist beyond `limit`.")


class ExplainResult(BaseModel):
    analyzed: bool
    format: Literal["text", "json"]
    plan: Any = Field(
        description="Plan text (format=text) or EXPLAIN JSON document (format=json)."
    )


async def execute_query(
    sql: Annotated[
        str,
        Field(description="A single SELECT, WITH, VALUES, TABLE or SHOW statement."),
    ],
    limit: Annotated[
        int,
        Field(
            ge=1,
            description="Maximum rows to return (capped by the server's QUERY_MAX_ROWS).",
        ),
    ] = 100,
) -> QueryResult:
    """Run one read-only SQL statement and return its rows.

    Runs in a READ ONLY transaction that is always rolled back, under the server's
    statement timeout. Rows are fetched incrementally, so large results are cut at `limit`
    without being fully materialised."""
    require_statement(sql, QUERY_STATEMENTS)
    limit = min(limit, settings.QUERY_MAX_ROWS)

    async with db_connection() as conn:
        raw = await conn.get_raw_connection()
        # asyncpg.Connection, already inside our read-only transaction.
        driver = raw.driver_connection
        assert driver is not None
        stmt = await driver.prepare(sql)  # also rejects multiple statements
        columns = [
            ResultColumn(name=a.name, type=a.type.name) for a in stmt.get_attributes()
        ]
        cursor = await stmt.cursor()
        records = await cursor.fetch(limit + 1)

    truncated = len(records) > limit
    rows = [[to_json(v) for v in r.values()] for r in records[:limit]]
    return QueryResult(
        columns=columns, rows=rows, row_count=len(rows), truncated=truncated
    )


async def explain_query(
    sql: Annotated[
        str,
        Field(
            description="The statement to explain (SELECT/WITH/VALUES/TABLE, or DML without analyze)."
        ),
    ],
    analyze: Annotated[
        bool,
        Field(
            description="Actually execute the statement to report real timings, row counts and buffer usage."
        ),
    ] = False,
    format: Annotated[
        Literal["text", "json"], Field(description="Plan output format.")
    ] = "text",
) -> ExplainResult:
    """Show the PostgreSQL execution plan for a statement (EXPLAIN, optionally ANALYZE)."""
    require_statement(sql, EXPLAIN_STATEMENTS)
    options = [f"FORMAT {format.upper()}", "VERBOSE"]
    if analyze:
        options += ["ANALYZE", "BUFFERS"]

    async with db_connection() as conn:
        raw = await conn.get_raw_connection()
        driver = raw.driver_connection
        assert driver is not None
        records = await driver.fetch(f"EXPLAIN ({', '.join(options)}) {sql}")

    if not records:
        raise ToolError("EXPLAIN returned no plan.")
    plan: Any = (
        "\n".join(r[0] for r in records) if format == "text" else to_json(records[0][0])
    )
    return ExplainResult(analyzed=analyze, format=format, plan=plan)
