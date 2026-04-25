"""Smoke das 5 melhorias de robustez (#1, #2, #3, #5, #6).

#1 (datetime.utcnow → now(tz=UTC)) cobertos implicitamente — a suite agora
roda sem DeprecationWarning. Não precisa test dedicado.

#2: tasks endpoint allowlist
#3: Dam.owner_group tolera DetachedInstanceError
#5: error handler global retorna 500 sanitizado
#6: audit_log persiste entries em mutações de client/dam/alert
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.dam import Dam

from tests.conftest import make_alert, make_dam


# ---------------------------------------------------------------------------
# #2 — Tasks allowlist
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_task_returns_404_with_allowlist_in_detail(api_client):
    """POST /tasks/run/foo retorna 404 + lista as tasks permitidas no detail."""
    resp = await api_client.post("/api/v1/tasks/run/some_random_task_name")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "fetch_all_climate_data" in detail
    assert "expire_stale_alerts" in detail
    assert "dispatch_pending_notifications" in detail


# ---------------------------------------------------------------------------
# #3 — Dam.owner_group tolerante a session detached
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dam_owner_group_property_is_safe_when_client_not_loaded(
    async_engine, async_session
):
    """Dam carregada em uma session, lida em outra → property não crasha.

    Cobre o caso onde callsite passa Dam pra função/render sem JOIN explícito
    e a session original já fechou.
    """
    # Cria client e dam na primeira session
    client = Client(name="Detach Test")
    async_session.add(client)
    await async_session.commit()
    await async_session.refresh(client)

    dam = make_dam(name="Detached", client_id=client.id)
    async_session.add(dam)
    await async_session.commit()
    await async_session.refresh(dam)
    dam_id = dam.id

    # Abre uma session fresca, carrega dam SEM eager-load do client.
    factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    async with factory() as fresh_session:
        # noload — força a relationship a ficar como Lazy non-loaded
        from sqlalchemy.orm import noload
        stmt = select(Dam).options(noload(Dam.client)).where(Dam.id == dam_id)
        fresh_dam = (await fresh_session.execute(stmt)).scalar_one()

    # Fora da session — qualquer lazy-load tentativa daria DetachedInstanceError.
    # Property deve devolver "" / None em vez de crashar.
    assert fresh_dam.owner_group == ""
    assert fresh_dam.client_name is None


# ---------------------------------------------------------------------------
# #5 — Error handler global
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unhandled_exception_returns_sanitized_500(async_session, monkeypatch):
    """Endpoint que levanta exception qualquer → 500 + payload limpo.

    Garantia: stacktrace NÃO vai pro cliente; só vira log estruturado
    (capturado pelo handler). Cliente recebe error_type pra debug rápido.

    Cliente customizado com `raise_app_exceptions=False`: o ASGITransport
    default re-levanta exceptions mesmo depois do handler emitir resposta —
    em produção (uvicorn) o handler tem a palavra final.
    """
    from httpx import ASGITransport, AsyncClient, BasicAuth

    from app.config import settings
    from app.database import get_session
    from app.main import app
    from tests.conftest import TEST_PASS, TEST_USER

    @app.get("/_test/boom")
    async def _boom():
        raise RuntimeError("intentional test boom — should be caught by handler")

    monkeypatch.setattr(settings, "basic_auth_user", TEST_USER)
    monkeypatch.setattr(settings, "basic_auth_pass", TEST_PASS)
    async def _override_get_session():
        yield async_session
    app.dependency_overrides[get_session] = _override_get_session

    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(
            transport=transport, base_url="http://test",
            auth=BasicAuth(TEST_USER, TEST_PASS),
        ) as client:
            resp = await client.get("/_test/boom")
            assert resp.status_code == 500
            body = resp.json()
            assert body["detail"] == "Internal server error"
            assert body["error_type"] == "RuntimeError"
            # NÃO deve vazar mensagem da exception (só vai pro log)
            assert "intentional" not in str(body)
    finally:
        app.dependency_overrides.pop(get_session, None)
        # Limpa rota stub pra não poluir outras suítes
        app.router.routes = [
            r for r in app.router.routes
            if getattr(r, "path", None) != "/_test/boom"
        ]


# ---------------------------------------------------------------------------
# #6 — Audit log
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_client_records_audit_entry(api_client, async_session):
    """POST /clients deixa rastro em audit_log com action='client.create'."""
    resp = await api_client.post(
        "/api/v1/clients", json={"name": "Audit Test Co"}
    )
    assert resp.status_code == 201
    client_id = resp.json()["id"]

    # Audit entry persistido na mesma session SQLite (in-memory) usada pelo client
    entries = (
        await async_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "client", AuditLog.entity_id == client_id
            )
        )
    ).scalars().all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == "client.create"
    assert entry.user == "testuser"  # vem do BasicAuth no api_client
    assert entry.details.get("name") == "Audit Test Co"


@pytest.mark.asyncio
async def test_dam_deactivate_records_distinct_audit_action(
    api_client, async_session, sample_dam
):
    """PATCH com is_active=false → action='dam.deactivate' (não 'dam.update').

    Distinção semântica: facilita busca futura "quem desativou X" sem ter
    que parsear details JSON.
    """
    resp = await api_client.patch(
        f"/api/v1/dams/{sample_dam.id}", json={"is_active": False}
    )
    assert resp.status_code == 200

    entries = (
        await async_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "dam",
                AuditLog.entity_id == sample_dam.id,
                AuditLog.action == "dam.deactivate",
            )
        )
    ).scalars().all()
    assert len(entries) == 1
    assert entries[0].details["changes"] == {"is_active": False}


@pytest.mark.asyncio
async def test_alert_acknowledge_records_audit(
    api_client, async_session, sample_dam
):
    """POST /alerts/{id}/acknowledge gera audit com severity nos details."""
    alert = make_alert(dam_id=sample_dam.id, severity=4, title="Crítico audit")
    async_session.add(alert)
    await async_session.commit()
    await async_session.refresh(alert)

    resp = await api_client.post(
        f"/api/v1/alerts/{alert.id}/acknowledge",
        json={"acknowledged_by": "operador-noite"},
    )
    assert resp.status_code == 200

    entries = (
        await async_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type == "alert",
                AuditLog.entity_id == alert.id,
            )
        )
    ).scalars().all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == "alert.acknowledge"
    assert entry.details["acknowledged_by"] == "operador-noite"
    assert entry.details["severity"] == 4
