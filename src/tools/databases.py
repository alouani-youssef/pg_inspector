"""Database-level tools."""

from pydantic import BaseModel, Field
from sqlalchemy import text

from src.tools.common import db_connection, fetch_all


class DatabaseInfo(BaseModel):
    name: str
    owner: str
    encoding: str
    size_bytes: int | None = Field(
        description="NULL when the role may not connect to it."
    )
    is_current: bool
    comment: str | None


_LIST_DATABASES = text("""
    SELECT d.datname AS name,
           pg_get_userbyid(d.datdba) AS owner,
           pg_encoding_to_char(d.encoding) AS encoding,
           CASE WHEN has_database_privilege(d.oid, 'CONNECT')
                THEN pg_database_size(d.oid) END AS size_bytes,
           d.datname = current_database() AS is_current,
           shobj_description(d.oid, 'pg_database') AS comment
    FROM pg_database d
    WHERE NOT d.datistemplate
    ORDER BY d.datname
""")


async def list_databases() -> list[DatabaseInfo]:
    """List the databases on the PostgreSQL server (templates excluded), with owner, encoding and size."""
    async with db_connection() as conn:
        rows = await fetch_all(conn, _LIST_DATABASES)
    return [DatabaseInfo.model_validate(dict(r)) for r in rows]
