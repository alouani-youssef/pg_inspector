"""Helpers shared by every tool: annotations, error mapping, SQL guard and JSON conversion."""

import datetime as dt
import math
import re
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import asyncpg
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import TypeAdapter
from sqlalchemy import RowMapping, TextClause
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from src.database import database
from src.database.connection import UnknownDatabaseError

# Every tool on this server only reads from a closed world (the configured PostgreSQL server).
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

# Schemas hidden from listings unless explicitly requested.
SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")

_TIMEDELTA = TypeAdapter(dt.timedelta)


@asynccontextmanager
async def db_connection(db: str | None = None) -> AsyncIterator[AsyncConnection]:
    """A read-only pooled connection to `db` (default: the configured database); database
    errors surface to the client as `ToolError`."""
    try:
        async with database.connection(db) as conn:
            yield conn
    except ToolError:
        raise
    except UnknownDatabaseError as exc:
        raise ToolError(str(exc)) from exc
    except asyncpg.PostgresError as exc:
        raise ToolError(_pg_message(exc)) from exc
    except DBAPIError as exc:
        orig = getattr(exc.orig, "__cause__", None) or exc.orig
        if isinstance(orig, asyncpg.PostgresError):
            raise ToolError(_pg_message(orig)) from exc
        raise ToolError(f"Database error: {exc.orig}") from exc
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        raise ToolError(f"Database unavailable: {exc}") from exc


async def fetch_all(
    conn: AsyncConnection, sql: TextClause, params: Mapping[str, Any] | None = None
) -> Sequence[RowMapping]:
    """Execute a catalog query and return every row as a mapping."""
    return (await conn.execute(sql, params or {})).mappings().all()


async def fetch_one(
    conn: AsyncConnection, sql: TextClause, params: Mapping[str, Any] | None = None
) -> RowMapping | None:
    """Execute a catalog query and return its first row (or None)."""
    return (await conn.execute(sql, params or {})).mappings().first()


def _pg_message(exc: asyncpg.PostgresError) -> str:
    parts = [f"{exc.sqlstate}: {exc.args[0] if exc.args else exc}"]
    if detail := getattr(exc, "detail", None):
        parts.append(f"DETAIL: {detail}")
    if hint := getattr(exc, "hint", None):
        parts.append(f"HINT: {hint}")
    return "\n".join(parts)


_LEADING_NOISE = re.compile(r"^(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/|\()*", re.DOTALL)
_FIRST_WORD = re.compile(r"[A-Za-z]+")


def first_keyword(sql: str) -> str:
    """The statement's leading keyword, lower-cased, ignoring whitespace, comments and `(`."""
    rest = _LEADING_NOISE.sub("", sql, count=1)
    match = _FIRST_WORD.match(rest)
    return match.group(0).lower() if match else ""


def require_statement(sql: str, allowed: frozenset[str]) -> None:
    """Reject anything whose leading keyword is not in `allowed` (defence in depth on top of
    the read-only transaction)."""
    if not sql.strip():
        raise ToolError("SQL must not be empty.")
    keyword = first_keyword(sql)
    if keyword not in allowed:
        raise ToolError(
            f"Statement type {keyword.upper() or '?'!r} is not allowed here; "
            f"allowed: {', '.join(sorted(k.upper() for k in allowed))}."
        )


def to_json(value: Any) -> Any:
    """Convert a value returned by asyncpg into something JSON-serialisable."""
    match value:
        case None | bool() | int() | str():
            return value
        case float():
            return value if math.isfinite(value) else str(value)
        case Decimal():
            # Keep exact precision (money, numeric) instead of rounding through float.
            return str(value)
        case dt.datetime() | dt.date() | dt.time():
            return value.isoformat()
        case dt.timedelta():
            # ISO 8601 duration, e.g. "P2D".
            return _TIMEDELTA.dump_python(value, mode="json")
        case uuid.UUID():
            return str(value)
        case asyncpg.Range():
            if value.isempty:
                return "empty"
            lo = "" if value.lower is None else to_json(value.lower)
            hi = "" if value.upper is None else to_json(value.upper)
            return f"{'[' if value.lower_inc else '('}{lo},{hi}{']' if value.upper_inc else ')'}"
        case bytes() | bytearray() | memoryview():
            return "\\x" + bytes(value).hex()
        case Mapping():
            return {str(k): to_json(v) for k, v in value.items()}
        case asyncpg.Record():
            return {k: to_json(v) for k, v in value.items()}
        case list() | tuple() | set() | frozenset():
            return [to_json(v) for v in value]
        case _:
            # Ranges, geometric types, network addresses, ... use their text form.
            return str(value)
