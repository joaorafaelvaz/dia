"""Smoke do sweep de expiração de alertas.

`_expire_alerts_async` em climate_tasks.py implementa 3 regras (qualquer uma
basta pra desativar `is_active=False`):

  1. expires_at NOT NULL AND expires_at < now()  — TTL explícito vencido
  2. acknowledged=True AND acknowledged_at < now() - 7d
  3. alert_type='forecast_warning' AND expires_at IS NULL AND
     forecast_date < today() - 2d

Cobertura: cada ramo isolado + alerta no limite (não deve expirar) +
idempotência (chamar duas vezes não muda nada).

Estratégia: monkeypatch `climate_tasks.task_session` pra reusar o
`async_session` do teste (engine SQLite in-memory). Sem isso a task abriria
uma engine asyncpg real e quebraria offline.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

import pytest

from app.tasks import climate_tasks

from tests.conftest import make_alert


@pytest.fixture
def patch_task_session(monkeypatch, async_session):
    """Faz `climate_tasks.task_session()` retornar a sessão de teste.

    O production code escreve `async with task_session() as session: ...`,
    então precisamos de um async CM. Yielda a mesma sessão sem fechá-la
    porque a fixture do teste é quem gerencia o ciclo de vida.
    """
    @asynccontextmanager
    async def fake_task_session():
        yield async_session

    monkeypatch.setattr(climate_tasks, "task_session", fake_task_session)


@pytest.mark.asyncio
async def test_expire_by_explicit_ttl(async_session, sample_dam, patch_task_session):
    """Regra 1: alerta com expires_at vencido é desativado."""
    expired = make_alert(
        dam_id=sample_dam.id,
        alert_type="threshold_exceeded",
        expires_at=datetime.now(tz=timezone.utc) - timedelta(hours=1),
    )
    not_expired = make_alert(
        dam_id=sample_dam.id,
        alert_type="threshold_exceeded",
        expires_at=datetime.now(tz=timezone.utc) + timedelta(hours=1),
    )
    async_session.add_all([expired, not_expired])
    await async_session.commit()

    result = await climate_tasks._expire_alerts_async()

    assert result == {"expired": 1}
    await async_session.refresh(expired)
    await async_session.refresh(not_expired)
    assert expired.is_active is False
    assert not_expired.is_active is True


@pytest.mark.asyncio
async def test_expire_acknowledged_after_seven_days(
    async_session, sample_dam, patch_task_session
):
    """Regra 2: ack feito há mais de 7d desativa; ack recente preserva."""
    old_ack = make_alert(
        dam_id=sample_dam.id,
        acknowledged=True,
        acknowledged_at=datetime.now(tz=timezone.utc) - timedelta(days=10),
    )
    fresh_ack = make_alert(
        dam_id=sample_dam.id,
        acknowledged=True,
        acknowledged_at=datetime.now(tz=timezone.utc) - timedelta(days=3),
    )
    async_session.add_all([old_ack, fresh_ack])
    await async_session.commit()

    result = await climate_tasks._expire_alerts_async()

    assert result == {"expired": 1}
    await async_session.refresh(old_ack)
    await async_session.refresh(fresh_ack)
    assert old_ack.is_active is False
    assert fresh_ack.is_active is True


@pytest.mark.asyncio
async def test_expire_forecast_warning_past_date_without_ttl(
    async_session, sample_dam, patch_task_session
):
    """Regra 3: forecast_warning com forecast_date no passado e sem expires_at.

    Defesa em profundidade — o aggregator já popula expires_at hoje, mas
    se algum forecast antigo escapou ou se o code de geração regredir,
    esse ramo desativa pra não acumular alertas zumbi.
    """
    stale = make_alert(
        dam_id=sample_dam.id,
        alert_type="forecast_warning",
        forecast_date=date.today() - timedelta(days=5),
        expires_at=None,
    )
    # forecast_date no passado mas com expires_at no futuro — regra 3 NÃO
    # se aplica (regra 1 também não, porque expires_at é futuro). Preserva.
    has_ttl = make_alert(
        dam_id=sample_dam.id,
        alert_type="forecast_warning",
        forecast_date=date.today() - timedelta(days=5),
        expires_at=datetime.now(tz=timezone.utc) + timedelta(hours=2),
    )
    # forecast_date dentro da janela (hoje-1) — preserva
    recent = make_alert(
        dam_id=sample_dam.id,
        alert_type="forecast_warning",
        forecast_date=date.today() - timedelta(days=1),
        expires_at=None,
    )
    async_session.add_all([stale, has_ttl, recent])
    await async_session.commit()

    result = await climate_tasks._expire_alerts_async()

    assert result == {"expired": 1}
    await async_session.refresh(stale)
    await async_session.refresh(has_ttl)
    await async_session.refresh(recent)
    assert stale.is_active is False
    assert has_ttl.is_active is True
    assert recent.is_active is True


@pytest.mark.asyncio
async def test_sweep_is_idempotent(async_session, sample_dam, patch_task_session):
    """Segunda chamada não muda nada — só atua em is_active=True.

    Cobre regressão clássica: se o filtro `is_active=True` sumir do WHERE
    da segunda passada, contadores ficam errados e o log mente.
    """
    expired = make_alert(
        dam_id=sample_dam.id,
        alert_type="threshold_exceeded",
        expires_at=datetime.now(tz=timezone.utc) - timedelta(hours=1),
    )
    async_session.add(expired)
    await async_session.commit()

    first = await climate_tasks._expire_alerts_async()
    second = await climate_tasks._expire_alerts_async()

    assert first == {"expired": 1}
    assert second == {"expired": 0}


@pytest.mark.asyncio
async def test_sweep_noop_when_nothing_to_expire(
    async_session, sample_dam, patch_task_session
):
    """Banco com só alertas válidos — sweep retorna 0 sem erro."""
    healthy = make_alert(
        dam_id=sample_dam.id,
        alert_type="threshold_exceeded",
        expires_at=datetime.now(tz=timezone.utc) + timedelta(days=1),
    )
    async_session.add(healthy)
    await async_session.commit()

    result = await climate_tasks._expire_alerts_async()

    assert result == {"expired": 0}
    await async_session.refresh(healthy)
    assert healthy.is_active is True
