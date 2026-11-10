"""PostgreSQL connection management: one async engine (connection pool) per process."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from src.config import Settings

logger = logging.getLogger(__name__)


class Database:
    """Owns the SQLAlchemy async engine. Call `connect()` on startup and `disconnect()` on shutdown."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None

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

    async def connect(self) -> None:
        """Create the connection pool and probe the server. Does not raise if PostgreSQL is down:
        the pool reconnects lazily and `/ready` reports the outage."""
        if self._engine is not None:
            return
        s = self._settings
        self._engine = create_async_engine(
            s.database_url,
            echo=s.DATABASE_ECHO,
            pool_size=s.DATABASE_POOL_SIZE,
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
        await self._engine.dispose()
        self._engine = None
        logger.info("PostgreSQL connection pool closed")

    async def ping(self) -> bool:
        """Return True when a `SELECT 1` round-trip succeeds."""
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.debug("PostgreSQL ping failed", exc_info=True)
            return False

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        """Yield a pooled connection inside a read-only transaction that is always rolled back."""
        async with self.engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            try:
                yield conn
            finally:
                await conn.rollback()
