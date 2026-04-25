"""Smoke do fluxo Clients + criação de Dam via UI/API.

Cobre o ciclo completo do refactor 0004 (FK Client em Dam):
1. POST /clients cria + GET /clients lista com dam_count
2. POST /dams com client_id válido cria E dispara fetch_climate_data_for_dam
3. POST /dams com client_id inexistente → 404
4. DELETE /clients/{id} com dams associadas → 409 (operador desativa em vez)
5. context_builder.resolve_dam_ids agora resolve scope via Client.name
"""
from __future__ import annotations

import pytest

from app.api.v1 import dams as dams_router
from app.services.ai import context_builder

from tests.conftest import make_client, make_dam


@pytest.mark.asyncio
async def test_create_and_list_clients(api_client, async_session):
    """POST /clients persiste e GET /clients retorna com dam_count."""
    payload = {
        "name": "Vale",
        "contact_name": "Operações Risco",
        "contact_email": "ops@vale.example",
        "contact_phone": "+55 31 0000-0000",
    }
    resp = await api_client.post("/api/v1/clients", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Vale"
    assert body["dam_count"] == 0

    list_resp = await api_client.get("/api/v1/clients")
    assert list_resp.status_code == 200
    names = [c["name"] for c in list_resp.json()]
    assert "Vale" in names


@pytest.mark.asyncio
async def test_create_dam_dispatches_climate_task(
    api_client, async_session, sample_client, monkeypatch
):
    """POST /dams cria dam ATÉ a coleta automática ser disparada.

    Stub `.delay()` pra capturar args sem precisar de Celery+Redis.
    """
    captured: list[int] = []

    class FakeTask:
        @staticmethod
        def delay(dam_id: int) -> None:
            captured.append(dam_id)

    # Patch climate_tasks.fetch_climate_data_for_dam dentro do módulo importado
    # pelo endpoint. O endpoint faz import lazy (`from app.tasks import
    # climate_tasks`) — por isso patch direto no módulo.
    from app.tasks import climate_tasks
    monkeypatch.setattr(climate_tasks, "fetch_climate_data_for_dam", FakeTask)

    payload = {
        "name": "Barragem Smoke",
        "client_id": sample_client.id,
        "dam_type": "tailings",
        "municipality": "Ouro Preto",
        "state": "MG",
        "latitude": -20.4,
        "longitude": -43.6,
        "dpa": "Alto",
    }
    resp = await api_client.post("/api/v1/dams", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["client_id"] == sample_client.id
    assert body["client_name"] == sample_client.name
    assert len(captured) == 1
    assert captured[0] == body["id"]


@pytest.mark.asyncio
async def test_create_dam_with_unknown_client_returns_404(
    api_client, async_session
):
    """POST /dams com client_id que não existe → 404 (não 422).

    Cobre o operador que copiou um id velho de outro ambiente.
    """
    payload = {
        "name": "Barragem órfã",
        "client_id": 9999,
        "dam_type": "tailings",
        "municipality": "Ouro Preto",
        "state": "MG",
        "latitude": -20.4,
        "longitude": -43.6,
    }
    resp = await api_client.post("/api/v1/dams", json=payload)
    assert resp.status_code == 404
    assert "9999" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_delete_client_with_dams_returns_409(
    api_client, async_session, sample_client
):
    """DELETE /clients/{id} com dams associadas → 409 com hint pra desativar.

    Política: operador apaga dams primeiro ou usa PATCH is_active=false.
    """
    dam = make_dam(name="presa associada", client_id=sample_client.id)
    async_session.add(dam)
    await async_session.commit()

    resp = await api_client.delete(f"/api/v1/clients/{sample_client.id}")
    assert resp.status_code == 409
    detail = resp.json()["detail"].lower()
    assert "barragem" in detail
    assert "is_active=false" in detail


@pytest.mark.asyncio
async def test_resolve_dam_ids_scope_now_matches_client_name(async_session):
    """Regressão do refactor: scope passa por Client.name (case-insensitive).

    Garante que a query `JOIN clients ON Client.name ilike scope` continua
    pegando dams ativas mesmo após a mudança de owner_group string → FK.
    """
    gerdau = make_client(name="Gerdau")
    kinross = make_client(name="Kinross")
    async_session.add_all([gerdau, kinross])
    await async_session.commit()
    await async_session.refresh(gerdau)
    await async_session.refresh(kinross)

    g = make_dam(name="G", client_id=gerdau.id, is_active=True)
    k = make_dam(name="K", client_id=kinross.id, is_active=True)
    async_session.add_all([g, k])
    await async_session.commit()

    g_ids = await context_builder.resolve_dam_ids(async_session, scope="GERDAU")
    assert g.id in g_ids and k.id not in g_ids
