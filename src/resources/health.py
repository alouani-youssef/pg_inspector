"""`pg-inspector://database/health`: is the configured database healthy right now?

A point-in-time snapshot of the signals worth checking first: connection pressure, cache
hit and rollback ratios, long-running and blocked sessions, transaction ID wraparound,
vacuum backlog and indexes that are invalid or never used. `warnings` turns those signals
into plain sentences using the thresholds below, and `status` is "warning" when there is any.

Counters in pg_stat_database / pg_stat_user_* are cumulative since `stats_reset`. Query text
of other roles' sessions is only visible with `pg_read_all_stats` (or as a superuser).
"""

import datetime as dt

from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text

from src.resources.common import resource_connection
from src.tools.common import SYSTEM_SCHEMAS, fetch_all, fetch_one, to_json

HEALTH_URI = "pg-inspector://database/health"

# Thresholds behind `warnings`.
CACHE_HIT_RATIO_MIN = 0.99
CONNECTION_USAGE_MAX = 0.8
LONG_RUNNING_SECONDS = 300
DEAD_TUPLE_RATIO_MAX = 0.2
DEAD_TUPLES_MIN = 1000  # Ignore the dead-tuple ratio of tiny tables.

# Rows listed at most per section; query text is cut to QUERY_PREVIEW characters.
LIST_LIMIT = 10
QUERY_PREVIEW = 300


# --------------------------------------------------------------------------- models


class ConnectionStats(BaseModel):
    database_total: int = Field(
        description="Client sessions connected to this database."
    )
    by_state: dict[str, int] = Field(
        description="This database's client sessions per state."
    )
    server_total: int = Field(description="Client sessions on the whole server.")
    max_connections: int
    usage_ratio: float = Field(description="server_total / max_connections.")


class ActivityStats(BaseModel):
    cache_hit_ratio: float | None = Field(
        description="Buffer hits / (hits + reads); NULL before any read."
    )
    rollback_ratio: float | None = Field(
        description="Rollbacks / transactions; NULL before any transaction. Includes this "
        "server's own sessions, which always roll back their read-only transactions, so it "
        "is reported but never warned on."
    )
    commits: int
    rollbacks: int
    deadlocks: int
    conflicts: int = Field(
        description="Queries cancelled by recovery conflicts (standbys)."
    )
    temp_files: int
    temp_bytes: int
    stats_reset: dt.datetime | None


class WraparoundStats(BaseModel):
    xid_age: int = Field(
        description="Age of the database's oldest unfrozen transaction ID."
    )
    autovacuum_freeze_max_age: int
    ratio_to_wraparound: float = Field(
        description="xid_age / 2^31; 1.0 means the server stops writes."
    )


class SessionInfo(BaseModel):
    pid: int
    user_name: str | None
    application_name: str | None
    state: str | None
    duration_seconds: float = Field(
        description="Time since the session's transaction (or query) started."
    )
    wait_event_type: str | None
    wait_event: str | None
    query: str | None


class BlockedSession(BaseModel):
    pid: int
    blocked_by: list[int]
    user_name: str | None
    waiting_seconds: float | None
    wait_event_type: str | None
    query: str | None


class VacuumCandidate(BaseModel):
    schema_name: str
    name: str
    live_tuples: int
    dead_tuples: int
    dead_ratio: float | None
    modified_since_analyze: int
    last_vacuum: dt.datetime | None = Field(description="Latest manual or auto vacuum.")
    last_analyze: dt.datetime | None = Field(
        description="Latest manual or auto analyze."
    )


class IndexIssue(BaseModel):
    schema_name: str
    table_name: str
    index_name: str
    size_bytes: int


class HealthSnapshot(BaseModel):
    status: str = Field(description='"ok", or "warning" when `warnings` is not empty.')
    warnings: list[str]
    checked_at: dt.datetime
    database: str
    in_recovery: bool = Field(description="True on a standby.")
    replicas: int = Field(description="Standbys streaming from this server.")
    connections: ConnectionStats
    activity: ActivityStats
    wraparound: WraparoundStats
    long_running: list[SessionInfo] = Field(
        description=f"Non-idle sessions whose transaction is older than {LONG_RUNNING_SECONDS}s, oldest first."
    )
    blocked: list[BlockedSession] = Field(
        description="Sessions waiting on another session's lock."
    )
    vacuum_candidates: list[VacuumCandidate] = Field(
        description="Tables with the most dead tuples."
    )
    invalid_indexes: list[IndexIssue] = Field(
        description="Indexes left invalid (e.g. a failed CREATE INDEX CONCURRENTLY)."
    )
    unused_indexes: list[IndexIssue] = Field(
        description="Largest non-unique indexes never scanned since stats_reset."
    )


# --------------------------------------------------------------------------- queries

_CLIENT = "a.backend_type = 'client backend'"
_THIS_DB = "a.datname = current_database()"
_USER_SCHEMA = "schemaname NOT IN :system AND schemaname NOT LIKE 'pg\\_%'"

_DATABASE = text(f"""
    SELECT now() AS checked_at,
           current_database() AS database,
           pg_is_in_recovery() AS in_recovery,
           (SELECT count(*) FROM pg_stat_replication) AS replicas,
           (SELECT count(*) FROM pg_stat_activity a WHERE {_CLIENT}) AS server_total,
           current_setting('max_connections')::int AS max_connections,
           (s.blks_hit::float8 / NULLIF(s.blks_hit + s.blks_read, 0)) AS cache_hit_ratio,
           (s.xact_rollback::float8 / NULLIF(s.xact_commit + s.xact_rollback, 0)) AS rollback_ratio,
           s.xact_commit AS commits,
           s.xact_rollback AS rollbacks,
           s.deadlocks,
           s.conflicts,
           s.temp_files,
           s.temp_bytes,
           s.stats_reset,
           age(d.datfrozenxid)::bigint AS xid_age,
           current_setting('autovacuum_freeze_max_age')::bigint AS autovacuum_freeze_max_age
    FROM pg_stat_database s
    JOIN pg_database d ON d.oid = s.datid
    WHERE s.datname = current_database()
""")

_BY_STATE = text(f"""
    SELECT coalesce(a.state, 'unknown') AS state, count(*) AS sessions
    FROM pg_stat_activity a
    WHERE {_CLIENT} AND {_THIS_DB}
    GROUP BY 1
    ORDER BY 1
""")

_LONG_RUNNING = text(f"""
    SELECT a.pid,
           a.usename AS user_name,
           a.application_name,
           a.state,
           extract(epoch FROM now() - coalesce(a.xact_start, a.query_start))::float8 AS duration_seconds,
           a.wait_event_type,
           a.wait_event,
           left(a.query, :preview) AS query
    FROM pg_stat_activity a
    WHERE {_CLIENT} AND {_THIS_DB}
      AND a.pid <> pg_backend_pid()
      AND a.state IS DISTINCT FROM 'idle'
      AND now() - coalesce(a.xact_start, a.query_start) > make_interval(secs => :threshold)
    ORDER BY duration_seconds DESC
    LIMIT :limit
""")

_BLOCKED = text(f"""
    SELECT a.pid,
           pg_blocking_pids(a.pid) AS blocked_by,
           a.usename AS user_name,
           extract(epoch FROM now() - a.query_start)::float8 AS waiting_seconds,
           a.wait_event_type,
           left(a.query, :preview) AS query
    FROM pg_stat_activity a
    WHERE {_THIS_DB} AND cardinality(pg_blocking_pids(a.pid)) > 0
    ORDER BY a.query_start
    LIMIT :limit
""")

_VACUUM = text(f"""
    SELECT schemaname AS schema_name,
           relname AS name,
           n_live_tup AS live_tuples,
           n_dead_tup AS dead_tuples,
           (n_dead_tup::float8 / NULLIF(n_live_tup + n_dead_tup, 0)) AS dead_ratio,
           n_mod_since_analyze AS modified_since_analyze,
           greatest(last_vacuum, last_autovacuum) AS last_vacuum,
           greatest(last_analyze, last_autoanalyze) AS last_analyze
    FROM pg_stat_user_tables
    WHERE n_dead_tup > 0 AND {_USER_SCHEMA}
    ORDER BY n_dead_tup DESC, schemaname, relname
    LIMIT :limit
""").bindparams(bindparam("system", expanding=True))

_INDEXES = f"""
    SELECT s.schemaname AS schema_name,
           s.relname AS table_name,
           s.indexrelname AS index_name,
           pg_relation_size(s.indexrelid) AS size_bytes
    FROM pg_stat_user_indexes s
    JOIN pg_index i ON i.indexrelid = s.indexrelid
    WHERE {{condition}} AND {_USER_SCHEMA.replace("schemaname", "s.schemaname")}
    ORDER BY size_bytes DESC, s.schemaname, s.indexrelname
    LIMIT :limit
"""
_INVALID_INDEXES = text(_INDEXES.format(condition="NOT i.indisvalid")).bindparams(
    bindparam("system", expanding=True)
)
_UNUSED_INDEXES = text(
    _INDEXES.format(condition="s.idx_scan = 0 AND i.indisvalid AND NOT i.indisunique")
).bindparams(bindparam("system", expanding=True))


# --------------------------------------------------------------------------- resource


async def database_health() -> str:
    """Health snapshot of the configured database: status and warnings, connection usage,
    cache hit and rollback ratios, long-running and blocked sessions, transaction ID
    wraparound, vacuum backlog, and invalid or unused indexes."""
    system = {"system": list(SYSTEM_SCHEMAS)}
    listing = {"limit": LIST_LIMIT, "preview": QUERY_PREVIEW}
    async with resource_connection() as conn:
        db = dict(await fetch_one(conn, _DATABASE) or {})
        by_state = await fetch_all(conn, _BY_STATE)
        long_running = await fetch_all(
            conn, _LONG_RUNNING, {**listing, "threshold": float(LONG_RUNNING_SECONDS)}
        )
        blocked = await fetch_all(conn, _BLOCKED, listing)
        vacuum = await fetch_all(conn, _VACUUM, {**system, "limit": LIST_LIMIT})
        invalid = await fetch_all(
            conn, _INVALID_INDEXES, {**system, "limit": LIST_LIMIT}
        )
        unused = await fetch_all(conn, _UNUSED_INDEXES, {**system, "limit": LIST_LIMIT})

    sessions = {r["state"]: r["sessions"] for r in by_state}
    snapshot = HealthSnapshot(
        status="ok",
        warnings=[],
        checked_at=db["checked_at"],
        database=db["database"],
        in_recovery=db["in_recovery"],
        replicas=db["replicas"],
        connections=ConnectionStats(
            database_total=sum(sessions.values()),
            by_state=sessions,
            server_total=db["server_total"],
            max_connections=db["max_connections"],
            usage_ratio=db["server_total"] / db["max_connections"],
        ),
        activity=ActivityStats.model_validate(db),
        wraparound=WraparoundStats(
            xid_age=db["xid_age"],
            autovacuum_freeze_max_age=db["autovacuum_freeze_max_age"],
            ratio_to_wraparound=db["xid_age"] / 2**31,
        ),
        long_running=[
            SessionInfo.model_validate(to_json(dict(r))) for r in long_running
        ],
        blocked=[BlockedSession.model_validate(to_json(dict(r))) for r in blocked],
        vacuum_candidates=[VacuumCandidate.model_validate(dict(r)) for r in vacuum],
        invalid_indexes=[IndexIssue.model_validate(dict(r)) for r in invalid],
        unused_indexes=[IndexIssue.model_validate(dict(r)) for r in unused],
    )
    snapshot.warnings = health_warnings(snapshot)
    snapshot.status = "warning" if snapshot.warnings else "ok"
    return snapshot.model_dump_json(indent=2)


def health_warnings(s: HealthSnapshot) -> list[str]:
    """Plain-language warnings for the signals that cross a threshold."""
    warnings: list[str] = []
    c, a, w = s.connections, s.activity, s.wraparound
    if c.usage_ratio > CONNECTION_USAGE_MAX:
        warnings.append(
            f"Connections at {c.usage_ratio:.0%} of max_connections ({c.server_total}/{c.max_connections})."
        )
    if a.cache_hit_ratio is not None and a.cache_hit_ratio < CACHE_HIT_RATIO_MIN:
        warnings.append(
            f"Cache hit ratio is {a.cache_hit_ratio:.2%} (below {CACHE_HIT_RATIO_MIN:.0%}): "
            "the working set may not fit in shared_buffers."
        )
    if w.xid_age > w.autovacuum_freeze_max_age:
        warnings.append(
            f"Transaction ID age {w.xid_age:,} exceeds autovacuum_freeze_max_age "
            f"({w.autovacuum_freeze_max_age:,}): {w.ratio_to_wraparound:.1%} of the way to wraparound."
        )
    if idle := [
        x for x in s.long_running if (x.state or "").startswith("idle in transaction")
    ]:
        warnings.append(
            f"{len(idle)} session(s) idle in transaction for over {LONG_RUNNING_SECONDS}s "
            "(they hold locks and block vacuum)."
        )
    if busy := [x for x in s.long_running if x not in idle]:
        warnings.append(
            f"{len(busy)} session(s) running for over {LONG_RUNNING_SECONDS}s."
        )
    if s.blocked:
        warnings.append(
            f"{len(s.blocked)} session(s) waiting on locks held by other sessions."
        )
    for t in s.vacuum_candidates:
        if (
            t.dead_tuples >= DEAD_TUPLES_MIN
            and (t.dead_ratio or 0) > DEAD_TUPLE_RATIO_MAX
        ):
            warnings.append(
                f"{t.schema_name}.{t.name} has {t.dead_ratio:.0%} dead tuples ({t.dead_tuples:,}): needs VACUUM."
            )
    if s.invalid_indexes:
        names = ", ".join(f"{i.schema_name}.{i.index_name}" for i in s.invalid_indexes)
        warnings.append(f"Invalid index(es), not used by queries: {names}.")
    return warnings
