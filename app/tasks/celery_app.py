"""Celery application with Redbeat scheduler and cron-based beat schedule."""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import settings
from app.utils.logging import configure_logging

configure_logging()


def _parse_cron(expr: str) -> crontab:
    """Parse '0 */3 * * *' → crontab(minute=0, hour='*/3', ...)."""
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: {expr!r}")
    minute, hour, day_of_month, month_of_year, day_of_week = parts
    return crontab(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month_of_year=month_of_year,
        day_of_week=day_of_week,
    )


celery_app = Celery(
    "dia",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="America/Sao_Paulo",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=60 * 10,
    task_soft_time_limit=60 * 9,
    worker_max_tasks_per_child=100,
    worker_prefetch_multiplier=1,
    redbeat_redis_url=settings.celery_broker_url,
    redbeat_lock_timeout=90,
    beat_schedule={
        "fetch-climate-data": {
            "task": "app.tasks.climate_tasks.fetch_all_climate_data",
            "schedule": _parse_cron(settings.schedule_climate_fetch),
        },
        "check-alerts": {
            "task": "app.tasks.climate_tasks.check_all_alerts",
            "schedule": _parse_cron(settings.schedule_alert_check),
        },
        "expire-stale-alerts": {
            "task": "app.tasks.climate_tasks.expire_stale_alerts",
            "schedule": _parse_cron(settings.schedule_alert_expiration),
        },
        "scrape-news": {
            "task": "app.tasks.news_tasks.scrape_all_news",
            "schedule": _parse_cron(settings.schedule_news_scrape),
        },
        "generate-weekly-briefing": {
            "task": "app.tasks.report_tasks.generate_weekly_briefing",
            "schedule": _parse_cron(settings.schedule_report_briefing),
        },
        "generate-monthly-client-reports": {
            "task": "app.tasks.report_tasks.generate_monthly_client_reports",
            "schedule": _parse_cron(settings.schedule_report_client),
        },
    },
)

# Autodiscover tasks in these modules
celery_app.autodiscover_tasks(["app.tasks"])

# Explicit imports so worker registers tasks at startup
import app.tasks.climate_tasks  # noqa: E402, F401
import app.tasks.news_tasks  # noqa: E402, F401
import app.tasks.report_tasks  # noqa: E402, F401

__all__ = ["celery_app"]
