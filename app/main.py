"""FastAPI application factory — routers, lifespan, static, templates."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router
from app.config import settings
from app.database import engine
from app.utils.logging import configure_logging, get_logger
from app.web.router import web_router

configure_logging()
log = get_logger(__name__)


def _maybe_init_sentry() -> None:
    """Inicializa Sentry SDK se SENTRY_DSN estiver setado.

    No-op silencioso quando não configurado — dev/test não precisa instalar
    sentry-sdk. Em prod o operador seta SENTRY_DSN no .env e tudo passa
    a fluir pro projeto Sentry sem mais mudanças no código.
    """
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        log.warning(
            "sentry_dsn_set_but_sdk_missing",
            hint="adicione 'sentry-sdk[fastapi]' ao pyproject.toml",
        )
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
        # Não envia request body por default — pode ter Bearer tokens em
        # headers de webhooks de teste. Default sem PII é o seguro.
        send_default_pii=False,
    )
    log.info("sentry_initialized", env=settings.app_env)


_maybe_init_sentry()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("app_startup", env=settings.app_env, debug=settings.app_debug)
    yield
    log.info("app_shutdown")
    await engine.dispose()


app = FastAPI(
    title="DIA — Dam Intelligence Agent",
    version="0.1.0",
    description="Monitoramento autônomo de barragens — Fractal Engenharia.",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Captura qualquer exceção não tratada pelos endpoints.

    Loga estruturado (structlog → stdout → Docker logs) com path/método/user
    pra facilitar diagnóstico. Sentry, se configurado, já recebeu via
    integração FastAPI antes desse handler. Resposta 500 genérica pra não
    vazar stacktrace pro cliente — detalhe fica no log.

    Não captura HTTPException (4xx/redirects) — essas são esperadas e
    FastAPI já tem handler dedicado.
    """
    from fastapi import HTTPException
    if isinstance(exc, HTTPException):
        raise exc  # deixa o handler default lidar
    log.exception(
        "unhandled_endpoint_error",
        path=request.url.path,
        method=request.method,
        client_host=request.client.host if request.client else None,
        error_type=type(exc).__name__,
        error=str(exc)[:500],
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error_type": type(exc).__name__},
    )


app.include_router(api_router)
app.include_router(web_router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
