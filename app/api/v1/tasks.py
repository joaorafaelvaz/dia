"""Manual task triggers — operator escape hatch."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.dependencies import AuthUser
from app.tasks import climate_tasks, news_tasks

router = APIRouter(prefix="/tasks", tags=["tasks"])

_TASKS = {
    "fetch_all_climate_data": climate_tasks.fetch_all_climate_data,
    "check_all_alerts": climate_tasks.check_all_alerts,
    "scrape_all_news": news_tasks.scrape_all_news,
}


@router.post("/run/{task_name}")
async def run_task(task_name: str, _: AuthUser) -> dict[str, str]:
    task = _TASKS.get(task_name)
    if not task:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown task '{task_name}'. Available: {sorted(_TASKS)}",
        )
    async_result = task.delay()
    return {"task_id": async_result.id, "task": task_name, "status": "queued"}


@router.post("/run/fetch_climate_data_for_dam/{dam_id}")
async def run_fetch_for_dam(dam_id: int, _: AuthUser) -> dict[str, str]:
    async_result = climate_tasks.fetch_climate_data_for_dam.delay(dam_id)
    return {
        "task_id": async_result.id,
        "task": "fetch_climate_data_for_dam",
        "dam_id": str(dam_id),
        "status": "queued",
    }


@router.post("/run/scrape_news_for_dam/{dam_id}")
async def run_scrape_news_for_dam(dam_id: int, _: AuthUser) -> dict[str, str]:
    async_result = news_tasks.scrape_news_for_dam.delay(dam_id)
    return {
        "task_id": async_result.id,
        "task": "scrape_news_for_dam",
        "dam_id": str(dam_id),
        "status": "queued",
    }
