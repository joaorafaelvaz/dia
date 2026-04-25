"""Smoke da camada HTTP: auth + filtros + ack de alerta.

Foco em contratos da API que clientes externos (ou o próprio dashboard)
dependem. Não testamos cada query — só os caminhos que mais importam:

- 401 sem auth e com auth ruim (`require_basic_auth` é a única defesa)
- Filtros listados na spec: `owner_group` em /dams, `severity_min` em /events
- POST /alerts/{id}/acknowledge muda estado (acknowledged + is_active=false)
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.conftest import make_alert, make_client, make_dam, make_event


@pytest.mark.asyncio
async def test_no_credentials_returns_401(anon_api_client):
    """Sem header Authorization → 401 (não 403, não 500)."""
    resp = await anon_api_client.get("/api/v1/dams")
    assert resp.status_code == 401
    # WWW-Authenticate é exigido por RFC 7235 quando responde 401
    assert "www-authenticate" in {h.lower() for h in resp.headers}


@pytest.mark.asyncio
async def test_bad_credentials_returns_401(anon_api_client):
    """Auth presente mas com senha errada → 401, não 403."""
    from httpx import BasicAuth

    resp = await anon_api_client.get(
        "/api/v1/dams", auth=BasicAuth("testuser", "wrong-password")
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_dams_filters_by_client_id(api_client, async_session):
    """`?client_id=N` retorna só barragens daquele cliente.

    Migration 0004 trocou o filtro string `owner_group` por FK `client_id`.
    Cobre regressão de quem ainda passa o param antigo: agora deve usar
    /api/v1/clients pra resolver o id e filtrar dams.
    """
    gerdau = make_client(name="Gerdau")
    kinross = make_client(name="Kinross")
    async_session.add_all([gerdau, kinross])
    await async_session.commit()
    await async_session.refresh(gerdau)
    await async_session.refresh(kinross)

    gerdau_dam = make_dam(name="Alemães", client_id=gerdau.id)
    kinross_dam = make_dam(name="Santo Antônio", client_id=kinross.id)
    async_session.add_all([gerdau_dam, kinross_dam])
    await async_session.commit()

    resp = await api_client.get("/api/v1/dams", params={"client_id": gerdau.id})
    assert resp.status_code == 200
    payload = resp.json()
    assert len(payload) == 1
    assert payload[0]["name"] == "Alemães"
    assert payload[0]["client_id"] == gerdau.id
    assert payload[0]["client_name"] == "Gerdau"


@pytest.mark.asyncio
async def test_list_events_filters_by_severity_min(api_client, async_session, sample_dam):
    """`?severity_min=3` esconde eventos severity 1-2.

    Dashboard usa esse filtro pra mostrar só "Alto+" — se quebrar, time
    operacional vê ruído de eventos triviais.
    """
    today = date.today()
    low = make_event(
        dam_id=sample_dam.id,
        event_date=today - timedelta(days=2),
        severity=2,
        severity_label="Moderado",
        title="Chuva moderada",
    )
    high = make_event(
        dam_id=sample_dam.id,
        event_date=today - timedelta(days=1),
        severity=4,
        severity_label="Muito Alto",
        title="Chuva muito alta",
    )
    async_session.add_all([low, high])
    await async_session.commit()

    resp = await api_client.get("/api/v1/events", params={"severity_min": 3})
    assert resp.status_code == 200
    payload = resp.json()
    titles = [e["title"] for e in payload]
    assert "Chuva muito alta" in titles
    assert "Chuva moderada" not in titles


@pytest.mark.asyncio
async def test_acknowledge_alert_marks_inactive_and_records_user(
    api_client, async_session, sample_dam
):
    """POST /alerts/{id}/acknowledge muda is_active→false, acknowledged→true,
    grava acknowledged_at e acknowledged_by (vem do AlertAcknowledge.acknowledged_by
    ou cai pro user autenticado).
    """
    alert = make_alert(
        dam_id=sample_dam.id,
        title="Alerta crítico",
        message="Risco iminente",
        is_active=True,
    )
    async_session.add(alert)
    await async_session.commit()
    await async_session.refresh(alert)

    resp = await api_client.post(
        f"/api/v1/alerts/{alert.id}/acknowledge",
        json={"acknowledged_by": "operador-noite"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["acknowledged"] is True
    assert payload["acknowledged_by"] == "operador-noite"
    assert payload["acknowledged_at"] is not None
    assert payload["is_active"] is False

    # E o GET /alerts (default is_active=true) já não deve retornar este
    list_resp = await api_client.get("/api/v1/alerts")
    assert list_resp.status_code == 200
    assert all(a["id"] != alert.id for a in list_resp.json())
