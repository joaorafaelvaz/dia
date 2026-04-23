"""Celery tasks: fetch climate data, build events and forecasts, generate alerts.

Design note: Celery runs sync tasks; we bridge to async via asyncio.run(). This is
acceptable for independent ingestion tasks. The DB session is short-lived per task.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import and_, or_, select, update

from app.config import settings
from app.database import task_session
from app.models.alert import Alert
from app.models.dam import Dam
from app.services.climate import aggregator, inmet, open_meteo
from app.services.climate.inmet import InmetError
from app.tasks.celery_app import celery_app
from app.utils.logging import get_logger

log = get_logger(__name__)


async def _fetch_for_dam(dam_id: int) -> dict[str, int]:
    """Async worker body for fetch_climate_data_for_dam."""
    async with task_session() as session:
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

        # 2b. INMET (opcional, atrás de feature flag): estação real mais
        # próxima. Dedup acontece em save_climate_events via (dam_id,
        # event_type, event_date ± 2d) — eventos que batem com Open-Meteo
        # são mesclados no raw_data, não duplicados. Qualquer falha do INMET
        # (timeout, 5xx, estação sem dados) é absorvida aqui: log + segue
        # somente com Open-Meteo.
        if settings.inmet_enabled:
            try:
                station, distance_km, inmet_days = await inmet.get_historical_for_coords(
                    dam.latitude,
                    dam.longitude,
                    lookback_days=settings.inmet_lookback_days,
                    state_filter=dam.state,
                )
                inmet_events = aggregator.detect_extreme_events(
                    inmet_days, dam, source_key="inmet", source_label="inmet_api"
                )
                events.extend(inmet_events)
                log.info(
                    "inmet_merged",
                    dam_id=dam.id,
                    station=station.code,
                    distance_km=round(distance_km, 2),
                    inmet_days=len(inmet_days),
                    inmet_events=len(inmet_events),
                )
            except InmetError as exc:
                log.warning("inmet_skipped", dam_id=dam.id, error=str(exc))
            except Exception as exc:  # defensivo: INMET nunca derruba a task
                log.warning("inmet_unexpected_error", dam_id=dam.id, error=str(exc))

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
    async with task_session() as session:
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
    async with task_session() as session:
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


# ---------------------------------------------------------------------------
# Alert expiration
# ---------------------------------------------------------------------------

# TTL para alertas já reconhecidos ("acknowledged"). Mesmo reconhecido, mantém
# na UI por N dias pra referência/auditoria; depois some da query de ativos.
_ACK_TTL_DAYS = 7

# TTL padrão para forecast_warning sem expires_at explícito — defesa em
# profundidade. O aggregator HOJE sempre popula expires_at = forecast_date + 1d,
# mas se alguma migração ou código futuro esquecer, esse fallback pega.
_FORECAST_WARNING_STALE_DAYS = 2


async def _expire_alerts_async() -> dict[str, int]:
    """Varre alertas ativos e desativa os que cairam em regras de expiração.

    Regras (ordem de avaliação; qualquer uma basta pra desativar):
      1. `expires_at IS NOT NULL AND expires_at < now()` — TTL explícito vencido
      2. `acknowledged = true AND acknowledged_at < now() - 7d` — reconhecido há mais de uma semana
      3. `alert_type = 'forecast_warning' AND forecast_date < today - 2d AND expires_at IS NULL`
         — forecast alert antigo sem expires_at (defesa em profundidade)

    Idempotente: já atua somente em `is_active=true`. Roda de hora em hora.
    """
    now = datetime.now(tz=timezone.utc)
    ack_cutoff = now - timedelta(days=_ACK_TTL_DAYS)
    forecast_cutoff = date.today() - timedelta(days=_FORECAST_WARNING_STALE_DAYS)

    async with task_session() as session:
        # Precisa contar primeiro pra log estruturado — segundo query atualiza.
        # Duas passadas custam barato; alerts ativos raramente passam de 100.
        rule_conditions = [
            and_(Alert.expires_at.isnot(None), Alert.expires_at < now),
            and_(Alert.acknowledged.is_(True), Alert.acknowledged_at < ack_cutoff),
            and_(
                Alert.alert_type == "forecast_warning",
                Alert.expires_at.is_(None),
                Alert.forecast_date < forecast_cutoff,
            ),
        ]

        target_stmt = select(Alert).where(
            Alert.is_active.is_(True),
            or_(*rule_conditions),
        )
        targets = list((await session.execute(target_stmt)).scalars().all())
        if not targets:
            log.info("alerts_expire_sweep_clean")
            return {"expired": 0}

        update_stmt = (
            update(Alert)
            .where(
                Alert.is_active.is_(True),
                Alert.id.in_([a.id for a in targets]),
            )
            .values(is_active=False)
        )
        await session.execute(update_stmt)
        await session.commit()

        # Breakdown por tipo pra diagnóstico
        by_type: dict[str, int] = {}
        for a in targets:
            by_type[a.alert_type] = by_type.get(a.alert_type, 0) + 1

        log.info(
            "alerts_expired",
            total=len(targets),
            by_type=by_type,
            ack_ttl_days=_ACK_TTL_DAYS,
        )
        return {"expired": len(targets)}


@celery_app.task(name="app.tasks.climate_tasks.expire_stale_alerts")
def expire_stale_alerts() -> dict[str, int]:
    """Desativa alertas vencidos (expires_at passado, acknowledged antigo, forecast no passado)."""
    return asyncio.run(_expire_alerts_async())
