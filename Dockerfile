# The Python version comes from .python-version: `uv python install` reads it.
FROM debian:bookworm-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install the pinned interpreter and dependencies first for layer caching.
COPY .python-version pyproject.toml uv.lock ./
RUN uv python install && uv sync --locked --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev

CMD ["start"]
