"""`uv run mock-activity`: its sessions show up in the health resource, and leave no trace."""

import pytest

from src.mock_activity import simulate_activity
from src.resources import health
from src.resources.health import HEALTH_URI
from tests.mocks.conftest import scalar
from tests.resources.conftest import read_json

pytestmark = pytest.mark.usefixtures("db")


async def test_simulated_sessions_are_reported(mcp, monkeypatch):
    monkeypatch.setattr(health, "LONG_RUNNING_SECONDS", 0)
    async with simulate_activity() as a:
        out = await read_json(mcp, HEALTH_URI)

    [blocked] = [b for b in out["blocked"] if b["pid"] == a.waiter_pid]
    assert blocked["blocked_by"] == [a.holder_pid]
    assert "UPDATE crm.accounts" in blocked["query"]

    states = {s["pid"]: s["state"] for s in out["long_running"]}
    assert states[a.holder_pid] == "idle in transaction"
    assert states[a.sleeper_pid] == "active"
    assert any("waiting on locks" in w for w in out["warnings"])


async def test_nothing_is_left_behind(mcp):
    async with simulate_activity() as a:
        pass
    out = await read_json(mcp, HEALTH_URI)
    assert out["blocked"] == []
    pids = [a.holder_pid, a.waiter_pid, a.sleeper_pid]
    assert (
        await scalar(
            f"SELECT count(*) FROM pg_stat_activity WHERE pid IN ({', '.join(map(str, pids))})"
        )
        == 0
    )
    assert await scalar("SELECT name FROM crm.accounts WHERE id = 1") == "Account 1"
