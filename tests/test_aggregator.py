"""Smoke do aggregator: scoring, detecção de eventos, dedup e alertas.

São as regras de domínio que mais sangram quando alguém ajusta thresholds
ou multiplicadores sem re-rodar tudo. Cada teste isola um comportamento:

- Risk scoring (default + tailings + DPA)
- Filtro de eventos abaixo do limiar
- Propagação de source_key/source_label (Open-Meteo vs ANA)
- Dedup ±2d em save_climate_events
- Idempotência de check_and_create_alerts
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.alert import Alert
from app.models.event import ClimateEvent
from app.models.forecast import Forecast
from app.services.climate import aggregator
from app.services.climate.open_meteo import DailyForecast

from tests.conftest import make_dam


# ---------------------------------------------------------------------------
# Risk scoring — escala default + multiplicadores
# ---------------------------------------------------------------------------


def test_compute_risk_score_default_dam_scale():
    """Barragem sem dam_type/dpa especiais segue a escala 50/100/150/250 → 2/3/4/5.

    Esses cortes vêm dos defaults em settings.alert_rain_mm_24h_*. Se alguém
    mudar os defaults sem atualizar SEVERITY_SCALE, este teste pega.
    """
    dam = make_dam(dam_type="hydropower", dpa="Baixo")

    # < moderate (50) → severity 1
    level, _, exceeded = aggregator.compute_risk_score(40.0, dam)
    assert level == 1
    assert exceeded is False

    # moderate → severity 2 (não dispara alerta — exceeded só em ≥3)
    level, _, exceeded = aggregator.compute_risk_score(50.0, dam)
    assert level == 2
    assert exceeded is False

    # high → severity 3 (alerta dispara)
    level, label, exceeded = aggregator.compute_risk_score(100.0, dam)
    assert level == 3
    assert label == "Alto"
    assert exceeded is True

    # very_high → severity 4
    level, _, _ = aggregator.compute_risk_score(150.0, dam)
    assert level == 4

    # critical → severity 5
    level, label, _ = aggregator.compute_risk_score(250.0, dam)
    assert level == 5
    assert label == "Crítico"


def test_compute_risk_score_tailings_reduces_threshold_by_20pct():
    """Barragens de rejeito têm thresholds *0.8 — 80mm vira severity 3.

    Sem o multiplicador, 80mm caía em severity 2 (entre moderate=50 e high=100).
    Com tailings: thresholds viram 40/80/120/200, então 80mm bate exatamente
    em high → severity 3.
    """
    tailings = make_dam(dam_type="tailings", dpa="Baixo")  # só tailings, sem DPA bonus
    normal = make_dam(dam_type="hydropower", dpa="Baixo")

    level_t, _, exceeded_t = aggregator.compute_risk_score(80.0, tailings)
    level_n, _, exceeded_n = aggregator.compute_risk_score(80.0, normal)

    assert level_t == 3
    assert exceeded_t is True
    assert level_n == 2  # mesma chuva, dam comum: não cruza alerta
    assert exceeded_n is False


def test_compute_risk_score_dpa_alto_reduces_threshold_by_10pct():
    """DPA=Alto multiplica thresholds por 0.9. 90mm vira severity 3 (vs 2 com DPA Baixo)."""
    high_dpa = make_dam(dam_type="hydropower", dpa="Alto")
    low_dpa = make_dam(dam_type="hydropower", dpa="Baixo")

    # threshold high vira 100*0.9 = 90 → 90mm é exatamente severity 3
    assert aggregator.compute_risk_score(90.0, high_dpa)[0] == 3
    assert aggregator.compute_risk_score(90.0, low_dpa)[0] == 2


# ---------------------------------------------------------------------------
# detect_extreme_events
# ---------------------------------------------------------------------------


def _day(d: date, mm: float, code: int = 61) -> DailyForecast:
    return DailyForecast(date=d, precipitation_mm=mm, weather_code=code)


def test_detect_extreme_events_skips_below_moderate_threshold():
    """Dias com severity < 2 (i.e. < moderate) não viram evento.

    Reduz ruído no banco — não queremos um evento por cada dia chuvoso.
    """
    dam = make_dam(dam_type="hydropower", dpa="Baixo")
    today = date(2026, 1, 10)
    days = [
        _day(today, 10.0),         # severity 1 → skip
        _day(today + timedelta(days=1), 49.9),  # severity 1 → skip
        _day(today + timedelta(days=2), 50.0),  # severity 2 → keep
        _day(today + timedelta(days=3), 200.0),  # severity 4 → keep
    ]

    events = aggregator.detect_extreme_events(days, dam)
    assert len(events) == 2
    assert events[0]["event_date"] == today + timedelta(days=2)
    assert events[0]["severity"] == 2
    assert events[1]["severity"] == 4


def test_detect_extreme_events_propagates_source_labels():
    """`source_key` controla a chave em raw_data; `source_label` vai pro campo `source`.

    A mesma função roda pra Open-Meteo (defaults) e pra ANA (`ana` / `ana_hidroweb`).
    Se alguém quebrar essa simetria a gente perde rastreabilidade da fonte.
    """
    dam = make_dam(dam_type="tailings", dpa="Alto")  # baixa o threshold pra garantir 1 evento
    days = [_day(date(2026, 1, 10), 100.0)]

    # Default = Open-Meteo
    om_events = aggregator.detect_extreme_events(days, dam)
    assert om_events[0]["source"] == "open_meteo_archive"
    assert "open_meteo" in om_events[0]["raw_data"]

    # ANA override
    ana_events = aggregator.detect_extreme_events(
        days, dam, source_key="ana", source_label="ana_hidroweb"
    )
    assert ana_events[0]["source"] == "ana_hidroweb"
    assert "ana" in ana_events[0]["raw_data"]
    assert "open_meteo" not in ana_events[0]["raw_data"]


# ---------------------------------------------------------------------------
# save_climate_events — dedup ±2d
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_climate_events_dedups_within_2_days(async_session, sample_dam):
    """Evento mesmo (dam, type) com event_date a 2 dias de outro existente → UPDATE."""
    base_date = date(2026, 1, 10)

    first = [
        {
            "event_date": base_date,
            "event_type": "heavy_rain",
            "severity": 2,
            "severity_label": "Moderado",
            "title": "Chuva moderada",
            "description": "...",
            "source_type": "weather",
            "source": "open_meteo_archive",
            "precipitation_mm": 60.0,
            "raw_data": {"open_meteo": {"precipitation_sum": 60.0}},
        }
    ]
    written = await aggregator.save_climate_events(async_session, sample_dam, first)
    assert written == 1

    # Segundo evento, 2 dias depois, severity maior — deve UPDATE não INSERT
    second = [
        {
            "event_date": base_date + timedelta(days=2),
            "event_type": "heavy_rain",
            "severity": 4,
            "severity_label": "Muito Alto",
            "title": "Chuva muito alta",
            "description": "...",
            "source_type": "weather",
            "source": "ana_hidroweb",
            "precipitation_mm": 180.0,
            "raw_data": {"ana": {"precipitation_sum": 180.0}},
        }
    ]
    written = await aggregator.save_climate_events(async_session, sample_dam, second)
    assert written == 0  # nada novo — apenas merge

    rows = (
        await async_session.execute(
            select(ClimateEvent).where(ClimateEvent.dam_id == sample_dam.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    # severity escalou pra 4 (mantém o pior); raw_data merge dos dois lados
    assert rows[0].severity == 4
    assert "open_meteo" in rows[0].raw_data
    assert "ana" in rows[0].raw_data


@pytest.mark.asyncio
async def test_save_climate_events_creates_new_event_for_4_day_gap(async_session, sample_dam):
    """Eventos com 4 dias de distância não dedupam — janela é ±2d."""
    base = date(2026, 1, 10)

    payload_1 = [
        {
            "event_date": base,
            "event_type": "heavy_rain",
            "severity": 3,
            "severity_label": "Alto",
            "title": "A",
            "description": "...",
            "source_type": "weather",
            "source": "open_meteo_archive",
            "precipitation_mm": 110.0,
            "raw_data": {"open_meteo": {"precipitation_sum": 110.0}},
        }
    ]
    payload_2 = [
        {
            "event_date": base + timedelta(days=4),
            "event_type": "heavy_rain",
            "severity": 3,
            "severity_label": "Alto",
            "title": "B",
            "description": "...",
            "source_type": "weather",
            "source": "open_meteo_archive",
            "precipitation_mm": 110.0,
            "raw_data": {"open_meteo": {"precipitation_sum": 110.0}},
        }
    ]

    await aggregator.save_climate_events(async_session, sample_dam, payload_1)
    await aggregator.save_climate_events(async_session, sample_dam, payload_2)

    rows = (
        await async_session.execute(
            select(ClimateEvent).where(ClimateEvent.dam_id == sample_dam.id)
        )
    ).scalars().all()
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# check_and_create_alerts — idempotência
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_and_create_alerts_is_idempotent(async_session, sample_dam):
    """Rodar duas vezes não duplica alertas pra mesma (dam, forecast_date).

    Cobre o cenário de Celery beat re-disparar a task antes do anterior
    expirar — não pode floodear o histórico de alertas.
    """
    forecast_date = date.today() + timedelta(days=2)
    fc = Forecast(
        dam_id=sample_dam.id,
        forecast_date=forecast_date,
        source="open_meteo",
        max_precipitation_mm=180.0,
        total_precipitation_mm=180.0,
        risk_level=4,
        risk_label="Muito Alto",
        alert_threshold_exceeded=True,
        weather_code=82,
        weather_description="Aguaceiros violentos",
        raw_data={"open_meteo": {"precipitation_sum": 180.0}},
    )
    async_session.add(fc)
    await async_session.flush()

    created_first = await aggregator.check_and_create_alerts(async_session, sample_dam)
    await async_session.flush()
    assert len(created_first) == 1

    # Segunda execução não deve criar nada novo
    created_second = await aggregator.check_and_create_alerts(async_session, sample_dam)
    await async_session.flush()
    assert len(created_second) == 0

    rows = (
        await async_session.execute(
            select(Alert).where(Alert.dam_id == sample_dam.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].is_active is True
    assert rows[0].forecast_date == forecast_date
    assert rows[0].severity == 4
