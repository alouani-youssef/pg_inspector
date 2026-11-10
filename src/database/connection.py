"""PostgreSQL connection management.

A PostgreSQL connection is bound to a single database, so catalog queries about another
database need their own connection. The configured database (`DATABASE_DB`) gets the main
pool; any other database on the same server gets a small pool created lazily on first use.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from src.config import Settings

logger = logging.getLogger(__name__)


class UnknownDatabaseError(LookupError):
    """The requested database does not exist on the server or does not accept connections."""


_DATABASE_EXISTS = text("""
    SELECT datallowconn
    FROM pg_database
    WHERE datname = :name AND NOT datistemplate
""")


class Database:
    """Owns the SQLAlchemy async engines. Call `connect()` on startup and `disconnect()` on shutdown."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None
        # Pools for databases other than the configured one, keyed by database name.
        self._extra_engines: dict[str, AsyncEngine] = {}
        self._extra_lock = asyncio.Lock()

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError(
                "Database is not connected; call `await database.connect()` first."
            )
        return self._engine

    @property
    def is_connected(self) -> bool:
        return self._engine is not None

    def _create_engine(self, database: str, *, pool_size: int) -> AsyncEngine:
        s = self._settings
        return create_async_engine(
            s.url_for(database),
            echo=s.DATABASE_ECHO,
            pool_size=pool_size,
            max_overflow=s.DATABASE_MAX_OVERFLOW,
            pool_timeout=s.DATABASE_POOL_TIMEOUT,
            pool_recycle=s.DATABASE_POOL_RECYCLE,
            pool_pre_ping=True,
            connect_args={
                "timeout": s.DATABASE_CONNECT_TIMEOUT,
                # Session defaults applied to every pooled connection.
                "server_settings": {
                    "application_name": "pg-inspector",
                    # This is an inspector: every transaction is read-only by default.
                    "default_transaction_read_only": "on",
                    "statement_timeout": str(s.DATABASE_STATEMENT_TIMEOUT_MS),
                },
            },
        )

    async def connect(self) -> None:
        """Create the connection pool and probe the server. Does not raise if PostgreSQL is down:
        the pool reconnects lazily and `/ready` reports the outage."""
        if self._engine is not None:
            return
        s = self._settings
        self._engine = self._create_engine(
            s.DATABASE_DB, pool_size=s.DATABASE_POOL_SIZE
        )
        if await self.ping():
            logger.info(
                "Connected to PostgreSQL at %s:%s/%s",
                s.DATABASE_HOST,
                s.DATABASE_PORT,
                s.DATABASE_DB,
            )
        else:
            logger.warning(
                "PostgreSQL at %s:%s/%s is unreachable; will retry on demand",
                s.DATABASE_HOST,
                s.DATABASE_PORT,
                s.DATABASE_DB,
            )

    async def disconnect(self) -> None:
        """Close every pooled connection."""
        if self._engine is None:
            return
        async with self._extra_lock:
            extra = list(self._extra_engines.values())
            self._extra_engines.clear()
        for engine in extra:
            await engine.dispose()
        await self._engine.dispose()
        self._engine = None
        logger.info("PostgreSQL connection pools closed")

    async def ping(self) -> bool:
        """Return True when a `SELECT 1` round-trip succeeds."""
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.debug("PostgreSQL ping failed", exc_info=True)
            return False

    async def _engine_for(self, database: str | None) -> AsyncEngine:
        """The engine for `database` (None means the configured database).

        Other databases are checked against `pg_database` before a pool is created, so a bad
        name never opens a connection or leaves an engine behind."""
        if database is None or database == self._settings.DATABASE_DB:
            return self.engine
        if (engine := self._extra_engines.get(database)) is not None:
            return engine
        async with self._extra_lock:
            if (engine := self._extra_engines.get(database)) is not None:
                return engine
            async with self.engine.connect() as conn:
                allow_conn = (
                    await conn.execute(_DATABASE_EXISTS, {"name": database})
                ).scalar_one_or_none()
            if allow_conn is None:
                raise UnknownDatabaseError(f"Database {database!r} does not exist.")
            if not allow_conn:
                raise UnknownDatabaseError(
                    f"Database {database!r} does not accept connections."
                )
            engine = self._create_engine(database, pool_size=1)
            self._extra_engines[database] = engine
            logger.info("Opened connection pool for database %s", database)
            return engine

    @asynccontextmanager
    async def connection(
        self, database: str | None = None
    ) -> AsyncIterator[AsyncConnection]:
        """Yield a pooled connection to `database` (default: the configured one) inside a
        read-only transaction that is always rolled back."""
        engine = await self._engine_for(database)
        async with engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            try:
                yield conn
            finally:
                await conn.rollback()
