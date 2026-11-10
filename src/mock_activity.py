"""`uv run mock-activity`: simulate live sessions so the health resource has something to report.

Seed data cannot plant runtime state, so this opens three sessions on the configured
database (it needs the crm mock from data/mocks/30_crm_quality.sql):

* holder:  locks the row crm.accounts id=1, then sits "idle in transaction";
* waiter:  UPDATEs that same row and is blocked by the holder;
* sleeper: runs a long query (`pg_sleep`).

They show up in `pg-inspector://database/health` under `blocked` right away, and under
`long_running` (with warnings) once they pass its 300s threshold. Every transaction is
rolled back, so nothing is changed. The sessions end after `--seconds` or on Ctrl+C.

It connects with DATABASE_USERNAME from .env, which needs UPDATE on crm.accounts (the
docker compose superuser does), not with the read-only role pg-inspector itself should use.
"""

import argparse
import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg

from src.config import Settings, settings

APPLICATION_NAME = "pg-inspector-mock"
LOCKED_ROW = "SELECT id FROM crm.accounts WHERE id = 1 FOR UPDATE"
BLOCKED_UPDATE = "BEGIN; UPDATE crm.accounts SET name = name WHERE id = 1"
LONG_QUERY = "SELECT pg_sleep(86400) /* pg-inspector-mock: long-running report */"


@dataclass(frozen=True, slots=True)
class Activity:
    """Backend PIDs of the simulated sessions."""

    holder_pid: int
    waiter_pid: int
    sleeper_pid: int


async def _connect(s: Settings) -> asyncpg.Connection:
    return await asyncpg.connect(
        host=s.DATABASE_HOST,
        port=s.DATABASE_PORT,
        user=s.DATABASE_USERNAME,
        password=s.DATABASE_PASSWORD.get_secret_value() or None,
        database=s.DATABASE_DB,
        timeout=s.DATABASE_CONNECT_TIMEOUT,
        server_settings={"application_name": APPLICATION_NAME},
    )


async def _wait_for(
    monitor: asyncpg.Connection, condition: str, pid: int, what: str
) -> None:
    """Poll (outside any transaction, so pg_stat_activity is fresh) until `condition` holds."""
    for _ in range(100):
        if await monitor.fetchval(f"SELECT {condition}", pid):
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"Session {pid} did not {what} within 5s.")


@contextlib.asynccontextmanager
async def simulate_activity(s: Settings = settings) -> AsyncIterator[Activity]:
    """Open the holder, waiter and sleeper sessions; end them (rolling back) on exit."""
    monitor, holder, waiter, sleeper = await asyncio.gather(
        *(_connect(s) for _ in range(4))
    )
    tasks: list[asyncio.Task[object]] = []
    try:
        holder_pid, waiter_pid, sleeper_pid = (
            c.get_server_pid() for c in (holder, waiter, sleeper)
        )
        await holder.execute("BEGIN")
        if await holder.fetchval(LOCKED_ROW) is None:
            raise LookupError(
                "crm.accounts id=1 not found: load the mocks in data/mocks first."
            )

        tasks.append(asyncio.create_task(waiter.execute(BLOCKED_UPDATE)))
        await _wait_for(
            monitor, "cardinality(pg_blocking_pids($1)) > 0", waiter_pid, "block"
        )
        tasks.append(asyncio.create_task(sleeper.execute(LONG_QUERY)))
        await _wait_for(
            monitor,
            "EXISTS (SELECT 1 FROM pg_stat_activity WHERE pid = $1 AND state = 'active')",
            sleeper_pid,
            "start its query",
        )

        yield Activity(holder_pid, waiter_pid, sleeper_pid)
    finally:
        # Ending a backend rolls back its transaction. The waiter and sleeper go first so the
        # waiter never gets to run its UPDATE; the timeout makes each call wait for the exit.
        pids = [c.get_server_pid() for c in (sleeper, waiter, holder)]
        with contextlib.suppress(asyncpg.PostgresError, OSError):
            for pid in pids:
                await monitor.execute("SELECT pg_terminate_backend($1, 5000)", pid)
        for conn in (sleeper, waiter, holder):
            conn.terminate()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await monitor.close()


# --------------------------------------------------------------------------- CLI


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mock-activity",
        description="Hold blocked, idle-in-transaction and long-running sessions open so the "
        "health resource reports them. Nothing is changed in the database.",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=360,
        help="how long to keep the sessions open (default 360, past the health "
        "resource's 300s long-running threshold)",
    )
    return parser.parse_args(argv)


async def _run(seconds: float) -> None:
    async with simulate_activity() as a:
        print(
            f"holder  pid {a.holder_pid}: idle in transaction, holds crm.accounts id=1\n"
            f"waiter  pid {a.waiter_pid}: blocked by {a.holder_pid}\n"
            f"sleeper pid {a.sleeper_pid}: long-running query\n"
            f"Read pg-inspector://database/health now; long_running fills in after 300s.\n"
            f"Holding for {seconds:.0f}s (Ctrl+C to stop).",
            flush=True,
        )
        await asyncio.sleep(seconds)
    print("Sessions closed; every transaction was rolled back.")


def main(argv: list[str] | None = None) -> None:
    args = _parse(argv)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run(args.seconds))


if __name__ == "__main__":
    main()
