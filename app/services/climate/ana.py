"""Cliente ANA Hidrowebservice — REST autenticado (www.ana.gov.br/hidrowebservice).

**Por quê:** Open-Meteo usa ERA5 (reanálise) interpolado — bom pra forecast e OK
pra histórico, mas não é dado observado. A ANA mantém a rede hidrometeorológica
oficial brasileira; chuva convencional publicada aqui é **ground truth** com
validação por operadora (`Nivel_Consistencia=2`). Usamos como **validação
cruzada** do Open-Meteo e como fonte autoritativa em relatórios.

**Gotcha importante — lag:** "Convencional" = coleta manual (pluviômetro
lido por operador humano). A operadora coleta, submete à ANA, ANA publica.
Lag típico: 2-6 meses. Dados **não** cobrem a janela recente do Open-Meteo
(<30d). Por isso o aggregator dedup por `event_date ± 2d` — quando ANA chega
com dado antigo, mescla em eventos já detectados pelo Open-Meteo, adicionando
autoridade sem duplicar.

**Telemétrica (tempo real):** o endpoint `HidroinfoanaSerieTelemetrica*/v2`
existe mas precisa de mais reverse engineering (testamos várias estações
marcadas `Tipo_Estacao_Telemetrica=1` e retornaram `items: []`). Provável
diferença de codificação de estação. Fica como TODO; Open-Meteo cobre o
caso recente.

**Endpoints usados:**
  - `GET /OAUth/v1`                                — auth (headers Identificador + Senha)
  - `GET /HidroInventarioEstacoes/v1`              — inventário de estações (por UF)
  - `GET /HidroSerieChuva/v1`                      — série mensal de chuva convencional

**Regras de engenharia:**
- Token JWT cacheado em Redis (TTL = exp - 60s). Expira em ~1h; evita pedir
  auth a cada task.
- Inventário por UF cacheado em Redis 24h (5k+ estações por estado, raro mudar).
- Estação mais próxima via `geopy.distance.geodesic` filtrando por
  `Tipo_Estacao_Pluviometro='1'` + `Operando='1'`.
- Chuva: cada item é 1 mês com `Chuva_01..Chuva_31` (mm/dia). Expandimos pra
  `DailyForecast` diário. Respeita limite da API de 366 dias por chamada.
- Feature flag `settings.ana_enabled` — chamador (climate_tasks) só invoca
  quando true e absorve qualquer `AnaError` como "log e segue sem ANA".
"""
from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
USER_AGENT = "dia-bot/1.0 (+https://dia.linkwise.digital)"

# Token JWT dura 1h. Cacheamos com margem de 60s pra não usar quase-expirado.
TOKEN_CACHE_KEY = "ana:token"
TOKEN_SAFETY_MARGIN_SECONDS = 60

# Inventário por UF: pesado (~11MB em MG), mas raramente muda.
STATIONS_CACHE_KEY_TMPL = "ana:stations:uf:{uf}"
STATIONS_CACHE_TTL_SECONDS = 24 * 60 * 60

# Tipo de medição marcador — vem como string "0"/"1" no JSON.
TIPO_PLUVIOMETRO = "1"
OPERANDO = "1"


class AnaError(Exception):
    """Erro base do cliente ANA. Chamador trata como fallback."""


class AnaAuthError(AnaError):
    """Falha de autenticação — credenciais ausentes, inválidas ou token expirado."""


@dataclass
class AnaStation:
    """Subset dos campos do HidroInventarioEstacoes que usamos."""

    code: int  # codigoestacao — chave primária usada nos endpoints de dados
    name: str
    state: str
    municipality: str
    latitude: float
    longitude: float
    is_pluviometric: bool
    is_operating: bool

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> AnaStation | None:
        """Parse defensivo. Retorna None se campo crítico faltar."""
        code_raw = raw.get("codigoestacao")
        try:
            lat = float(raw["Latitude"])
            lon = float(raw["Longitude"])
            code = int(str(code_raw).strip())
        except (TypeError, ValueError, KeyError):
            return None
        return cls(
            code=code,
            name=str(raw.get("Estacao_Nome") or "").strip(),
            state=str(raw.get("UF_Estacao") or "").strip().upper(),
            municipality=str(raw.get("Municipio_Nome") or "").strip(),
            latitude=lat,
            longitude=lon,
            is_pluviometric=str(raw.get("Tipo_Estacao_Pluviometro") or "0") == TIPO_PLUVIOMETRO,
            is_operating=str(raw.get("Operando") or "0") == OPERANDO,
        )


# ---------------------------------------------------------------------------
# Redis helper
# ---------------------------------------------------------------------------

_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


# ---------------------------------------------------------------------------
# OAUth — obter e cachear Bearer token
# ---------------------------------------------------------------------------


def _jwt_exp_unix(token: str) -> int | None:
    """Extrai `exp` de um JWT sem validar assinatura.

    ANA usa HS512 mas não precisamos verificar a assinatura pra saber
    quando expira — só pra ter uma TTL boa no cache. Defensivo: retorna
    None se o token não parecer JWT ou parse falhar.
    """
    import base64

    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload_b64 = parts[1]
        payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return None
    exp = payload.get("exp")
    return int(exp) if exp is not None else None


async def _request_new_token() -> tuple[str, int]:
    """Chama OAUth/v1 e devolve (token, ttl_seconds). Lança AnaAuthError."""
    if not settings.ana_user or not settings.ana_pass:
        raise AnaAuthError(
            "ANA_USER/ANA_PASS não configurados — impossível obter token."
        )

    url = f"{settings.ana_base_url.rstrip('/')}/EstacoesTelemetricas/OAUth/v1"
    headers = {
        "Identificador": settings.ana_user,
        "Senha": settings.ana_pass,
        "User-Agent": USER_AGENT,
    }

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    ):
        with attempt:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 401:
                    raise AnaAuthError(
                        f"ANA OAUth retornou 401 — credenciais inválidas."
                    )
                if resp.status_code >= 500:
                    # Força retry do tenacity
                    raise httpx.HTTPError(
                        f"ANA OAUth HTTP {resp.status_code}"
                    )
                resp.raise_for_status()
                try:
                    payload = resp.json()
                except ValueError as exc:
                    raise AnaError(f"ANA OAUth JSON parse: {exc}") from exc

    items = payload.get("items") or {}
    token = items.get("tokenautenticacao")
    if not token:
        raise AnaAuthError(
            f"ANA OAUth não retornou tokenautenticacao: {payload}"
        )

    # TTL a partir do JWT exp; fallback 3300s (55min) se não conseguir decodar.
    exp_unix = _jwt_exp_unix(token)
    if exp_unix is None:
        ttl = 3300
    else:
        now = int(datetime.now(tz=timezone.utc).timestamp())
        ttl = max(60, exp_unix - now - TOKEN_SAFETY_MARGIN_SECONDS)

    log.info("ana_token_refreshed", ttl_seconds=ttl)
    return token, ttl


async def _get_token(*, force_refresh: bool = False) -> str:
    """Retorna um Bearer válido. Cacheia no Redis respeitando o exp do JWT."""
    r = _get_redis()
    if not force_refresh:
        try:
            cached = await r.get(TOKEN_CACHE_KEY)
            if cached:
                return cached
        except Exception as exc:
            log.warning("ana_token_cache_read_failed", error=str(exc))

    token, ttl = await _request_new_token()
    try:
        await r.set(TOKEN_CACHE_KEY, token, ex=ttl)
    except Exception as exc:
        log.warning("ana_token_cache_write_failed", error=str(exc))
    return token


async def _authed_get(
    path: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """GET com Bearer ANA + retry com refresh de token em 401.

    Lança AnaError em falha definitiva. Retorna o JSON body parseado.
    """
    url = f"{settings.ana_base_url.rstrip('/')}{path}"

    # Duas tentativas no nível da auth: a primeira com cache, a segunda
    # forçando refresh (cobre o caso de token invalidado no servidor antes do
    # exp local). Dentro de cada uma, tenacity ainda cuida de 5xx transientes.
    auth_attempts = 0
    last_exc: Exception | None = None
    while auth_attempts < 2:
        token = await _get_token(force_refresh=auth_attempts > 0)
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=1, max=10),
                retry=retry_if_exception_type(httpx.HTTPError),
                reraise=True,
            ):
                with attempt:
                    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                        resp = await client.get(url, headers=headers, params=params)
                        if resp.status_code == 401:
                            # Sinaliza auth retry no loop externo
                            raise AnaAuthError(f"ANA {path} 401")
                        if resp.status_code >= 500:
                            raise httpx.HTTPError(f"ANA {path} HTTP {resp.status_code}")
                        resp.raise_for_status()
                        try:
                            return resp.json()
                        except ValueError as exc:
                            raise AnaError(
                                f"ANA {path} JSON parse: {exc}"
                            ) from exc
            # `return` acima sai do while; o `else` não é necessário.
        except AnaAuthError as exc:
            last_exc = exc
            auth_attempts += 1
            log.warning("ana_auth_retry", path=path, attempt=auth_attempts)
            continue

    raise AnaAuthError(f"ANA {path} falhou após refresh de token: {last_exc}")


# ---------------------------------------------------------------------------
# Inventário de estações (cacheado por UF)
# ---------------------------------------------------------------------------


async def _fetch_stations_for_state(uf: str) -> list[AnaStation]:
    """Busca inventário completo de estações de uma UF."""
    uf = uf.upper().strip()
    payload = await _authed_get(
        "/EstacoesTelemetricas/HidroInventarioEstacoes/v1",
        params={"Unidade Federativa": uf},
    )
    items = payload.get("items") or []
    if not isinstance(items, list):
        raise AnaError(
            f"ANA HidroInventarioEstacoes retornou items={type(items).__name__}"
        )
    stations = [s for raw in items if (s := AnaStation.from_api(raw))]
    log.info(
        "ana_inventario_fetched",
        uf=uf,
        raw=len(items),
        parsed=len(stations),
    )
    return stations


async def list_stations_for_state(uf: str) -> list[AnaStation]:
    """Lista estações de uma UF. Cache Redis 24h por UF."""
    uf_key = uf.upper().strip()
    r = _get_redis()
    cache_key = STATIONS_CACHE_KEY_TMPL.format(uf=uf_key)
    try:
        cached = await r.get(cache_key)
        if cached:
            data = json.loads(cached)
            return [AnaStation(**s) for s in data]
    except Exception as exc:
        log.warning("ana_cache_read_failed", uf=uf_key, error=str(exc))

    stations = await _fetch_stations_for_state(uf_key)
    try:
        serialized = json.dumps([s.__dict__ for s in stations])
        await r.set(cache_key, serialized, ex=STATIONS_CACHE_TTL_SECONDS)
    except Exception as exc:
        log.warning("ana_cache_write_failed", uf=uf_key, error=str(exc))
    return stations


async def nearest_pluvio_station(
    lat: float, lon: float, *, state_filter: str | None = None
) -> tuple[AnaStation, float]:
    """Retorna (estação pluviométrica mais próxima, distância_km).

    Filtra `is_pluviometric` + `is_operating`. Se `state_filter` fornecido
    busca só naquele UF (muito mais rápido que varrer BR inteiro — cada UF
    tem 3-10k estações). Se nenhuma candidata no estado, levanta AnaError.
    """
    if not state_filter:
        raise AnaError(
            "ANA nearest_pluvio_station requer state_filter — "
            "inventário é paginado por UF."
        )

    stations = await list_stations_for_state(state_filter)
    candidates = [s for s in stations if s.is_pluviometric and s.is_operating]
    if not candidates:
        raise AnaError(
            f"Nenhuma estação pluviométrica ativa em {state_filter}."
        )

    target = (lat, lon)
    best: tuple[AnaStation, float] | None = None
    for st in candidates:
        d = geodesic(target, (st.latitude, st.longitude)).kilometers
        if best is None or d < best[1]:
            best = (st, d)
    assert best is not None
    return best


# ---------------------------------------------------------------------------
# Chuva convencional — HidroSerieChuva
# ---------------------------------------------------------------------------


def _parse_rainfall_month(raw: dict[str, Any]) -> list[DailyForecast]:
    """Expande um item mensal em até 31 registros diários.

    ANA devolve cada mês assim:
        {
          "Data_Hora_Dado": "2024-01-01 00:00:00.0",
          "Chuva_01": "9.6", "Chuva_01_Status": "1", ...
          "Chuva_31": "3.6", "Chuva_31_Status": "1",
          "Total": "455.2", "Maxima": "98.2", "Dia_Maxima": "24",
          "Nivel_Consistencia": "1" | "2",
          "codigoestacao": "1942008"
        }

    Dias inexistentes no mês (ex: Fev/30, Abr/31) ou com Chuva_XX=None
    são pulados. Status != "1" é preservado mas logado com warning
    agregado no chamador (não aqui, pra não poluir).
    """
    dth = raw.get("Data_Hora_Dado")
    if not dth:
        return []
    try:
        # "2024-01-01 00:00:00.0" — só precisamos de YYYY-MM
        month_start = date.fromisoformat(str(dth)[:10])
    except ValueError:
        return []

    year = month_start.year
    month = month_start.month
    days_in_month = calendar.monthrange(year, month)[1]

    out: list[DailyForecast] = []
    for day in range(1, days_in_month + 1):
        key = f"Chuva_{day:02d}"
        v = raw.get(key)
        if v is None or v == "":
            continue
        try:
            mm = float(str(v).replace(",", "."))
        except ValueError:
            continue
        out.append(
            DailyForecast(
                date=date(year, month, day),
                precipitation_mm=mm,
                # Campos abaixo não existem no endpoint de chuva convencional.
                precipitation_probability_max=None,
                max_temperature_c=None,
                min_temperature_c=None,
                wind_speed_max_kmh=None,
                weather_code=None,
            )
        )
    return out


async def get_rainfall(
    station_code: int, start: date, end: date
) -> list[DailyForecast]:
    """Busca chuva diária de uma estação. Retorna dias ordenados ASC.

    A API limita 366 dias por chamada. `(end - start)` deve respeitar isso;
    o chamador (`get_historical_for_coords`) já garante com lookback_months<=12.

    Quando a mesma data aparece em bruto (`Nivel_Consistencia=1`) e consistido
    (`=2`), o consistido vence — é o dado revisado pela operadora.
    """
    if (end - start).days > 366:
        raise AnaError(
            f"get_rainfall: janela {(end-start).days}d excede limite de 366d da ANA"
        )

    payload = await _authed_get(
        "/EstacoesTelemetricas/HidroSerieChuva/v1",
        params={
            "Código da Estação": station_code,
            "Tipo Filtro Data": "DATA_LEITURA",
            "Data Inicial (yyyy-MM-dd)": start.isoformat(),
            "Data Final (yyyy-MM-dd)": end.isoformat(),
        },
    )

    items = payload.get("items") or []
    if not isinstance(items, list):
        raise AnaError(
            f"ANA HidroSerieChuva retornou items={type(items).__name__}"
        )
    if not items:
        log.info(
            "ana_rainfall_no_data",
            station=station_code,
            start=start.isoformat(),
            end=end.isoformat(),
            message=str(payload.get("message", "")),
        )
        return []

    # Dedup por data preferindo Nivel_Consistencia=2 (consistido) sobre 1 (bruto).
    by_date: dict[date, tuple[int, DailyForecast]] = {}
    for raw in items:
        consistencia = int(str(raw.get("Nivel_Consistencia") or "0") or 0)
        for day in _parse_rainfall_month(raw):
            prev = by_date.get(day.date)
            if prev is None or consistencia > prev[0]:
                by_date[day.date] = (consistencia, day)

    # Filtra pela janela pedida (item mensal traz o mês inteiro).
    days = [
        d for _, d in by_date.values() if start <= d.date <= end
    ]
    days.sort(key=lambda d: d.date)

    log.info(
        "ana_rainfall_fetched",
        station=station_code,
        start=start.isoformat(),
        end=end.isoformat(),
        months=len(items),
        daily_rows=len(days),
    )
    return days


# ---------------------------------------------------------------------------
# High-level helper — usado pelo climate_tasks
# ---------------------------------------------------------------------------


def _clamp_window_to_366d(start: date, end: date) -> tuple[date, date]:
    """Garante janela <= 366 dias (limite da API). Ajusta `start` se preciso."""
    if (end - start).days > 366:
        start = end - timedelta(days=366)
    return start, end


async def get_historical_for_coords(
    lat: float,
    lon: float,
    *,
    lookback_months: int = 6,
    state_filter: str,
) -> tuple[AnaStation, float, list[DailyForecast]]:
    """Retorna (estação escolhida, distância_km, dias de chuva).

    **Escopo:** chuva convencional — dados observados, com lag 2-6 meses.
    Não serve pra alerta em tempo real; serve pra reanálise histórica e
    validação cruzada com Open-Meteo em relatórios.
    """
    if lookback_months < 1:
        raise AnaError(f"lookback_months deve ser >=1, recebi {lookback_months}")

    station, distance_km = await nearest_pluvio_station(
        lat, lon, state_filter=state_filter
    )

    end = date.today()
    # lookback_months aproximado: 30 dias/mês. Melhor dar um pouco a mais
    # pra garantir que eventos na borda do mês entrem.
    start = end - timedelta(days=lookback_months * 31)
    start, end = _clamp_window_to_366d(start, end)

    days = await get_rainfall(station.code, start, end)
    log.info(
        "ana_historical_for_coords",
        lat=lat,
        lon=lon,
        station=station.code,
        station_name=station.name,
        distance_km=round(distance_km, 2),
        lookback_months=lookback_months,
        days=len(days),
    )
    return station, distance_km, days


__all__ = [
    "AnaError",
    "AnaAuthError",
    "AnaStation",
    "list_stations_for_state",
    "nearest_pluvio_station",
    "get_rainfall",
    "get_historical_for_coords",
]
