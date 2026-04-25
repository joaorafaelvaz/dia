"""Smoke do test harness.

Cobre:
1. POST /test-harness/alerts cria Alert(is_test=True) com notified_*=False —
   sweep do dispatcher pegaria normal.
2. POST /test-harness/alerts com send_notification=False pré-marca
   notified_*=True, suprimindo o sweep.
3. POST /test-harness/forecasts com precip alta dispara aggregator e Alert
   resultante herda is_test=True.
4. context_builder com include_test=False (default) ignora alerts/forecasts
   sintéticos; com include_test=True os inclui.
5. DELETE /test-harness/data?older_than_days=N apaga só registros is_test=True
   anteriores ao cutoff.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from sqlalchemy import select

from app.models.alert import Alert
from app.models.forecast import Forecast
from app.services.ai import context_builder

from tests.conftest import make_alert, make_forecast


@pytest.mark.asyncio
async def test_create_test_alert_marks_is_test_and_leaves_dispatcher_in_play(
    api_client, sample_dam
):
    """Modo A com send_notification=True: cria Alert(is_test=True), notified_*=False."""
    payload = {
        "dam_id": sample_dam.id,
        "alert_type": "threshold_exceeded",
        "severity": 4,
        "title": "[TESTE] Alerta de validação",
        "message": "Mensagem de teste",
        "send_notification": True,
    }
    resp = await api_client.post("/api/v1/test-harness/alerts", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["is_test"] is True
    assert body["send_notification"] is True
    assert body["alert_id"] is not None

    # Confirma persistência: alert tem is_test=True e flags de notif False
    # (sweep vai disparar em produção).
    alert = await api_client.get(f"/api/v1/alerts?is_active=true")
    found = next(a for a in alert.json() if a["id"] == body["alert_id"])
    assert found["is_test"] is True
    assert found["notified_whatsapp"] is False
    assert found["notified_email"] is False


@pytest.mark.asyncio
async def test_create_test_alert_with_send_notification_false_suppresses_dispatcher(
    api_client, sample_dam
):
    """Modo A silencioso: notified_*=True na criação → dispatcher pula no sweep."""
    payload = {
        "dam_id": sample_dam.id,
        "alert_type": "threshold_exceeded",
        "severity": 5,
        "title": "[TESTE] Sem notif",
        "message": "Pra testar relatório sem barulhar canal",
        "send_notification": False,
    }
    resp = await api_client.post("/api/v1/test-harness/alerts", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["send_notification"] is False

    alert_list = await api_client.get("/api/v1/alerts?is_active=true")
    found = next(a for a in alert_list.json() if a["id"] == body["alert_id"])
    # Pré-marcadas como já enviadas — o WHERE notified_*=False do dispatcher
    # nunca pega esse alert.
    assert found["notified_whatsapp"] is True
    assert found["notified_email"] is True


@pytest.mark.asyncio
async def test_create_test_forecast_with_high_precipitation_creates_test_alert(
    api_client, sample_dam
):
    """Modo B: precip 250mm em barragem tailings/Alto cruza threshold facilmente.

    Aggregator gera Alert via check_and_create_alerts; is_test deve propagar.
    """
    payload = {
        "dam_id": sample_dam.id,
        "forecast_date": (date.today() + timedelta(days=2)).isoformat(),
        "max_precipitation_mm": 250.0,
        "send_notification": True,
    }
    resp = await api_client.post("/api/v1/test-harness/forecasts", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["forecast_id"] is not None
    assert body["alert_id"] is not None, body["detail"]
    assert body["is_test"] is True

    # Alert criado pelo aggregator deve estar marcado is_test=True
    alert_list = await api_client.get("/api/v1/alerts?is_active=true")
    found = next(a for a in alert_list.json() if a["id"] == body["alert_id"])
    assert found["is_test"] is True
    assert found["alert_type"] == "forecast_warning"


@pytest.mark.asyncio
async def test_context_builder_excludes_test_data_by_default(
    async_session, sample_dam
):
    """include_test=False (default) filtra Alert/Forecast com is_test=True.

    Cobre o caso real em produção: relatórios automáticos do cron NÃO podem
    pegar lixo do test harness.
    """
    real_alert = make_alert(
        dam_id=sample_dam.id, severity=4, title="Real", is_test=False
    )
    test_alert = make_alert(
        dam_id=sample_dam.id, severity=5, title="[TESTE] Sintético", is_test=True
    )
    real_fc = make_forecast(
        dam_id=sample_dam.id, max_precipitation_mm=200.0, is_test=False
    )
    test_fc = make_forecast(
        dam_id=sample_dam.id, max_precipitation_mm=300.0, is_test=True
    )
    async_session.add_all([real_alert, test_alert, real_fc, test_fc])
    await async_session.commit()

    # Default: só os reais.
    ctx_default = await context_builder.build_context(
        async_session, scope="all", period_days=30, forecast_days=7
    )
    alert_titles = {a.title for a in ctx_default.active_alerts}
    fc_precips = {f.max_precipitation_mm for f in ctx_default.forecasts}
    assert "Real" in alert_titles
    assert "[TESTE] Sintético" not in alert_titles
    assert 200.0 in fc_precips
    assert 300.0 not in fc_precips

    # include_test=True: inclui os 4.
    ctx_with_test = await context_builder.build_context(
        async_session,
        scope="all",
        period_days=30,
        forecast_days=7,
        include_test=True,
    )
    alert_titles_t = {a.title for a in ctx_with_test.active_alerts}
    fc_precips_t = {f.max_precipitation_mm for f in ctx_with_test.forecasts}
    assert "[TESTE] Sintético" in alert_titles_t
    assert 300.0 in fc_precips_t


@pytest.mark.asyncio
async def test_purge_deletes_only_old_test_records(
    api_client, async_session, sample_dam
):
    """DELETE /test-harness/data?older_than_days=7 só apaga is_test=True antigos.

    Real alerts antigos preservam, test alerts novos preservam, só test alerts
    com created_at < cutoff são deletados.
    """
    # Setup:
    #  - Real alert antigo (não deve ser tocado)
    #  - Test alert antigo (deve sumir)
    #  - Test alert novo (deve preservar)
    old_real = make_alert(
        dam_id=sample_dam.id, title="Real antigo", is_test=False
    )
    old_test = make_alert(
        dam_id=sample_dam.id, title="[TESTE] Antigo", is_test=True
    )
    new_test = make_alert(
        dam_id=sample_dam.id, title="[TESTE] Novo", is_test=True
    )
    async_session.add_all([old_real, old_test, new_test])
    await async_session.commit()

    # Forçamos created_at retroativo nos antigos — server_default já populou
    # com now(); aqui sobrescrevemos pra simular registros de 10 dias atrás.
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=10)
    old_real.created_at = cutoff
    old_test.created_at = cutoff
    await async_session.commit()

    resp = await api_client.delete("/api/v1/test-harness/data?older_than_days=7")
    assert resp.status_code == 200
    body = resp.json()
    assert body["alerts_deleted"] == 1  # só old_test
    assert body["forecasts_deleted"] == 0

    # O endpoint usou synchronize_session=False, então a session ainda guarda
    # os 3 objetos no identity map. Consultar via SELECT explícito ignora o
    # cache e bate no banco — confirma o DELETE de fato persistiu.
    surviving_ids = set(
        (await async_session.execute(select(Alert.id))).scalars().all()
    )
    assert old_real.id in surviving_ids
    assert new_test.id in surviving_ids
    assert old_test.id not in surviving_ids
