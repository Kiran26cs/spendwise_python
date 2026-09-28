# syntax=docker/dockerfile:1
# SpendWise API image.
#   Stage 1 (builder): uv installs the locked dependencies, then the project, into /app/.venv.
#   Stage 2 (runtime): slim Python + the virtualenv and migrations only; runs as non-root.
# On start the container applies database migrations, then serves the API on port 8000.

ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first (changes rarely -> cached layer), using only the lockfile and metadata.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

# Then the project itself, installed non-editable so the venv is self-contained.
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    ENVIRONMENT=prod \
    LOG_JSON=true

RUN groupadd --system --gid 10001 spendwise \
 && useradd --system --gid spendwise --uid 10001 --no-create-home spendwise

WORKDIR /app
COPY --from=builder --chown=spendwise:spendwise /app/.venv ./.venv
COPY --chown=spendwise:spendwise alembic.ini ./
COPY --chown=spendwise:spendwise alembic ./alembic
COPY --chown=spendwise:spendwise scripts ./scripts
COPY --chown=spendwise:spendwise sample_data ./sample_data

USER spendwise
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

# Migrate, then replace the shell with uvicorn so it receives signals (clean shutdown).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn spendwise.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
