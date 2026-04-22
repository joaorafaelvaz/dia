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

# Fase 1 + Fase 2: Python deps (asyncpg, pandas, numpy, lxml, orjson, hiredis)
# têm wheels manylinux2014 para Python 3.11 amd64. Chromium headless (Fase 2)
# precisa das libs listadas abaixo.
#
# NÃO usamos `playwright install --with-deps` porque a tentativa do Playwright
# de rodar `apt-get install` aninhado falha intermitentemente no Debian bookworm
# (conflitos de dpkg em libxcb-sync1 / xvfb). Listamos as deps manualmente em
# um único `apt-get install` — mais previsível e reprodutível.
#
# Lista verificada contra:
#   playwright 1.44 / chromium 124 / debian 12 bookworm amd64
# Fontes: docs oficiais Playwright + saída de `ldd` no chromium binary.
#
#   curl                 → HEALTHCHECK + debugging
#   ca-certificates      → HTTPS ao Open-Meteo / news sources
#   fonts-liberation     → fallback de fontes sem-serif
#   fontconfig           → resolução de fontes no Chromium
#   libnss3 libnspr4     → networking + criptografia do Chromium
#   libatk*              → acessibilidade (headless ainda requer)
#   libcups2             → libcups: Chromium linka mesmo em headless
#   libdrm2 libgbm1      → GPU/Mesa (para --disable-gpu headless)
#   libxkbcommon0        → teclado
#   libxcomposite1       → compositor X (stub)
#   libxdamage1          → X damage extension
#   libxfixes3           → X fixes extension
#   libxrandr2           → X randr extension
#   libasound2           → áudio (stub, mas o binário linka)
#   libatspi2.0-0        → acessibilidade GTK
#   libpango-1.0-0       → renderização de texto (Chromium + WeasyPrint)
#   libpangoft2-1.0-0    → WeasyPrint (binding FreeType↔Pango, obrigatório)
#   libharfbuzz0b        → shaping de texto (WeasyPrint via Pango)
#   libcairo2            → renderização 2D (Chromium + WeasyPrint)
#   libgdk-pixbuf-2.0-0  → WeasyPrint (imagens PNG/JPEG em relatórios)
#   libxshmfence1        → sincronização GPU / drm
#   shared-mime-info     → detecção de MIME (WeasyPrint resolve imagens)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        fonts-liberation \
        fontconfig \
        libnss3 \
        libnspr4 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libasound2 \
        libatspi2.0-0 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libxshmfence1 \
        shared-mime-info \
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

# Playwright Chromium — apenas baixa o binário (libs nativas já instaladas acima).
# NUNCA usar `--with-deps` aqui: o Playwright lança apt-get aninhado que falha
# sem razão clara em Debian bookworm (ver comentário no bloco apt principal).
#
# PLAYWRIGHT_BROWSERS_PATH=/ms-playwright mantém os browsers num caminho
# previsível, útil para debug e para multi-stage builds futuros.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN uv run playwright install chromium

# Default port for API service
EXPOSE 8000

# Health check hook (overridden per service in compose)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fs http://localhost:8000/api/v1/health || exit 1

# Default command — overridden by compose per service
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
