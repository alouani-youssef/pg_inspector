"""Unit tests for the shared helpers: JSON conversion and database-error mapping (no DB needed)."""

import datetime as dt
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal

import asyncpg
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.exc import DBAPIError, OperationalError

from src.config import Settings
from src.database import Database
from src.database.connection import UnknownDatabaseError
from src.tools import common
from src.tools.common import db_connection, to_json

# --------------------------------------------------------------------------- to_json


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (42, 42),
        ("text", "text"),
        (1.5, 1.5),
        (float("nan"), "nan"),
        (float("inf"), "inf"),
        (Decimal("12.50"), "12.50"),  # exact, never rounded through float
        (Decimal("0.1") + Decimal("0.2"), "0.3"),
        (dt.date(2025, 1, 2), "2025-01-02"),
        (dt.time(13, 45), "13:45:00"),
        (
            dt.datetime(2025, 1, 2, 3, 4, 5, tzinfo=dt.UTC),
            "2025-01-02T03:04:05+00:00",
        ),
        (dt.timedelta(days=2), "P2D"),
        (dt.timedelta(hours=1, minutes=30), "PT1H30M"),
        (
            uuid.UUID("12345678-1234-5678-1234-567812345678"),
            "12345678-1234-5678-1234-567812345678",
        ),
        (b"\x00\xff", "\\x00ff"),
        (bytearray(b"ab"), "\\x6162"),
        (memoryview(b"a"), "\\x61"),
        ([1, Decimal("2.0"), None], [1, "2.0", None]),
        ((1, 2), [1, 2]),
        ({"a": Decimal(1), 2: [dt.date(2025, 1, 1)]}, {"a": "1", "2": ["2025-01-01"]}),
    ],
)
def test_to_json(value, expected):
    assert to_json(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (asyncpg.Range(1, 10), "[1,10)"),
        (asyncpg.Range(1, 10, lower_inc=False, upper_inc=True), "(1,10]"),
        (asyncpg.Range(None, 5), "(,5)"),  # an unbounded side is always exclusive
        (asyncpg.Range(dt.date(2025, 1, 1), None), "[2025-01-01,)"),
        (asyncpg.Range(empty=True), "empty"),
    ],
)
def test_to_json_ranges(value, expected):
    assert to_json(value) == expected


def test_to_json_unknown_types_fall_back_to_text():
    import ipaddress

    assert to_json(ipaddress.ip_address("10.0.0.1")) == "10.0.0.1"


# --------------------------------------------------------------------------- db_connection


class _FakeDatabase:
    """Stands in for `src.database.database`; `connection()` raises `exc` on enter."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    @asynccontextmanager
    async def connection(self, db=None):
        raise self.exc
        yield  # pragma: no cover


async def _error_for(monkeypatch, exc: BaseException) -> Exception:
    monkeypatch.setattr(common, "database", _FakeDatabase(exc))
    with pytest.raises(Exception) as info:
        async with db_connection():
            pass
    return info.value


def _pg_error(
    cls: type[asyncpg.PostgresError], message: str, **fields
) -> asyncpg.PostgresError:
    exc = cls(message)
    for name, value in fields.items():
        setattr(exc, name, value)
    return exc


async def test_unknown_database_becomes_tool_error(monkeypatch):
    err = await _error_for(
        monkeypatch, UnknownDatabaseError("Database 'x' does not exist.")
    )
    assert type(err) is ToolError
    assert str(err) == "Database 'x' does not exist."


async def test_postgres_error_keeps_sqlstate_detail_and_hint(monkeypatch):
    pg = _pg_error(
        asyncpg.exceptions.UndefinedColumnError,
        'column "nme" does not exist',
        detail="some detail",
        hint='Perhaps you meant "name".',
    )
    err = await _error_for(monkeypatch, pg)
    assert type(err) is ToolError
    assert str(err) == (
        '42703: column "nme" does not exist\nDETAIL: some detail\nHINT: Perhaps you meant "name".'
    )
    assert err.__cause__ is pg


async def test_postgres_error_wrapped_by_sqlalchemy_is_unwrapped(monkeypatch):
    pg = asyncpg.exceptions.DivisionByZeroError("division by zero")

    class _AdaptedError(Exception):
        """SQLAlchemy's asyncpg adapter error: the real asyncpg error is its __cause__."""

    adapted = _AdaptedError("wrapped")
    adapted.__cause__ = pg
    err = await _error_for(monkeypatch, DBAPIError("SELECT 1/0", {}, adapted))
    assert type(err) is ToolError
    assert str(err) == "22012: division by zero"


async def test_other_dbapi_error(monkeypatch):
    err = await _error_for(
        monkeypatch, DBAPIError("SELECT 1", {}, RuntimeError("boom"))
    )
    assert type(err) is ToolError
    assert str(err) == "Database error: boom"


@pytest.mark.parametrize(
    "exc",
    [
        OSError("Connection refused"),
        TimeoutError("timed out"),
        OperationalError("SELECT 1", {}, Exception("server closed the connection")),
    ],
)
async def test_connectivity_failures_report_unavailable(monkeypatch, exc):
    err = await _error_for(monkeypatch, exc)
    assert type(err) is ToolError
    # OperationalError is a DBAPIError, so it is reported as a database error.
    assert str(err).startswith(("Database unavailable:", "Database error:"))


async def test_tool_error_passes_through_unchanged(monkeypatch):
    original = ToolError("already friendly")
    assert await _error_for(monkeypatch, original) is original


async def test_programming_errors_are_not_swallowed(monkeypatch):
    # A bug in a tool must not be disguised as a database problem.
    err = await _error_for(monkeypatch, KeyError("oid"))
    assert type(err) is KeyError


async def test_errors_raised_inside_the_block_are_mapped(monkeypatch):
    @asynccontextmanager
    async def connection(db=None):
        yield object()

    monkeypatch.setattr(common.database, "connection", connection)
    with pytest.raises(ToolError, match="^42P01: missing$"):
        async with db_connection():
            raise asyncpg.exceptions.UndefinedTableError("missing")


async def test_unreachable_server_reports_database_unavailable(monkeypatch):
    # Nothing listens on port 1: a real connection attempt fails fast.
    dead = Database(
        Settings(
            _env_file=None,
            DATABASE_HOST="127.0.0.1",
            DATABASE_PORT=1,
            DATABASE_CONNECT_TIMEOUT=2,
        )
    )
    await dead.connect()  # never raises; readiness reports the outage instead
    monkeypatch.setattr(common, "database", dead)
    try:
        with pytest.raises(ToolError, match="^Database unavailable: "):
            async with db_connection():
                pass
    finally:
        await dead.disconnect()
