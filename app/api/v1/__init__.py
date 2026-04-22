"""API v1 aggregate router."""
from fastapi import APIRouter

from app.api.v1 import alerts, dams, events, forecasts, tasks

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(dams.router)
api_router.include_router(events.router)
api_router.include_router(forecasts.router)
api_router.include_router(alerts.router)
api_router.include_router(tasks.router)


@api_router.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
