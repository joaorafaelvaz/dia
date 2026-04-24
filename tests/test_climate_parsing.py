"""Smoke da camada de parsing climático: Open-Meteo + ANA.

Foco: shape de dados externos (JSON da API) → dataclasses internas.
São os pontos mais fáceis de quebrar silenciosamente quando o provedor
muda nomenclatura ou tipos sem aviso. Cada teste é offline:

- Open-Meteo: `pytest-httpx` mocka as duas URLs (forecast + archive).
- ANA: usamos as fixtures JSON já commitadas em `scripts/fixtures/ana/`
  pra exercitar `_parse_rainfall_month`. Não precisa de mock — é parse
  puro de dict.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.services.climate import ana, open_meteo

ANA_FIXTURES = Path(__file__).resolve().parent.parent / "scripts" / "fixtures" / "ana"


# ---------------------------------------------------------------------------
# Open-Meteo — payload helpers
# ---------------------------------------------------------------------------


def _open_meteo_forecast_payload(days: int = 3) -> dict:
    """Shape mínimo equivalente ao que `/v1/forecast` devolve.

    Mantemos só os campos `daily.*` que `_parse_daily` consome — o teste
    deve pegar regressão se o nome de algum desses mudar (ex.: a API
    voltar a chamar `weathercode` de `weather_code` ou vice-versa).
    """
    today = date.today()
    dates = [(today + timedelta(days=i)).isoformat() for i in range(days)]
    return {
        "latitude": -20.39,
        "longitude": -43.50,
        "timezone": "America/Sao_Paulo",
        "daily": {
            "time": dates,
            "precipitation_sum": [12.5, 0.0, 88.3],
            "precipitation_probability_max": [70, 5, 95],
            "temperature_2m_max": [27.4, 28.1, 24.0],
            "temperature_2m_min": [18.2, 19.0, 17.5],
            "windspeed_10m_max": [22.0, 18.4, 41.2],
            "weathercode": [61, 2, 82],
        },
    }


def _open_meteo_archive_payload() -> dict:
    """Archive endpoint usa shape idêntico ao forecast — só sem `precipitation_probability_max`."""
    return {
        "latitude": -20.39,
        "longitude": -43.50,
        "timezone": "America/Sao_Paulo",
        "daily": {
            "time": ["2026-01-15", "2026-01-16"],
            "precipitation_sum": [142.7, 9.4],
            "temperature_2m_max": [29.0, 30.2],
            "temperature_2m_min": [21.0, 21.5],
            "windspeed_10m_max": [33.0, 18.0],
            "weathercode": [82, 3],
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_open_meteo_get_forecast_parses_happy_path(httpx_mock):
    """Forecast: 3 dias, todos os campos presentes — parse 1:1."""
    # pytest-httpx faz match na URL completa (com query string). A request
    # final inclui ?latitude=...&longitude=...&daily=... — então usamos
    # um regex prefix-match em vez de URL exata.
    httpx_mock.add_response(
        url=re.compile(re.escape(open_meteo.settings.open_meteo_forecast_url) + r".*"),
        json=_open_meteo_forecast_payload(days=3),
    )

    resp = await open_meteo.get_forecast(-20.39, -43.50, days=3)
    assert len(resp.days) == 3
    d2 = resp.days[2]
    assert d2.precipitation_mm == 88.3
    assert d2.precipitation_probability_max == 95
    assert d2.weather_code == 82
    assert d2.weather_description == "Aguaceiros violentos"  # WMO 82 → PT-BR


async def test_open_meteo_get_historical_parses_archive(httpx_mock):
    """Archive endpoint: shape compartilhado; campos de probability não vêm."""
    httpx_mock.add_response(
        url=re.compile(re.escape(open_meteo.settings.open_meteo_archive_url) + r".*"),
        json=_open_meteo_archive_payload(),
    )

    resp = await open_meteo.get_historical(
        -20.39, -43.50, date(2026, 1, 15), date(2026, 1, 16)
    )
    assert len(resp.days) == 2
    assert resp.days[0].precipitation_mm == 142.7
    # Archive não traz probabilidade — campo deve ficar None.
    assert resp.days[0].precipitation_probability_max is None


async def test_open_meteo_retries_on_5xx_then_succeeds(httpx_mock):
    """500 → 200 — tenacity tem que retentar e não propagar a primeira falha.

    Esse teste ataca o coração do retry do `_fetch`: se alguém remover
    `retry_if_exception_type(httpx.HTTPError)`, a primeira 500 cairia
    direto pra exception em vez de retentar.
    """
    url_pattern = re.compile(re.escape(open_meteo.settings.open_meteo_forecast_url) + r".*")
    httpx_mock.add_response(url=url_pattern, status_code=500, text="upstream error")
    httpx_mock.add_response(url=url_pattern, json=_open_meteo_forecast_payload(days=1))

    resp = await open_meteo.get_forecast(-20.39, -43.50, days=1)
    assert len(resp.days) == 1


def test_ana_parse_rainfall_month_against_real_fixture():
    """Replica a checagem do smoke_ana, mas integrada ao pytest.

    Se algum dia mudarmos `_parse_rainfall_month` e o smoke offline
    sair sem rodar (ex.: só rodam pytest no CI), esse teste pega.
    """
    payload = json.loads(
        (ANA_FIXTURES / "chuva_station_1942008_2024.json").read_text(encoding="utf-8")
    )
    items = payload["items"]
    by_date: dict[date, tuple[int, float]] = {}
    for it in items:
        cons = int(str(it.get("Nivel_Consistencia") or "0") or 0)
        for d in ana._parse_rainfall_month(it):
            prev = by_date.get(d.date)
            if prev is None or cons > prev[0]:
                by_date[d.date] = (cons, d.precipitation_mm)

    # Janeiro 2024: API expõe `Total=455.2`; nossa soma diária tem que bater.
    jan = [mm for dt, (_, mm) in by_date.items() if dt.year == 2024 and dt.month == 1]
    assert abs(sum(jan) - 455.2) < 0.5, f"jan sum={sum(jan)} esperado 455.2"

    # Pico do mês: dia 24, 98.2 mm — bate com o `Maxima` / `Dia_Maxima` da API.
    jan_by_day = {dt.day: mm for dt, (_, mm) in by_date.items() if dt.year == 2024 and dt.month == 1}
    assert jan_by_day[24] == 98.2


def test_ana_jwt_exp_extraction_handles_padding_and_garbage():
    """Cobre os três casos que `_jwt_exp_unix` precisa tratar:

    1. JWT bem-formado com padding base64 implícito (`exp` num int)
    2. String não-JWT → None (pode acontecer se a ANA mudar o formato)
    3. JWT com payload válido mas sem `exp` → None (não cacheia eternamente)
    """
    import base64
    import json as _json

    def make_jwt(payload: dict) -> str:
        header = base64.urlsafe_b64encode(b'{"alg":"HS512","typ":"JWT"}').rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(_json.dumps(payload).encode()).rstrip(b"=").decode()
        return f"{header}.{body}.signaturefake"

    assert ana._jwt_exp_unix(make_jwt({"exp": 1700000000})) == 1700000000
    assert ana._jwt_exp_unix("not.a.jwt") is None
    assert ana._jwt_exp_unix(make_jwt({"sub": "user"})) is None  # sem exp


def test_ana_clamp_window_to_366d_clamps_long_window_keeps_short():
    """A API rejeita janelas > 366d; nossa clamp tem que respeitar isso."""
    end = date(2026, 4, 24)
    long_start = end - timedelta(days=500)  # 500d > 366
    short_start = end - timedelta(days=120)  # ok

    s, e = ana._clamp_window_to_366d(long_start, end)
    assert (e - s).days == 366
    assert e == end  # `end` nunca muda — ajustamos só o `start`

    s, e = ana._clamp_window_to_366d(short_start, end)
    assert s == short_start  # janela curta passa intacta
    assert e == end
