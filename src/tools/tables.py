"""Table-level tools: listing relations and describing their structure."""

from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text

from src.tools.common import (
    SYSTEM_SCHEMAS,
    db_connection,
    fetch_all,
    fetch_one,
    to_json,
)

RelationKind = Literal[
    "table", "partitioned table", "view", "materialized view", "foreign table"
]

_RELKIND = """
    CASE c.relkind
        WHEN 'r' THEN 'table'
        WHEN 'p' THEN 'partitioned table'
        WHEN 'v' THEN 'view'
        WHEN 'm' THEN 'materialized view'
        WHEN 'f' THEN 'foreign table'
    END
"""


# --------------------------------------------------------------------------- models


class TableInfo(BaseModel):
    schema_name: str
    name: str
    kind: RelationKind
    estimated_rows: int | None = Field(
        description="Planner estimate; NULL if never analyzed."
    )
    total_size_bytes: int
    comment: str | None


class ColumnInfo(BaseModel):
    name: str
    type: str
    nullable: bool
    default: str | None
    identity: Literal["always", "by default"] | None
    generated: str | None = Field(
        description="Expression of a STORED/VIRTUAL generated column."
    )
    comment: str | None


class ConstraintInfo(BaseModel):
    name: str
    type: Literal[
        "primary key",
        "foreign key",
        "unique",
        "check",
        "exclusion",
        "not null",
        "trigger",
    ]
    definition: str


class IndexInfo(BaseModel):
    name: str
    is_primary: bool
    is_unique: bool
    is_valid: bool
    size_bytes: int
    definition: str


class ReferencingForeignKey(BaseModel):
    name: str
    from_table: str
    definition: str


class TriggerInfo(BaseModel):
    name: str
    enabled: bool
    definition: str


class TableDescription(BaseModel):
    schema_name: str
    name: str
    kind: RelationKind
    comment: str | None
    estimated_rows: int | None
    total_size_bytes: int
    columns: list[ColumnInfo]
    constraints: list[ConstraintInfo]
    indexes: list[IndexInfo]
    referenced_by: list[ReferencingForeignKey] = Field(
        description="Foreign keys in other tables pointing here."
    )
    triggers: list[TriggerInfo]
    view_definition: str | None = Field(
        description="SQL body for views and materialized views."
    )


# --------------------------------------------------------------------------- queries

_LIST_TABLES = text(f"""
    SELECT n.nspname AS schema_name,
           c.relname AS name,
           {_RELKIND} AS kind,
           CASE WHEN c.reltuples >= 0 THEN c.reltuples::bigint END AS estimated_rows,
           pg_total_relation_size(c.oid) AS total_size_bytes,
           obj_description(c.oid, 'pg_class') AS comment
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
      AND NOT c.relispartition
      AND CASE WHEN CAST(:schema AS text) IS NULL
               THEN n.nspname NOT IN :system AND n.nspname NOT LIKE 'pg\\_%'
               ELSE n.nspname = :schema END
    ORDER BY n.nspname, c.relname
""").bindparams(bindparam("system", expanding=True))

_RELATION = text(f"""
    SELECT c.oid,
           {_RELKIND} AS kind,
           obj_description(c.oid, 'pg_class') AS comment,
           CASE WHEN c.reltuples >= 0 THEN c.reltuples::bigint END AS estimated_rows,
           pg_total_relation_size(c.oid) AS total_size_bytes,
           CASE WHEN c.relkind IN ('v', 'm') THEN pg_get_viewdef(c.oid, true) END AS view_definition
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema AND c.relname = :table
      AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
""")

_COLUMNS = text("""
    SELECT a.attname AS name,
           format_type(a.atttypid, a.atttypmod) AS type,
           NOT a.attnotnull AS nullable,
           CASE WHEN a.attgenerated = '' THEN pg_get_expr(ad.adbin, ad.adrelid) END AS "default",
           CASE a.attidentity WHEN 'a' THEN 'always' WHEN 'd' THEN 'by default' END AS identity,
           CASE WHEN a.attgenerated <> '' THEN pg_get_expr(ad.adbin, ad.adrelid) END AS generated,
           col_description(a.attrelid, a.attnum) AS comment
    FROM pg_attribute a
    LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
    WHERE a.attrelid = :oid AND a.attnum > 0 AND NOT a.attisdropped
    ORDER BY a.attnum
""")

_CONSTRAINTS = text("""
    SELECT conname AS name,
           CASE contype
               WHEN 'p' THEN 'primary key' WHEN 'f' THEN 'foreign key'
               WHEN 'u' THEN 'unique'      WHEN 'c' THEN 'check'
               WHEN 'x' THEN 'exclusion'   WHEN 'n' THEN 'not null'
               WHEN 't' THEN 'trigger'
           END AS type,
           pg_get_constraintdef(oid, true) AS definition
    FROM pg_constraint
    WHERE conrelid = :oid AND contype <> 'n'
    ORDER BY array_position(ARRAY['p','u','f','c','x','t']::"char"[], contype), conname
""")

_INDEXES = text("""
    SELECT ic.relname AS name,
           i.indisprimary AS is_primary,
           i.indisunique AS is_unique,
           i.indisvalid AS is_valid,
           pg_relation_size(ic.oid) AS size_bytes,
           pg_get_indexdef(i.indexrelid) AS definition
    FROM pg_index i
    JOIN pg_class ic ON ic.oid = i.indexrelid
    WHERE i.indrelid = :oid
    ORDER BY i.indisprimary DESC, ic.relname
""")

_REFERENCED_BY = text("""
    SELECT con.conname AS name,
           con.conrelid::regclass::text AS from_table,
           pg_get_constraintdef(con.oid, true) AS definition
    FROM pg_constraint con
    WHERE con.confrelid = :oid AND con.contype = 'f'
    ORDER BY 2, 1
""")

_TRIGGERS = text("""
    SELECT tgname AS name,
           tgenabled <> 'D' AS enabled,
           pg_get_triggerdef(oid, true) AS definition
    FROM pg_trigger
    WHERE tgrelid = :oid AND NOT tgisinternal
    ORDER BY tgname
""")


# --------------------------------------------------------------------------- tools


async def list_tables(
    schema: Annotated[
        str | None,
        Field(description="Only this schema. Omit to list every non-system schema."),
    ] = None,
) -> list[TableInfo]:
    """List tables, views, materialized views and foreign tables, with estimated row counts and sizes."""
    params = {"schema": schema, "system": list(SYSTEM_SCHEMAS)}
    async with db_connection() as conn:
        rows = await fetch_all(conn, _LIST_TABLES, params)
    return [TableInfo.model_validate(dict(r)) for r in rows]


async def describe_table(
    table: Annotated[
        str,
        Field(
            description="Table, view or materialized view name (case-sensitive, unquoted)."
        ),
    ],
    schema: Annotated[
        str, Field(description="Schema containing the table.")
    ] = "public",
) -> TableDescription:
    """Describe a table or view: columns, constraints, indexes, incoming foreign keys, triggers and comments."""
    async with db_connection() as conn:
        head = await fetch_one(conn, _RELATION, {"schema": schema, "table": table})
        if head is None:
            raise ToolError(
                f"Relation {schema}.{table} not found. Use list_tables to see what exists."
            )
        oid = {"oid": head["oid"]}
        columns = await fetch_all(conn, _COLUMNS, oid)
        constraints = await fetch_all(conn, _CONSTRAINTS, oid)
        indexes = await fetch_all(conn, _INDEXES, oid)
        referenced_by = await fetch_all(conn, _REFERENCED_BY, oid)
        triggers = await fetch_all(conn, _TRIGGERS, oid)

    def models[M: BaseModel](model: type[M], rows: Any) -> list[M]:
        return [model.model_validate(to_json(dict(r))) for r in rows]

    return TableDescription(
        schema_name=schema,
        name=table,
        kind=head["kind"],
        comment=head["comment"],
        estimated_rows=head["estimated_rows"],
        total_size_bytes=head["total_size_bytes"],
        view_definition=head["view_definition"],
        columns=models(ColumnInfo, columns),
        constraints=models(ConstraintInfo, constraints),
        indexes=models(IndexInfo, indexes),
        referenced_by=models(ReferencingForeignKey, referenced_by),
        triggers=models(TriggerInfo, triggers),
    )
