"""The health snapshot resource: warning rules (no DB) and live signals (seeded DB)."""

import datetime as dt

import pytest
from sqlalchemy import text

from src.database import database
from src.resources import health
from src.resources.health import (
    HEALTH_URI,
    ActivityStats,
    ConnectionStats,
    HealthSnapshot,
    IndexIssue,
    SessionInfo,
    VacuumCandidate,
    WraparoundStats,
    health_warnings,
)
from tests.resources.conftest import read_json

# --------------------------------------------------------------------------- warnings (no DB)


def snapshot(**changes) -> HealthSnapshot:
    healthy = HealthSnapshot(
        status="ok",
        warnings=[],
        checked_at=dt.datetime.now(dt.UTC),
        database="app",
        in_recovery=False,
        replicas=0,
        connections=ConnectionStats(
            database_total=2,
            by_state={"active": 1, "idle": 1},
            server_total=2,
            max_connections=100,
            usage_ratio=0.02,
        ),
        activity=ActivityStats(
            cache_hit_ratio=0.999,
            rollback_ratio=0.0,
            commits=100,
            rollbacks=0,
            deadlocks=0,
            conflicts=0,
            temp_files=0,
            temp_bytes=0,
            stats_reset=None,
        ),
        wraparound=WraparoundStats(
            xid_age=1_000,
            autovacuum_freeze_max_age=200_000_000,
            ratio_to_wraparound=0.0,
        ),
        long_running=[],
        blocked=[],
        vacuum_candidates=[],
        invalid_indexes=[],
        unused_indexes=[],
    )
    return healthy.model_copy(update=changes)


def session(state: str) -> SessionInfo:
    return SessionInfo(
        pid=1,
        user_name="u",
        application_name="",
        state=state,
        duration_seconds=900.0,
        wait_event_type=None,
        wait_event=None,
        query="SELECT 1",
    )


def test_a_healthy_database_has_no_warnings():
    assert health_warnings(snapshot()) == []


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        (
            {
                "connections": ConnectionStats(
                    database_total=90,
                    by_state={},
                    server_total=90,
                    max_connections=100,
                    usage_ratio=0.9,
                )
            },
            "Connections at 90%",
        ),
        (
            {
                "activity": ActivityStats(
                    cache_hit_ratio=0.9,
                    rollback_ratio=0.0,
                    commits=1,
                    rollbacks=0,
                    deadlocks=0,
                    conflicts=0,
                    temp_files=0,
                    temp_bytes=0,
                    stats_reset=None,
                )
            },
            "Cache hit ratio is 90.00%",
        ),
        (
            {
                "wraparound": WraparoundStats(
                    xid_age=300_000_000,
                    autovacuum_freeze_max_age=200_000_000,
                    ratio_to_wraparound=0.14,
                )
            },
            "exceeds autovacuum_freeze_max_age",
        ),
        (
            {"long_running": [session("idle in transaction")]},
            "1 session(s) idle in transaction",
        ),
        ({"long_running": [session("active")]}, "1 session(s) running for over 300s"),
        (
            {
                "vacuum_candidates": [
                    VacuumCandidate(
                        schema_name="public",
                        name="orders",
                        live_tuples=2000,
                        dead_tuples=1500,
                        dead_ratio=0.43,
                        modified_since_analyze=0,
                        last_vacuum=None,
                        last_analyze=None,
                    )
                ]
            },
            "public.orders has 43% dead tuples",
        ),
        (
            {
                "invalid_indexes": [
                    IndexIssue(
                        schema_name="public",
                        table_name="orders",
                        index_name="orders_x",
                        size_bytes=0,
                    )
                ]
            },
            "Invalid index(es), not used by queries: public.orders_x",
        ),
    ],
)
def test_each_threshold_raises_one_warning(changes, expected):
    [warning] = health_warnings(snapshot(**changes))
    assert expected in warning


def test_rollback_ratio_is_reported_but_not_warned_on():
    # pg-inspector's own sessions always roll back, so a high ratio is expected here.
    busy = snapshot().activity.model_copy(update={"rollback_ratio": 0.9})
    assert health_warnings(snapshot(activity=busy)) == []


def test_small_tables_with_dead_tuples_are_not_flagged():
    tiny = VacuumCandidate(
        schema_name="public",
        name="t",
        live_tuples=1,
        dead_tuples=9,
        dead_ratio=0.9,
        modified_since_analyze=0,
        last_vacuum=None,
        last_analyze=None,
    )
    assert health_warnings(snapshot(vacuum_candidates=[tiny])) == []


# --------------------------------------------------------------------------- live (DB)


@pytest.mark.usefixtures("db")
async def test_snapshot_of_the_seeded_database(mcp):
    out = await read_json(mcp, HEALTH_URI)
    assert out["status"] == ("warning" if out["warnings"] else "ok")
    assert out["database"] == "app"
    assert out["in_recovery"] is False

    conns = out["connections"]
    assert conns["by_state"].get("active", 0) >= 1  # the session reading the resource
    assert conns["database_total"] == sum(conns["by_state"].values())
    assert 0 < conns["usage_ratio"] < 1

    assert 0 <= out["activity"]["cache_hit_ratio"] <= 1
    assert out["wraparound"]["xid_age"] > 0
    assert out["blocked"] == []
    assert all(i["table_name"] for i in out["unused_indexes"])

    # Problems planted by data/mocks/40_analytics_performance.sql.
    assert [(i["schema_name"], i["index_name"]) for i in out["invalid_indexes"]] == [
        ("analytics", "events_session_id_key")
    ]
    assert any("analytics.events_session_id_key" in w for w in out["warnings"])
    assert any("analytics.page_views has 60% dead tuples" in w for w in out["warnings"])
    assert out["status"] == "warning"


@pytest.mark.usefixtures("db")
async def test_an_open_transaction_is_reported(mcp, monkeypatch):
    monkeypatch.setattr(health, "LONG_RUNNING_SECONDS", 0)
    # A second session left idle inside its (read-only) transaction.
    async with database.connection() as other:
        pid = (await other.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        out = await read_json(mcp, HEALTH_URI)

    [mine] = [s for s in out["long_running"] if s["pid"] == pid]
    assert mine["state"] == "idle in transaction"
    assert mine["query"] == "SELECT pg_backend_pid()"
    assert out["status"] == "warning"
    assert any("idle in transaction" in w for w in out["warnings"])
