"""Schema-level tools."""

from typing import Annotated

from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text

from src.tools.common import SYSTEM_SCHEMAS, db_connection, fetch_all


class SchemaInfo(BaseModel):
    name: str
    owner: str
    comment: str | None


_LIST_SCHEMAS = text("""
    SELECT n.nspname AS name,
           pg_get_userbyid(n.nspowner) AS owner,
           obj_description(n.oid, 'pg_namespace') AS comment
    FROM pg_namespace n
    WHERE :include_system
       OR (n.nspname NOT IN :system AND n.nspname NOT LIKE 'pg\\_%')
    ORDER BY n.nspname
""").bindparams(bindparam("system", expanding=True))


async def list_schemas(
    include_system: Annotated[
        bool,
        Field(
            description="Also list pg_catalog, information_schema, pg_toast and pg_temp_*."
        ),
    ] = False,
) -> list[SchemaInfo]:
    """List the schemas of the current database."""
    params = {"include_system": include_system, "system": list(SYSTEM_SCHEMAS)}
    async with db_connection() as conn:
        rows = await fetch_all(conn, _LIST_SCHEMAS, params)
    return [SchemaInfo.model_validate(dict(r)) for r in rows]
