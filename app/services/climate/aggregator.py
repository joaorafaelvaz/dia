"""Aggregator: turn Open-Meteo daily payloads into persistent Forecast and ClimateEvent rows.

Also exposes:
  - detect_extreme_events: identify days in a historical window that count as "extreme"
  - compute_risk_score: map forecast + dam profile to severity 1–5
  - save_forecasts: upsert forecasts for a dam
  - save_climate_events: dedup-aware save of detected events
  - check_and_create_alerts: scan today's forecasts and create alerts where needed
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.alert import Alert
from app.models.dam import Dam
from app.models.event import ClimateEvent
from app.models.forecast import Forecast
from app.services.climate.open_meteo import DailyForecast, ForecastResponse, describe_weather
from app.utils.logging import get_logger
from app.utils.severity import label_for, severity_from_precipitation

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------


def compute_risk_score(precipitation_mm: float, dam: Dam) -> tuple[int, str, bool]:
    """Return (risk_level, risk_label, threshold_exceeded) for a forecast precip value."""
    level = severity_from_precipitation(
        precipitation_mm, dam_type=dam.dam_type, dpa=dam.dpa
    )
    exceeded = level >= 3
    return level, label_for(level), exceeded


# ---------------------------------------------------------------------------
# Extreme event detection from historical data
# ---------------------------------------------------------------------------


def detect_extreme_events(
    days: list[DailyForecast],
    dam: Dam,
    *,
    source_key: str = "open_meteo",
    source_label: str = "open_meteo_archive",
) -> list[dict[str, Any]]:
    """Return events for days whose precipitation crosses the moderate threshold.

    Adjusted for dam profile (tailings dams / high DPA get lower thresholds).

    `source_key` é a chave usada dentro de `raw_data` (merge por fonte em
    save_climate_events); `source_label` vira o campo `source` do ClimateEvent.
    Defaults apontam pra Open-Meteo, mas a mesma função é reusada pra ANA
    passando `source_key="ana"` / `source_label="ana_hidroweb"`.
    """
    events: list[dict[str, Any]] = []
    for day in days:
        severity = severity_from_precipitation(
            day.precipitation_mm, dam_type=dam.dam_type, dpa=dam.dpa
        )
        if severity < 2:
            continue
        events.append(
            {
                "event_date": day.date,
                "event_type": "heavy_rain",
                "severity": severity,
                "severity_label": label_for(severity),
                "title": f"Precipitação elevada em {dam.municipality}/{dam.state}",
                "description": (
                    f"{day.precipitation_mm:.1f} mm registrados em {day.date.isoformat()} "
                    f"em {dam.municipality}/{dam.state}. "
                    f"Código de tempo: {describe_weather(day.weather_code)}."
                ),
                "source_type": "weather",
                "source": source_label,
                "precipitation_mm": day.precipitation_mm,
                "raw_data": {
                    source_key: {
                        "precipitation_sum": day.precipitation_mm,
                        "weather_code": day.weather_code,
                        "max_temperature_c": day.max_temperature_c,
                        "min_temperature_c": day.min_temperature_c,
                        "wind_speed_max_kmh": day.wind_speed_max_kmh,
                    }
                },
            }
        )
    return events


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def save_forecasts(
    session: AsyncSession, dam: Dam, forecast: ForecastResponse
) -> int:
    """Upsert forecasts for a dam. Returns number of rows written.

    Uniqueness: (dam_id, forecast_date, source). Existing rows are updated.
    """
    written = 0
    for day in forecast.days:
        level, label, exceeded = compute_risk_score(day.precipitation_mm, dam)

        stmt = select(Forecast).where(
            and_(
                Forecast.dam_id == dam.id,
                Forecast.forecast_date == day.date,
                Forecast.source == "open_meteo",
            )
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()

        raw = {
            "open_meteo": {
                "precipitation_sum": day.precipitation_mm,
                "precipitation_probability_max": day.precipitation_probability_max,
                "max_temperature_c": day.max_temperature_c,
                "min_temperature_c": day.min_temperature_c,
                "wind_speed_max_kmh": day.wind_speed_max_kmh,
                "weather_code": day.weather_code,
            }
        }

        if existing:
            existing.max_precipitation_mm = day.precipitation_mm
            existing.total_precipitation_mm = day.precipitation_mm
            existing.max_temperature_c = day.max_temperature_c
            existing.min_temperature_c = day.min_temperature_c
            existing.wind_speed_kmh = day.wind_speed_max_kmh
            existing.weather_code = day.weather_code
            existing.weather_description = describe_weather(day.weather_code)
            existing.risk_level = level
            existing.risk_label = label
            existing.alert_threshold_exceeded = exceeded
            existing.raw_data = raw
            existing.generated_at = datetime.utcnow()
        else:
            session.add(
                Forecast(
                    dam_id=dam.id,
                    forecast_date=day.date,
                    source="open_meteo",
                    max_precipitation_mm=day.precipitation_mm,
                    total_precipitation_mm=day.precipitation_mm,
                    max_temperature_c=day.max_temperature_c,
                    min_temperature_c=day.min_temperature_c,
                    wind_speed_kmh=day.wind_speed_max_kmh,
                    weather_code=day.weather_code,
                    weather_description=describe_weather(day.weather_code),
                    risk_level=level,
                    risk_label=label,
                    alert_threshold_exceeded=exceeded,
                    raw_data=raw,
                )
            )
        written += 1

    await session.flush()
    return written


async def save_climate_events(
    session: AsyncSession, dam: Dam, events: list[dict[str, Any]]
) -> int:
    """Dedup-aware save of detected climate events.

    Dedup rule: (dam_id, event_type, event_date ± 2 days) → update existing row
    instead of creating a duplicate. Preserves cross-source references in raw_data.
    """
    written = 0
    for payload in events:
        target_date: date = payload["event_date"]
        stmt = select(ClimateEvent).where(
            and_(
                ClimateEvent.dam_id == dam.id,
                ClimateEvent.event_type == payload["event_type"],
                ClimateEvent.event_date >= target_date - timedelta(days=2),
                ClimateEvent.event_date <= target_date + timedelta(days=2),
            )
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()

        if existing:
            if payload["severity"] > existing.severity:
                existing.severity = payload["severity"]
                existing.severity_label = payload["severity_label"]
                existing.title = payload["title"]
                existing.description = payload["description"]
            if payload.get("precipitation_mm") is not None:
                existing.precipitation_mm = payload["precipitation_mm"]
            merged = dict(existing.raw_data or {})
            for k, v in (payload.get("raw_data") or {}).items():
                merged[k] = v
            existing.raw_data = merged
            continue

        session.add(
            ClimateEvent(
                dam_id=dam.id,
                event_type=payload["event_type"],
                severity=payload["severity"],
                severity_label=payload["severity_label"],
                title=payload["title"],
                description=payload["description"],
                source_type=payload.get("source_type", "weather"),
                source=payload["source"],
                event_date=target_date,
                precipitation_mm=payload.get("precipitation_mm"),
                ai_analysis=payload.get("ai_analysis"),
                raw_data=payload.get("raw_data", {}),
            )
        )
        written += 1

    await session.flush()
    return written


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


async def check_and_create_alerts(session: AsyncSession, dam: Dam) -> list[Alert]:
    """Scan forecast window for this dam and create alerts where thresholds exceeded."""
    today = date.today()
    horizon = today + timedelta(days=settings.alert_forecast_days)

    stmt = select(Forecast).where(
        and_(
            Forecast.dam_id == dam.id,
            Forecast.forecast_date >= today,
            Forecast.forecast_date <= horizon,
            Forecast.risk_level >= 3,
        )
    )
    forecasts = list((await session.execute(stmt)).scalars().all())

    created: list[Alert] = []
    for fc in forecasts:
        # Skip if an active alert already exists for same dam + same forecast date
        existing_stmt = select(Alert).where(
            and_(
                Alert.dam_id == dam.id,
                Alert.alert_type == "forecast_warning",
                Alert.forecast_date == fc.forecast_date,
                Alert.is_active.is_(True),
            )
        )
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            if fc.risk_level > existing.severity:
                existing.severity = fc.risk_level
                existing.message = (
                    f"Risco elevado de {fc.max_precipitation_mm:.1f} mm previsto para "
                    f"{fc.forecast_date.isoformat()} em {dam.name} "
                    f"({dam.municipality}/{dam.state})."
                )
            continue

        alert = Alert(
            dam_id=dam.id,
            alert_type="forecast_warning",
            severity=fc.risk_level,
            title=f"Alerta {fc.risk_label} — {dam.name}",
            message=(
                f"Precipitação prevista de {fc.max_precipitation_mm:.1f} mm em "
                f"{fc.forecast_date.isoformat()} para {dam.name} "
                f"({dam.municipality}/{dam.state}). {fc.weather_description or ''}"
            ),
            forecast_date=fc.forecast_date,
            is_active=True,
            expires_at=datetime.combine(fc.forecast_date + timedelta(days=1), datetime.min.time()),
            # Propaga: forecast sintético (test harness) gera alert sintético.
            is_test=fc.is_test,
        )
        session.add(alert)
        created.append(alert)
        log.info(
            "alert_created",
            dam_id=dam.id,
            dam_name=dam.name,
            severity=fc.risk_level,
            forecast_date=fc.forecast_date.isoformat(),
            precipitation_mm=fc.max_precipitation_mm,
        )

    await session.flush()
    return created
