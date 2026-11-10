"""SQL validation layer for client-supplied SQL.

Every statement is parsed with libpg_query (PostgreSQL's own parser, via `pglast`), so the
checks see exactly the syntax tree the server would execute. There is no keyword or regex
matching, which comments, quoting and odd whitespace could get around.

This layer runs on top of the database's own protections (a READ ONLY transaction that is
always rolled back, plus a statement timeout). Its job is to reject anything that is not a
plain read *before* it reaches PostgreSQL, including things the read-only transaction does
not stop:

* changing session settings on a pooled connection (`set_config`),
* taking session-level advisory locks that outlive the transaction,
* reading server files, signalling backends, sleeping,
* running SQL from strings, which skips validation (`query_to_xml`, `dblink`, ...),
* reading credential catalogs (`pg_authid`, `pg_shadow`, ...).

User-defined functions can still do anything their owner allows, and no parser can see
inside them. The final boundary is the privileges of the database role, so run the
server as a dedicated read-only role (e.g. one granted `pg_read_all_data`), not a superuser.
"""

from dataclasses import dataclass
from enum import StrEnum

from pglast import ast, parse_sql
from pglast.parser import ParseError
from pglast.visitors import Visitor


class SQLValidationError(ValueError):
    """The SQL was rejected. `code` is a stable, machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class StatementKind(StrEnum):
    SELECT = "SELECT"
    SHOW = "SHOW"
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    MERGE = "MERGE"


@dataclass(frozen=True, slots=True)
class ValidatedSQL:
    """SQL that passed validation, ready to be sent unchanged to the server."""

    sql: str
    kind: StatementKind


# --------------------------------------------------------------------------- policy

_STATEMENT_KINDS: dict[type[ast.Node], StatementKind] = {
    ast.SelectStmt: StatementKind.SELECT,  # also VALUES and TABLE
    ast.VariableShowStmt: StatementKind.SHOW,
    ast.InsertStmt: StatementKind.INSERT,
    ast.UpdateStmt: StatementKind.UPDATE,
    ast.DeleteStmt: StatementKind.DELETE,
    ast.MergeStmt: StatementKind.MERGE,
}

_READS = frozenset({StatementKind.SELECT, StatementKind.SHOW})
# EXPLAIN without ANALYZE plans the statement but never runs it.
_PLANNABLE = frozenset(
    {
        StatementKind.SELECT,
        StatementKind.INSERT,
        StatementKind.UPDATE,
        StatementKind.DELETE,
        StatementKind.MERGE,
    }
)
_DATA_MODIFYING = (ast.InsertStmt, ast.UpdateStmt, ast.DeleteStmt, ast.MergeStmt)

# Functions that are never allowed, matched on the unqualified, lower-cased name so that
# `pg_catalog.set_config` and `SET_CONFIG` are both caught.
DENIED_FUNCTIONS = frozenset(
    {
        # Session state that persists on the pooled connection.
        "set_config",
        # Sleeping holds a pooled connection.
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        # Signalling and server administration.
        "pg_cancel_backend",
        "pg_terminate_backend",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_promote",
        "pg_log_backend_memory_contexts",
        "pg_backup_start",
        "pg_backup_stop",
        "pg_switch_wal",
        "pg_create_restore_point",
        "pg_import_system_collations",
        "pg_logical_emit_message",
        "pg_notify",
        # Server filesystem access.
        "pg_stat_file",
        "pg_file_write",
        "pg_file_rename",
        "pg_file_unlink",
        "pg_file_sync",
        # Running SQL from a string, which skips this validator.
        "query_to_xml",
        "query_to_xmlschema",
        "query_to_xml_and_xmlschema",
        "cursor_to_xml",
        "cursor_to_xmlschema",
        "table_to_xml",
        "table_to_xmlschema",
        "table_to_xml_and_xmlschema",
        "schema_to_xml",
        "schema_to_xmlschema",
        "schema_to_xml_and_xmlschema",
        "database_to_xml",
        "database_to_xmlschema",
        "database_to_xml_and_xmlschema",
        # Sequence side effects and transaction-id assignment.
        "nextval",
        "setval",
        "txid_current",
        "pg_current_xact_id",
    }
)

DENIED_FUNCTION_PREFIXES = (
    "pg_advisory_",  # session-level locks outlive the rolled-back transaction
    "pg_try_advisory_",
    "pg_read_",  # pg_read_file, pg_read_binary_file
    "pg_ls_",  # pg_ls_dir, pg_ls_logdir, pg_ls_waldir, ...
    "lo_",  # large objects, including lo_import / lo_export on the server filesystem
    "dblink",  # connects elsewhere and runs arbitrary SQL there
    "pg_stat_reset",
    "pg_create_",  # replication slots
    "pg_drop_",
    "pg_replication_",
    "pg_wal_",
)

# Catalogs holding credentials or server configuration; denied with or without a schema.
DENIED_RELATIONS = frozenset(
    {
        "pg_authid",
        "pg_shadow",
        "pg_user_mapping",
        "pg_user_mappings",
        "pg_hba_file_rules",
        "pg_ident_file_mappings",
        "pg_file_settings",
    }
)

DEFAULT_MAX_LENGTH = 100_000


# --------------------------------------------------------------------------- public API


def validate_query(sql: str, *, max_length: int = DEFAULT_MAX_LENGTH) -> ValidatedSQL:
    """Validate SQL for `query_execute`: one SELECT / VALUES / TABLE / WITH or SHOW statement."""
    return _validate(sql, allowed=_READS, max_length=max_length)


def validate_explain(
    sql: str, *, analyze: bool, max_length: int = DEFAULT_MAX_LENGTH
) -> ValidatedSQL:
    """Validate the statement to be wrapped in EXPLAIN.

    Without ANALYZE nothing runs, so DML plans may be inspected. With ANALYZE the statement
    really runs, so only reads are allowed."""
    allowed = _READS - {StatementKind.SHOW} if analyze else _PLANNABLE
    return _validate(sql, allowed=allowed, max_length=max_length)


# --------------------------------------------------------------------------- internals


def _validate(
    sql: str, *, allowed: frozenset[StatementKind], max_length: int
) -> ValidatedSQL:
    if not sql or not sql.strip():
        raise SQLValidationError("empty", "SQL must not be empty.")
    if len(sql) > max_length:
        raise SQLValidationError(
            "too_long", f"SQL is {len(sql)} characters; the limit is {max_length}."
        )
    if "\x00" in sql:
        raise SQLValidationError("nul_byte", "SQL must not contain NUL bytes.")

    try:
        statements = parse_sql(sql)
    except ParseError as exc:
        raise SQLValidationError("syntax_error", f"SQL syntax error: {exc}") from exc

    if not statements:
        raise SQLValidationError("empty", "SQL contains no statement.")
    if len(statements) != 1:
        raise SQLValidationError(
            "multiple_statements",
            f"Exactly one statement is allowed; got {len(statements)}.",
        )
    stmt = statements[0].stmt
    if stmt is None:  # e.g. input that is only comments or semicolons
        raise SQLValidationError("empty", "SQL contains no statement.")

    kind = _STATEMENT_KINDS.get(type(stmt))
    if kind is None or kind not in allowed:
        raise SQLValidationError(
            "statement_not_allowed",
            f"{_describe(stmt)} statements are not allowed here; allowed: "
            f"{', '.join(sorted(allowed))}.",
        )

    _TreeChecker(top_level=stmt)(stmt)
    return ValidatedSQL(sql=sql, kind=kind)


def _describe(stmt: ast.Node) -> str:
    kind = _STATEMENT_KINDS.get(type(stmt))
    return kind.value if kind else type(stmt).__name__.removesuffix("Stmt").upper()


def _is_denied_function(name: str) -> bool:
    return name in DENIED_FUNCTIONS or name.startswith(DENIED_FUNCTION_PREFIXES)


class _TreeChecker(Visitor):  # type: ignore[misc]
    """Walks the whole syntax tree (subqueries, CTEs, FROM-functions, ...) and raises on the
    first construct that is not allowed."""

    def __init__(self, top_level: ast.Node) -> None:
        super().__init__()
        self._top_level = top_level

    def visit_SelectStmt(self, _ancestors: object, node: ast.SelectStmt) -> None:
        if node.intoClause is not None:
            raise SQLValidationError(
                "select_into", "SELECT ... INTO creates a table and is not allowed."
            )
        if node.lockingClause:
            raise SQLValidationError(
                "row_locking",
                "Row-locking clauses (FOR UPDATE / FOR SHARE / ...) are not allowed.",
            )

    def _nested_write(self, _ancestors: object, node: ast.Node) -> None:
        # The top-level DML of a plain EXPLAIN was already allowed by statement policy.
        if node is not self._top_level:
            raise SQLValidationError(
                "data_modifying_cte",
                f"Nested {_describe(node)} (e.g. in a WITH clause) is not allowed.",
            )

    visit_InsertStmt = _nested_write
    visit_UpdateStmt = _nested_write
    visit_DeleteStmt = _nested_write
    visit_MergeStmt = _nested_write

    def visit_FuncCall(self, _ancestors: object, node: ast.FuncCall) -> None:
        parts = [part.sval or "" for part in node.funcname or ()]
        if parts and _is_denied_function(parts[-1].lower()):
            qualified = ".".join(parts)
            raise SQLValidationError(
                "function_not_allowed", f"Function {qualified}() is not allowed."
            )

    def visit_RangeVar(self, _ancestors: object, node: ast.RangeVar) -> None:
        if (node.relname or "").lower() in DENIED_RELATIONS:
            raise SQLValidationError(
                "relation_not_allowed", f"Access to {node.relname} is not allowed."
            )
