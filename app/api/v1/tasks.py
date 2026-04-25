"""Manual task triggers — operator escape hatch.

Allowlist explícita em `_TASKS`: operador pode acionar manualmente apenas
tasks listadas aqui. Qualquer task Celery registrada que NÃO esteja nessa
lista (ex: scheduled_report_row, internal helpers) não é alcançável via API.

Pra adicionar uma task: importe o módulo e adicione a entry. Se for tarefa
parametrizada (recebe dam_id, etc.), crie endpoint dedicado abaixo.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.dependencies import AuthUser
from app.tasks import climate_tasks, news_tasks, notification_tasks

router = APIRouter(prefix="/tasks", tags=["tasks"])

# Allowlist de tasks "no-arg" que o operador pode disparar pelo dashboard
# ou pelo Makefile. Não inclua tasks que recebem parâmetros — endpoints
# dedicados abaixo cobrem esses (fetch_climate_data_for_dam, etc.).
_TASKS = {
    "fetch_all_climate_data": climate_tasks.fetch_all_climate_data,
    "check_all_alerts": climate_tasks.check_all_alerts,
    "expire_stale_alerts": climate_tasks.expire_stale_alerts,
    "scrape_all_news": news_tasks.scrape_all_news,
    "dispatch_pending_notifications": notification_tasks.dispatch_pending_notifications,
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
