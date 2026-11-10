"""`uv run test`: run the test suite against the seeded PostgreSQL.

Integration tests need the database from docker compose (data/db_init.sql). By default this
starts it (if needed), waits until it is healthy, then runs pytest. Unrecognised arguments
are passed straight to pytest, e.g. `uv run test -k describe tests/tools/test_catalog.py`.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _step(msg: str) -> None:
    print(f"\n\033[1;34m==> {msg}\033[0m", flush=True)


def _run(*cmd: str) -> None:
    """Run a command from the project root; exit with its code if it fails."""
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        sys.exit(result.returncode)


def _parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        prog="test",
        description="Run the pg_inspector test suite. Extra arguments go to pytest.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="also run ruff (lint + format check) and mypy first",
    )
    parser.add_argument(
        "--reseed",
        action="store_true",
        help="recreate the db from data/db_init.sql first (runs `docker compose down -v`, DELETES its data)",
    )
    parser.add_argument(
        "--unit",
        action="store_true",
        help="don't start PostgreSQL (db tests are skipped)",
    )
    parser.add_argument(
        "--down", action="store_true", help="stop the db container after the run"
    )
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> None:
    args, pytest_args = _parse(sys.argv[1:] if argv is None else argv)
    os.chdir(ROOT)  # pytest config, tests/ and .env are resolved from the project root

    try:
        import pytest
    except ImportError:
        sys.exit("pytest is not installed; run `uv sync` (it installs the dev group).")

    if not (ROOT / ".env").exists():
        sys.exit(".env is missing; copy .env.example to .env and fill it in.")

    if args.check:
        _step("ruff check")
        _run("ruff", "check", ".")
        _step("ruff format --check")
        _run("ruff", "format", "--check", ".")
        _step("mypy")
        _run("mypy", "src")

    use_db = not args.unit
    if use_db:
        if shutil.which("docker") is None:
            sys.exit("docker is required for integration tests (or pass --unit).")
        if args.reseed:
            _step("Re-seeding PostgreSQL (dropping volume)")
            _run("docker", "compose", "down", "-v")
        _step("Starting PostgreSQL")
        _run("docker", "compose", "up", "-d", "--wait", "db")

    _step("pytest")
    try:
        code = pytest.main(pytest_args)
    finally:
        if use_db and args.down:
            _step("Stopping PostgreSQL")
            subprocess.run(["docker", "compose", "stop", "db"], check=False)
    sys.exit(int(code))


if __name__ == "__main__":
    main()
