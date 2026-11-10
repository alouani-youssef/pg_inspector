"""`pg-inspector://database/overview`: a one-read snapshot of the configured database.

Gives a client (or the model) the lay of the land before it reaches for tools: server and
database identity, per-schema object counts, installed extensions and the largest relations.
"""

import datetime as dt

from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text

from src.resources.common import resource_connection
from src.tools.common import SYSTEM_SCHEMAS, fetch_all, fetch_one

OVERVIEW_URI = "pg-inspector://database/overview"

# How many relations `largest_relations` lists.
LARGEST_LIMIT = 10


# --------------------------------------------------------------------------- models


class DatabaseSummary(BaseModel):
    name: str
    server_version: str
    connected_as: str
    owner: str
    encoding: str
    collation: str | None = Field(
        description="NULL when the database uses a non-libc locale provider."
    )
    timezone: str
    size_bytes: int
    comment: str | None
    server_started_at: dt.datetime
    connections: int = Field(
        description="Sessions currently connected to this database."
    )
    max_connections: int = Field(description="Server-wide connection limit.")


class SchemaSummary(BaseModel):
    name: str
    owner: str
    comment: str | None
    tables: int = Field(
        description="Tables and partitioned tables (partitions not counted)."
    )
    views: int
    materialized_views: int
    foreign_tables: int
    sequences: int
    functions: int = Field(description="Functions and procedures.")
    types: int = Field(description="Enums, domains, composite and range types.")
    size_bytes: int = Field(
        description="Total size of tables, partitions and materialized views."
    )


class ExtensionSummary(BaseModel):
    name: str
    version: str
    schema_name: str


class RelationSize(BaseModel):
    schema_name: str
    name: str
    kind: str
    estimated_rows: int | None = Field(
        description="Planner estimate; NULL if never analyzed."
    )
    total_size_bytes: int


class DatabaseOverview(BaseModel):
    database: DatabaseSummary
    schemas: list[SchemaSummary] = Field(description="Non-system schemas.")
    extensions: list[ExtensionSummary]
    largest_relations: list[RelationSize] = Field(
        description=f"Up to {LARGEST_LIMIT} biggest tables and materialized views, including indexes and TOAST."
    )


# --------------------------------------------------------------------------- queries

_USER_SCHEMA = "n.nspname NOT IN :system AND n.nspname NOT LIKE 'pg\\_%'"

_DATABASE = text("""
    SELECT d.datname AS name,
           current_setting('server_version') AS server_version,
           current_user AS connected_as,
           pg_get_userbyid(d.datdba) AS owner,
           pg_encoding_to_char(d.encoding) AS encoding,
           CASE WHEN d.datlocprovider = 'c' THEN d.datcollate END AS collation,
           current_setting('TimeZone') AS timezone,
           pg_database_size(d.oid) AS size_bytes,
           shobj_description(d.oid, 'pg_database') AS comment,
           pg_postmaster_start_time() AS server_started_at,
           (SELECT count(*) FROM pg_stat_activity a WHERE a.datid = d.oid) AS connections,
           current_setting('max_connections')::int AS max_connections
    FROM pg_database d
    WHERE d.datname = current_database()
""")

_SCHEMAS = text(f"""
    SELECT n.nspname AS name,
           pg_get_userbyid(n.nspowner) AS owner,
           obj_description(n.oid, 'pg_namespace') AS comment,
           count(c.oid) FILTER (WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition) AS tables,
           count(c.oid) FILTER (WHERE c.relkind = 'v') AS views,
           count(c.oid) FILTER (WHERE c.relkind = 'm') AS materialized_views,
           count(c.oid) FILTER (WHERE c.relkind = 'f') AS foreign_tables,
           count(c.oid) FILTER (WHERE c.relkind = 'S') AS sequences,
           (SELECT count(*) FROM pg_proc p WHERE p.pronamespace = n.oid) AS functions,
           (SELECT count(*) FROM pg_type t
             WHERE t.typnamespace = n.oid
               AND (t.typtype IN ('e', 'd', 'r', 'm')
                    OR (t.typtype = 'c' AND EXISTS (
                          SELECT 1 FROM pg_class tc WHERE tc.oid = t.typrelid AND tc.relkind = 'c')))
           ) AS types,
           coalesce(sum(pg_total_relation_size(c.oid)) FILTER (WHERE c.relkind IN ('r', 'm')), 0)::bigint
               AS size_bytes
    FROM pg_namespace n
    LEFT JOIN pg_class c ON c.relnamespace = n.oid
    WHERE {_USER_SCHEMA}
    GROUP BY n.oid, n.nspname
    ORDER BY n.nspname
""").bindparams(bindparam("system", expanding=True))

_EXTENSIONS = text("""
    SELECT e.extname AS name, e.extversion AS version, n.nspname AS schema_name
    FROM pg_extension e
    JOIN pg_namespace n ON n.oid = e.extnamespace
    ORDER BY e.extname
""")

_LARGEST = text(f"""
    SELECT schema_name, name, kind, estimated_rows, total_size_bytes
    FROM (
        SELECT n.nspname AS schema_name,
               c.relname AS name,
               CASE c.relkind WHEN 'r' THEN 'table' WHEN 'p' THEN 'partitioned table'
                              ELSE 'materialized view' END AS kind,
               CASE WHEN c.reltuples >= 0 THEN c.reltuples::bigint END AS estimated_rows,
               CASE WHEN c.relkind = 'p'
                    THEN (SELECT coalesce(sum(pg_total_relation_size(pt.relid)), 0)
                          FROM pg_partition_tree(c.oid) pt)
                    ELSE pg_total_relation_size(c.oid) END::bigint AS total_size_bytes
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p', 'm')
          AND NOT c.relispartition
          AND {_USER_SCHEMA}
    ) r
    ORDER BY total_size_bytes DESC, schema_name, name
    LIMIT :limit
""").bindparams(bindparam("system", expanding=True))


# --------------------------------------------------------------------------- resource


async def database_overview() -> str:
    """Snapshot of the configured database: server version, size and connections,
    per-schema object counts, installed extensions and the largest tables."""
    system = {"system": list(SYSTEM_SCHEMAS)}
    async with resource_connection() as conn:
        database = await fetch_one(conn, _DATABASE)
        schemas = await fetch_all(conn, _SCHEMAS, system)
        extensions = await fetch_all(conn, _EXTENSIONS)
        largest = await fetch_all(conn, _LARGEST, {**system, "limit": LARGEST_LIMIT})

    overview = DatabaseOverview(
        database=DatabaseSummary.model_validate(dict(database or {})),
        schemas=[SchemaSummary.model_validate(dict(r)) for r in schemas],
        extensions=[ExtensionSummary.model_validate(dict(r)) for r in extensions],
        largest_relations=[RelationSize.model_validate(dict(r)) for r in largest],
    )
    return overview.model_dump_json(indent=2)
