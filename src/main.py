import argparse

from src.config import Transport, settings
from src.transports import run_http, run_stdio

RUNNERS = {"streamable-http": run_http, "stdio": run_stdio}


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="start", description="Run the PostgreSQL Inspector MCP server."
    )
    parser.add_argument(
        "--transport",
        choices=RUNNERS,
        default=settings.TRANSPORT,
        help="MCP transport (default: TRANSPORT from the environment, else streamable-http).",
    )
    transport: Transport = parser.parse_args().transport
    RUNNERS[transport]()


if __name__ == "__main__":
    main()
