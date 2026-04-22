# syntax=docker/dockerfile:1.7

# ==============================================================================
# DIA — Dam Intelligence Agent
# Base: Debian slim (bookworm) for Playwright + WeasyPrint compatibility
# Package manager: uv (fast, reproducible)
# ==============================================================================

FROM python:3.11-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# System dependencies:
#   - WeasyPrint: pango, cairo, gdk-pixbuf, shared-mime-info, fonts
#   - Playwright: installed via `playwright install --with-deps` at runtime
#   - Postgres client libs: libpq-dev for psycopg2-binary fallback
#   - Build essentials for any wheel that needs compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    libpq-dev \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-dejavu-core \
    fonts-liberation \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.4.20 /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy dependency manifest first for better layer caching
COPY pyproject.toml ./

# Install deps (without project) into /app/.venv
RUN uv sync --no-install-project --no-dev

# Copy project source
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts

# Sync again to install the project itself
RUN uv sync --no-dev

# Default port for API service
EXPOSE 8000

# Health check hook (overridden per service in compose)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fs http://localhost:8000/api/v1/health || exit 1

# Default command — overridden by compose per service
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
