"""Cliente INMET — API pública de estações meteorológicas (apitempo.inmet.gov.br).

**Por quê:** Open-Meteo usa ERA5 (reanalysis) interpolado — bom para forecast,
razoável para histórico. INMET mede em estações reais; pra eventos passados
é a fonte primária brasileira. Usamos como **validação cruzada** do Open-Meteo
e como fonte adicional de dias extremos.

**Endpoints usados:**
  - `GET /estacoes/T`                         — lista todas estações automáticas
  - `GET /estacao/diaria/{start}/{end}/{cd}`  — dados diários de uma estação

**Regras de engenharia:**
- Cache de estações em Redis por 24h (lista raramente muda).
- Estação mais próxima via geopy.distance.geodesic.
- Fallback gracioso: qualquer erro (timeout, 5xx, JSON quebrado) levanta
  `InmetError` — chamador decide se tenta novamente ou pula e usa só Open-Meteo.
- **Feature flag:** `settings.inmet_enabled` — cliente só é chamado quando true.
  Respeita o mesmo contrato de `DailyForecast` do Open-Meteo para compor no
  aggregator sem duplicar dataclasses.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx
import redis.asyncio as aioredis
from geopy.distance import geodesic
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.services.climate.open_meteo import DailyForecast
from app.utils.logging import get_logger

log = get_logger(__name__)

REQUEST_TIMEOUT = 30.0
STATIONS_CACHE_KEY = "inmet:stations:T"
STATIONS_CACHE_TTL_SECONDS = 24 * 60 * 60  # 24h


class InmetError(Exception):
    """Base error for INMET client — chamadores tratam como fallback."""


@dataclass
class InmetStation:
    """Subset dos campos que usamos da /estacoes/T."""

    code: str  # CD_ESTACAO, ex "A509"
    name: str
    state: str
    latitude: float
    longitude: float

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> InmetStation | None:
        """Parse defensivo. Retorna None se campo crítico faltar."""
        code = raw.get("CD_ESTACAO")
        try:
            lat = float(raw.get("VL_LATITUDE"))
            lon = float(raw.get("VL_LONGITUDE"))
        except (TypeError, ValueError):
            return None
        if not code or lat == 0.0 or lon == 0.0:
            return None
        return cls(
            code=str(code).strip(),
            name=str(raw.get("DC_NOME", "")).strip(),
            state=str(raw.get("SG_ESTADO", "")).strip(),
            latitude=lat,
            longitude=lon,
        )


# ---------------------------------------------------------------------------
# Station list (cached)
# ---------------------------------------------------------------------------

_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def _fetch_stations_from_api() -> list[InmetStation]:
    url = f"{settings.inmet_base_url.rstrip('/')}/estacoes/T"
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, InmetError)),
        reraise=True,
    ):
        with attempt:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.get(url)
                if resp.status_code >= 500:
                    raise InmetError(f"INMET /estacoes/T HTTP {resp.status_code}")
                resp.raise_for_status()
                try:
                    payload = resp.json()
                except ValueError as exc:
                    raise InmetError(f"INMET /estacoes/T JSON parse: {exc}") from exc

    if not isinstance(payload, list):
        raise InmetError(f"INMET /estacoes/T retornou {type(payload).__name__}, esperava list")

    stations = [s for raw in payload if (s := InmetStation.from_api(raw))]
    log.info("inmet_stations_fetched", total_raw=len(payload), parsed=len(stations))
    return stations


async def list_stations() -> list[InmetStation]:
    """Lista estações automáticas. Cacheado 24h via Redis."""
    r = _get_redis()
    try:
        cached = await r.get(STATIONS_CACHE_KEY)
        if cached:
            data = json.loads(cached)
            return [InmetStation(**s) for s in data]
    except Exception as exc:
        log.warning("inmet_cache_read_failed", error=str(exc))

    stations = await _fetch_stations_from_api()
    try:
        serialized = json.dumps([s.__dict__ for s in stations])
        await r.set(STATIONS_CACHE_KEY, serialized, ex=STATIONS_CACHE_TTL_SECONDS)
    except Exception as exc:
        log.warning("inmet_cache_write_failed", error=str(exc))

    return stations


# ---------------------------------------------------------------------------
# Nearest station
# ---------------------------------------------------------------------------


async def nearest_station(
    lat: float, lon: float, *, state_filter: str | None = None
) -> tuple[InmetStation, float]:
    """Retorna (estação mais próxima, distância em km).

    Se `state_filter` fornecido (ex "MG"), restringe a estações daquele estado.
    Se nenhuma estação do estado existe, faz fallback pra busca global (log warn).
    Lança `InmetError` se nenhuma estação disponível.
    """
    stations = await list_stations()
    if not stations:
        raise InmetError("Nenhuma estação INMET disponível")

    candidates = stations
    if state_filter:
        scoped = [s for s in stations if s.state.upper() == state_filter.upper()]
        if scoped:
            candidates = scoped
        else:
            log.warning(
                "inmet_state_fallback_global",
                state_filter=state_filter,
                total_stations=len(stations),
            )

    target = (lat, lon)
    best: tuple[InmetStation, float] | None = None
    for st in candidates:
        d = geodesic(target, (st.latitude, st.longitude)).kilometers
        if best is None or d < best[1]:
            best = (st, d)
    assert best is not None  # non-empty checado acima
    return best


# ---------------------------------------------------------------------------
# Daily data
# ---------------------------------------------------------------------------


def _parse_daily(raw: dict[str, Any]) -> DailyForecast | None:
    """Converte uma linha de /estacao/diaria em DailyForecast.

    Campos INMET usados:
      - DT_MEDICAO (YYYY-MM-DD)
      - CHUVA (mm/24h, string com vírgula ou ponto)
      - TEM_MAX / TEM_MIN (°C)
      - VEN_VEL (m/s — convertemos pra km/h)
    """
    dt_str = raw.get("DT_MEDICAO")
    if not dt_str:
        return None
    try:
        measured = date.fromisoformat(dt_str)
    except ValueError:
        return None

    def _as_float(key: str) -> float | None:
        v = raw.get(key)
        if v is None or v == "":
            return None
        # INMET às vezes devolve string com vírgula
        s = str(v).replace(",", ".").strip()
        try:
            return float(s)
        except ValueError:
            return None

    chuva = _as_float("CHUVA") or 0.0
    tmax = _as_float("TEM_MAX")
    tmin = _as_float("TEM_MIN")
    vento_ms = _as_float("VEN_VEL")
    vento_kmh = vento_ms * 3.6 if vento_ms is not None else None

    return DailyForecast(
        date=measured,
        precipitation_mm=chuva,
        precipitation_probability_max=None,  # INMET não expõe (é medido, não previsão)
        max_temperature_c=tmax,
        min_temperature_c=tmin,
        wind_speed_max_kmh=vento_kmh,
        weather_code=None,  # sem WMO code no formato INMET
    )


async def get_daily_data(
    station_code: str, start: date, end: date
) -> list[DailyForecast]:
    """Busca medições diárias de uma estação. Retorna dias ordenados ASC.

    Endpoint: /estacao/diaria/{start}/{end}/{cd_estacao}
    """
    url = (
        f"{settings.inmet_base_url.rstrip('/')}"
        f"/estacao/diaria/{start.isoformat()}/{end.isoformat()}/{station_code}"
    )
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, InmetError)),
        reraise=True,
    ):
        with attempt:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.get(url)
                if resp.status_code == 404:
                    # INMET devolve 404 se não tem dados no período — tratamos
                    # como "sem dados" (lista vazia), não como erro.
                    log.info(
                        "inmet_daily_no_data",
                        station=station_code,
                        start=start.isoformat(),
                        end=end.isoformat(),
                    )
                    return []
                if resp.status_code >= 500:
                    raise InmetError(f"INMET /estacao/diaria HTTP {resp.status_code}")
                resp.raise_for_status()
                try:
                    payload = resp.json()
                except ValueError as exc:
                    raise InmetError(f"INMET /estacao/diaria JSON parse: {exc}") from exc

    if payload is None:
        return []
    if not isinstance(payload, list):
        raise InmetError(
            f"INMET /estacao/diaria retornou {type(payload).__name__}, esperava list"
        )

    days = [d for raw in payload if (d := _parse_daily(raw))]
    days.sort(key=lambda d: d.date)
    log.info(
        "inmet_daily_fetched",
        station=station_code,
        range_days=(end - start).days,
        parsed=len(days),
    )
    return days


# ---------------------------------------------------------------------------
# High-level helper
# ---------------------------------------------------------------------------


async def get_historical_for_coords(
    lat: float,
    lon: float,
    *,
    lookback_days: int = 30,
    state_filter: str | None = None,
) -> tuple[InmetStation, float, list[DailyForecast]]:
    """Retorna (estação escolhida, distância_km, dias) para um par lat/lon.

    Usado pelo aggregator quando `settings.inmet_enabled=True`. Se o cliente
    falhar, chamador deve capturar InmetError e seguir sem INMET.
    """
    station, distance_km = await nearest_station(lat, lon, state_filter=state_filter)
    end = date.today()
    start = end - timedelta(days=lookback_days)
    days = await get_daily_data(station.code, start, end)
    log.info(
        "inmet_historical_for_coords",
        lat=lat,
        lon=lon,
        station=station.code,
        station_name=station.name,
        distance_km=round(distance_km, 2),
        days=len(days),
    )
    return station, distance_km, days


__all__ = [
    "InmetError",
    "InmetStation",
    "list_stations",
    "nearest_station",
    "get_daily_data",
    "get_historical_for_coords",
]
