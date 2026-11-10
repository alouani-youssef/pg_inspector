"""`pg-inspector://database/dictionary`: what the data *means*, in one read.

A data dictionary aimed at writing correct SQL: every user relation with its comment,
columns (type, nullability, default, comment, allowed enum values, the column it points to),
keys, check constraints and the join paths in both directions (`foreign_keys` /
`referenced_by`), plus the enum and domain types of the database.
"""

from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text

from src.resources.common import resource_connection
from src.tools.common import SYSTEM_SCHEMAS, fetch_all, to_json

DICTIONARY_URI = "pg-inspector://database/dictionary"

# Relations listed at most; past this the dictionary is cut and `truncated` is set
# (use catalog_list_tables / catalog_describe_table to go further).
MAX_RELATIONS = 500


# --------------------------------------------------------------------------- models


class ColumnEntry(BaseModel):
    name: str
    type: str
    nullable: bool
    default: str | None = None
    generated: str | None = Field(
        default=None, description="Expression of a generated column."
    )
    comment: str | None = None
    enum_values: list[str] | None = Field(
        default=None, description="Allowed values when the column is an enum."
    )
    references: str | None = Field(
        default=None,
        description="`schema.table.column` this column points to (single-column foreign key).",
    )


class ForeignKeyEntry(BaseModel):
    name: str
    columns: list[str]
    references_table: str = Field(description="`schema.table`")
    references_columns: list[str]


class ReferenceEntry(BaseModel):
    name: str
    from_table: str = Field(description="`schema.table` holding the foreign key.")
    from_columns: list[str]
    columns: list[str] = Field(description="Columns of this relation it points to.")


class RelationEntry(BaseModel):
    schema_name: str
    name: str
    kind: str
    comment: str | None = None
    estimated_rows: int | None = None
    primary_key: list[str] = Field(default_factory=list)
    unique: list[list[str]] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyEntry] = Field(default_factory=list)
    referenced_by: list[ReferenceEntry] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    columns: list[ColumnEntry] = Field(default_factory=list)


class EnumEntry(BaseModel):
    schema_name: str
    name: str
    values: list[str]
    comment: str | None = None


class DomainEntry(BaseModel):
    schema_name: str
    name: str
    base_type: str
    nullable: bool
    default: str | None = None
    checks: list[str]
    comment: str | None = None


class SemanticDictionary(BaseModel):
    relations: list[RelationEntry] = Field(
        description="Tables, views, materialized views and foreign tables in non-system schemas."
    )
    enums: list[EnumEntry]
    domains: list[DomainEntry]
    truncated: bool = Field(
        description=f"True when more than {MAX_RELATIONS} relations exist."
    )


# --------------------------------------------------------------------------- queries

_USER_SCHEMA = "n.nspname NOT IN :system AND n.nspname NOT LIKE 'pg\\_%'"
_USER_RELATION = f"""
    c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT c.relispartition AND {_USER_SCHEMA}
"""

_RELATIONS = text(f"""
    SELECT c.oid AS relid,
           n.nspname AS schema_name,
           c.relname AS name,
           CASE c.relkind
               WHEN 'r' THEN 'table'             WHEN 'p' THEN 'partitioned table'
               WHEN 'v' THEN 'view'              WHEN 'm' THEN 'materialized view'
               WHEN 'f' THEN 'foreign table'
           END AS kind,
           obj_description(c.oid, 'pg_class') AS comment,
           CASE WHEN c.reltuples >= 0 THEN c.reltuples::bigint END AS estimated_rows
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE {_USER_RELATION}
    ORDER BY n.nspname, c.relname
    LIMIT :limit
""").bindparams(bindparam("system", expanding=True))

_COLUMNS = text(f"""
    SELECT c.oid AS relid,
           a.attname AS name,
           format_type(a.atttypid, a.atttypmod) AS type,
           NOT a.attnotnull AS nullable,
           CASE WHEN a.attgenerated = '' THEN pg_get_expr(ad.adbin, ad.adrelid) END AS "default",
           CASE WHEN a.attgenerated <> '' THEN pg_get_expr(ad.adbin, ad.adrelid) END AS generated,
           col_description(a.attrelid, a.attnum) AS comment,
           CASE WHEN t.typtype = 'e' THEN ARRAY(
               SELECT e.enumlabel::text FROM pg_enum e
               WHERE e.enumtypid = t.oid ORDER BY e.enumsortorder)
           END AS enum_values
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
    JOIN pg_type t ON t.oid = a.atttypid
    LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
    WHERE {_USER_RELATION}
    ORDER BY c.oid, a.attnum
""").bindparams(bindparam("system", expanding=True))


def _column_names(rel: str, keys: str) -> str:
    """Names of the columns numbered by `keys` (an int2[] of attnums) in relation `rel`, in key order."""
    return f"""ARRAY(
        SELECT a.attname::text
        FROM unnest({keys}) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid = {rel} AND a.attnum = k.attnum
        ORDER BY k.ord)"""


_CONSTRAINTS = text(f"""
    SELECT con.conrelid AS relid,
           con.conname AS name,
           con.contype::text AS type,
           {_column_names("con.conrelid", "con.conkey")} AS columns,
           rn.nspname || '.' || rc.relname AS references_table,
           {_column_names("con.confrelid", "con.confkey")} AS references_columns,
           pg_get_constraintdef(con.oid, true) AS definition
    FROM pg_constraint con
    JOIN pg_class c ON c.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_class rc ON rc.oid = con.confrelid
    LEFT JOIN pg_namespace rn ON rn.oid = rc.relnamespace
    WHERE con.contype IN ('p', 'u', 'f', 'c') AND {_USER_RELATION}
    ORDER BY con.conrelid, con.conname
""").bindparams(bindparam("system", expanding=True))

_ENUMS = text(f"""
    SELECT n.nspname AS schema_name,
           t.typname AS name,
           ARRAY(SELECT e.enumlabel::text FROM pg_enum e
                 WHERE e.enumtypid = t.oid ORDER BY e.enumsortorder) AS values,
           obj_description(t.oid, 'pg_type') AS comment
    FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE t.typtype = 'e' AND {_USER_SCHEMA}
    ORDER BY n.nspname, t.typname
""").bindparams(bindparam("system", expanding=True))

_DOMAINS = text(f"""
    SELECT n.nspname AS schema_name,
           t.typname AS name,
           format_type(t.typbasetype, t.typtypmod) AS base_type,
           NOT t.typnotnull AS nullable,
           t.typdefault AS "default",
           ARRAY(SELECT pg_get_constraintdef(con.oid, true) FROM pg_constraint con
                 WHERE con.contypid = t.oid AND con.contype = 'c' ORDER BY con.conname) AS checks,
           obj_description(t.oid, 'pg_type') AS comment
    FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE t.typtype = 'd' AND {_USER_SCHEMA}
    ORDER BY n.nspname, t.typname
""").bindparams(bindparam("system", expanding=True))


# --------------------------------------------------------------------------- resource


async def database_dictionary() -> str:
    """Semantic data dictionary of the configured database: every table and view with its
    comment, columns (type, nullability, default, comment, enum values, foreign-key target),
    primary/unique keys, checks and join paths, plus enum and domain types."""
    system = {"system": list(SYSTEM_SCHEMAS)}
    async with resource_connection() as conn:
        relations = await fetch_all(
            conn, _RELATIONS, {**system, "limit": MAX_RELATIONS + 1}
        )
        columns = await fetch_all(conn, _COLUMNS, system)
        constraints = await fetch_all(conn, _CONSTRAINTS, system)
        enums = await fetch_all(conn, _ENUMS, system)
        domains = await fetch_all(conn, _DOMAINS, system)

    dictionary = SemanticDictionary(
        relations=build_relations(
            [dict(r) for r in relations[:MAX_RELATIONS]],
            [dict(r) for r in columns],
            [dict(r) for r in constraints],
        ),
        enums=[EnumEntry.model_validate(dict(r)) for r in enums],
        domains=[DomainEntry.model_validate(dict(r)) for r in domains],
        truncated=len(relations) > MAX_RELATIONS,
    )
    return dictionary.model_dump_json(indent=2, exclude_none=True)


def build_relations(
    relations: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
) -> list[RelationEntry]:
    """Assemble relation entries from catalog rows keyed by `relid`. Rows for relations not in
    `relations` (cut by MAX_RELATIONS) are ignored; `referenced_by` only lists relations kept."""
    entries = {
        r["relid"]: RelationEntry.model_validate(
            {k: v for k, v in r.items() if k != "relid"}
        )
        for r in relations
    }
    by_name = {f"{e.schema_name}.{e.name}": e for e in entries.values()}
    single_column_targets: dict[tuple[int, str], str] = {}
    incoming: dict[str, list[ReferenceEntry]] = defaultdict(list)

    for con in constraints:
        entry = entries.get(con["relid"])
        if entry is None:
            continue
        match con["type"]:
            case "p":
                entry.primary_key = con["columns"]
            case "u":
                entry.unique.append(con["columns"])
            case "c":
                entry.checks.append(con["definition"])
            case "f":
                entry.foreign_keys.append(ForeignKeyEntry.model_validate(con))
                if len(con["columns"]) == 1:
                    target = f"{con['references_table']}.{con['references_columns'][0]}"
                    single_column_targets[(con["relid"], con["columns"][0])] = target
                incoming[con["references_table"]].append(
                    ReferenceEntry(
                        name=con["name"],
                        from_table=f"{entry.schema_name}.{entry.name}",
                        from_columns=con["columns"],
                        columns=con["references_columns"],
                    )
                )

    for table, refs in incoming.items():
        if (referenced := by_name.get(table)) is not None:
            referenced.referenced_by = sorted(
                refs, key=lambda r: (r.from_table, r.name)
            )

    for col in columns:
        entry = entries.get(col["relid"])
        if entry is None:
            continue
        values = to_json({k: v for k, v in col.items() if k != "relid"})
        values["references"] = single_column_targets.get((col["relid"], col["name"]))
        entry.columns.append(ColumnEntry.model_validate(values))

    return list(entries.values())
