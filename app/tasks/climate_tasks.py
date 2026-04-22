"""Celery tasks: fetch climate data, build events and forecasts, generate alerts.

Design note: Celery runs sync tasks; we bridge to async via asyncio.run(). This is
acceptable for independent ingestion tasks. The DB session is short-lived per task.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select

from app.database import SessionLocal
from app.models.dam import Dam
from app.services.climate import aggregator, open_meteo
from app.tasks.celery_app import celery_app
from app.utils.logging import get_logger

log = get_logger(__name__)


async def _fetch_for_dam(dam_id: int) -> dict[str, int]:
    """Async worker body for fetch_climate_data_for_dam."""
    async with SessionLocal() as session:
        dam = (
            await session.execute(select(Dam).where(Dam.id == dam_id))
        ).scalar_one_or_none()
        if not dam:
            log.warning("dam_not_found", dam_id=dam_id)
            return {"forecasts_written": 0, "events_written": 0, "alerts_created": 0}

        if not dam.is_active:
            log.info("dam_inactive_skip", dam_id=dam_id, name=dam.name)
            return {"forecasts_written": 0, "events_written": 0, "alerts_created": 0}

        # 1. Forecast (next 16 days)
        forecast = await open_meteo.get_forecast(
            dam.latitude, dam.longitude, days=16
        )
        forecasts_written = await aggregator.save_forecasts(session, dam, forecast)

        # 2. Historical (last 30 days) → detect extreme events
        end = date.today()
        start = end - timedelta(days=30)
        historical = await open_meteo.get_historical(
            dam.latitude, dam.longitude, start, end
        )
        events = aggregator.detect_extreme_events(historical.days, dam)
        events_written = await aggregator.save_climate_events(session, dam, events)

        # 3. Generate alerts from forecast horizon
        alerts = await aggregator.check_and_create_alerts(session, dam)

        await session.commit()

        log.info(
            "climate_fetch_complete",
            dam_id=dam.id,
            dam_name=dam.name,
            forecasts_written=forecasts_written,
            events_written=events_written,
            alerts_created=len(alerts),
        )
        return {
            "forecasts_written": forecasts_written,
            "events_written": events_written,
            "alerts_created": len(alerts),
        }


@celery_app.task(
    name="app.tasks.climate_tasks.fetch_climate_data_for_dam",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def fetch_climate_data_for_dam(self, dam_id: int) -> dict[str, int]:
    """Ingest Open-Meteo forecast + history for a single dam and run alert checks."""
    try:
        return asyncio.run(_fetch_for_dam(dam_id))
    except SoftTimeLimitExceeded:
        log.warning("fetch_climate_soft_timeout", dam_id=dam_id)
        raise
    except Exception as exc:
        log.error("fetch_climate_failed", dam_id=dam_id, error=str(exc))
        raise self.retry(exc=exc) from exc


async def _all_active_dam_ids() -> list[int]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Dam.id).where(Dam.is_active.is_(True)).order_by(Dam.id)
        )
        return list(result.scalars().all())


@celery_app.task(name="app.tasks.climate_tasks.fetch_all_climate_data")
def fetch_all_climate_data() -> dict[str, int]:
    """Fan-out: dispatch fetch_climate_data_for_dam for each active dam."""
    dam_ids = asyncio.run(_all_active_dam_ids())
    log.info("fetch_all_climate_data_start", dam_count=len(dam_ids))
    for dam_id in dam_ids:
        fetch_climate_data_for_dam.delay(dam_id)
    return {"dispatched": len(dam_ids)}


async def _check_alerts_for_dam(dam_id: int) -> int:
    async with SessionLocal() as session:
        dam = (
            await session.execute(select(Dam).where(Dam.id == dam_id))
        ).scalar_one_or_none()
        if not dam or not dam.is_active:
            return 0
        alerts = await aggregator.check_and_create_alerts(session, dam)
        await session.commit()
        return len(alerts)


@celery_app.task(name="app.tasks.climate_tasks.check_all_alerts")
def check_all_alerts() -> dict[str, int]:
    """Hourly sweep: re-evaluate forecast thresholds and create/update alerts."""
    dam_ids = asyncio.run(_all_active_dam_ids())
    total = 0
    for dam_id in dam_ids:
        try:
            total += asyncio.run(_check_alerts_for_dam(dam_id))
        except Exception as exc:
            log.error("check_alerts_failed", dam_id=dam_id, error=str(exc))
    return {"dams_checked": len(dam_ids), "alerts_created_or_updated": total}
