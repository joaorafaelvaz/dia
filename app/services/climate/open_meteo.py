"""Open-Meteo API client.

Free, no API key. Two endpoints:
  - Forecast: api.open-meteo.com/v1/forecast (up to 16 days)
  - Archive: archive-api.open-meteo.com/v1/archive (historical daily data)

WMO weather code reference: https://open-meteo.com/en/docs
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.utils.logging import get_logger

log = get_logger(__name__)

TIMEZONE = "America/Sao_Paulo"
REQUEST_TIMEOUT = 30.0

WMO_CODES: dict[int, str] = {
    0: "Céu limpo",
    1: "Principalmente limpo",
    2: "Parcialmente nublado",
    3: "Nublado",
    45: "Neblina",
    48: "Neblina com gelo",
    51: "Garoa leve",
    53: "Garoa moderada",
    55: "Garoa intensa",
    61: "Chuva leve",
    63: "Chuva moderada",
    65: "Chuva forte",
    66: "Chuva congelante leve",
    67: "Chuva congelante forte",
    71: "Neve leve",
    73: "Neve moderada",
    75: "Neve intensa",
    80: "Aguaceiros leves",
    81: "Aguaceiros moderados",
    82: "Aguaceiros violentos",
    95: "Trovoada",
    96: "Trovoada com granizo leve",
    99: "Trovoada com granizo forte",
}


def describe_weather(code: int | None) -> str:
    if code is None:
        return "Desconhecido"
    return WMO_CODES.get(code, f"Código WMO {code}")


@dataclass
class DailyForecast:
    """One day of forecast or historical weather for a location."""

    date: date
    precipitation_mm: float = 0.0
    precipitation_probability_max: float | None = None
    max_temperature_c: float | None = None
    min_temperature_c: float | None = None
    wind_speed_max_kmh: float | None = None
    weather_code: int | None = None

    @property
    def weather_description(self) -> str:
        return describe_weather(self.weather_code)


@dataclass
class ForecastResponse:
    latitude: float
    longitude: float
    timezone: str
    days: list[DailyForecast] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


async def _fetch(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """httpx GET with tenacity retry (exponential backoff, up to 3 attempts)."""
    last_exc: Exception | None = None
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    ):
        with attempt:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
    # unreachable (reraise=True), appease type checker
    raise RuntimeError("unreachable") from last_exc


def _parse_daily(payload: dict[str, Any]) -> list[DailyForecast]:
    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    precip = daily.get("precipitation_sum") or [None] * len(dates)
    precip_prob = daily.get("precipitation_probability_max") or [None] * len(dates)
    t_max = daily.get("temperature_2m_max") or [None] * len(dates)
    t_min = daily.get("temperature_2m_min") or [None] * len(dates)
    wind = (
        daily.get("windspeed_10m_max")
        or daily.get("wind_speed_10m_max")
        or [None] * len(dates)
    )
    codes = daily.get("weathercode") or daily.get("weather_code") or [None] * len(dates)

    out: list[DailyForecast] = []
    for i, day_str in enumerate(dates):
        try:
            day = date.fromisoformat(day_str)
        except (TypeError, ValueError):
            continue
        out.append(
            DailyForecast(
                date=day,
                precipitation_mm=float(precip[i] or 0.0),
                precipitation_probability_max=precip_prob[i],
                max_temperature_c=t_max[i],
                min_temperature_c=t_min[i],
                wind_speed_max_kmh=wind[i],
                weather_code=codes[i],
            )
        )
    return out


async def get_forecast(latitude: float, longitude: float, days: int = 16) -> ForecastResponse:
    """Fetch up to 16 days of forecast for a location."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": ",".join(
            [
                "precipitation_sum",
                "precipitation_probability_max",
                "temperature_2m_max",
                "temperature_2m_min",
                "windspeed_10m_max",
                "weathercode",
            ]
        ),
        "timezone": TIMEZONE,
        "forecast_days": max(1, min(days, 16)),
        "models": "best_match",
    }
    log.info("open_meteo_forecast_fetch", lat=latitude, lon=longitude, days=days)
    data = await _fetch(settings.open_meteo_forecast_url, params)
    return ForecastResponse(
        latitude=latitude,
        longitude=longitude,
        timezone=data.get("timezone", TIMEZONE),
        days=_parse_daily(data),
        raw=data,
    )


async def get_historical(
    latitude: float, longitude: float, start_date: date, end_date: date
) -> ForecastResponse:
    """Fetch historical daily weather for a date range."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": ",".join(
            [
                "precipitation_sum",
                "temperature_2m_max",
                "temperature_2m_min",
                "windspeed_10m_max",
                "weathercode",
            ]
        ),
        "timezone": TIMEZONE,
    }
    log.info(
        "open_meteo_archive_fetch",
        lat=latitude,
        lon=longitude,
        start=start_date.isoformat(),
        end=end_date.isoformat(),
    )
    data = await _fetch(settings.open_meteo_archive_url, params)
    return ForecastResponse(
        latitude=latitude,
        longitude=longitude,
        timezone=data.get("timezone", TIMEZONE),
        days=_parse_daily(data),
        raw=data,
    )
