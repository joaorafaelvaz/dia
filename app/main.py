"""FastAPI application factory — routers, lifespan, static, templates."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router
from app.config import settings
from app.database import engine
from app.utils.logging import configure_logging, get_logger
from app.web.router import web_router

configure_logging()
log = get_logger(__name__)

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

app.include_router(api_router)
app.include_router(web_router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
