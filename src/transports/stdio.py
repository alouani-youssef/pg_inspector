import logging
import sys


def _keep_stdout_clean() -> None:
    """stdout carries JSON-RPC frames, so every log line must go to stderr.

    Python logging already defaults to stderr, but SQLAlchemy's `echo=True` attaches a
    stdout handler to its engine logger unless that logger already has one. Pre-empt it.
    """
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    engine_logger = logging.getLogger("sqlalchemy.engine.Engine")
    if not engine_logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        engine_logger.addHandler(handler)


def run_stdio() -> None:
    """Serve MCP over stdin/stdout for a client that launches this process.

    The client owns the process, so there is no network listener and no API key; the
    HTTP-only routes (/health, /ready, /metrics, OAuth metadata) are not served.
    """
    _keep_stdout_clean()
    # Imported after logging is set up so nothing configured at import time reaches stdout.
    from src.server import mcp

    mcp.run(transport="stdio")
