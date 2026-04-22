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

# Fase 1: Python deps (asyncpg, pandas, numpy, lxml, orjson, hiredis) têm wheels
# manylinux2014 para Python 3.11 amd64.
#
# Fase 2: Playwright/Chromium headless precisa de libs nativas (nss, atk, libx11,
# libxcomposite, libxdamage, libxrandr, libgbm, libasound2, libpango, libcairo).
# Em vez de listar tudo na mão, instalamos chromium via `playwright install
# --with-deps` mais abaixo, depois que o pacote Python playwright estiver
# instalado no venv.
#
#   - curl: HEALTHCHECK + debugging
#   - ca-certificates: HTTPS ao Open-Meteo / news sources
#   - fonts-liberation + fontconfig: evita squares em páginas renderizadas
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        fonts-liberation \
        fontconfig \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.4.20 /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy dependency manifest first for better layer caching
COPY pyproject.toml ./

# Install deps (without project) into /app/.venv
RUN uv sync --no-install-project --no-dev

# Copy project source
COPY README.md ./README.md
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts

# Sync again to install the project itself
RUN uv sync --no-dev

# Playwright + Chromium (Fase 2 — news scraping)
# `playwright install --with-deps chromium` baixa o browser e instala as libs
# nativas via apt. Fazemos isso APÓS o `uv sync` para que o pacote Python
# `playwright` já esteja no venv. Cleanup das listas apt depois.
#
# PLAYWRIGHT_BROWSERS_PATH=/ms-playwright mantém os browsers em caminho
# previsível (necessário em multi-stage futuro).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN uv run playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Default port for API service
EXPOSE 8000

# Health check hook (overridden per service in compose)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fs http://localhost:8000/api/v1/health || exit 1

# Default command — overridden by compose per service
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
