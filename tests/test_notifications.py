"""Smoke do dispatcher de notificações.

Cobre as 4 regras de policy do dispatcher:

1. Severity gating: ≥3 → WhatsApp, ≥4 → email
2. `notifications_enabled=False` → no-op total (modo padrão de produção
   até o flow n8n ser validado)
3. Rate limit por (dam, alert_type) suprime envio repetido
4. Falha de canal mantém `notified_*=False` — a próxima passada do sweep
   re-tenta sem precisar de retry-loop manual

Dependências externas mockadas:
- `whatsapp.send_alert_whatsapp` e `email.send_alert_email` (canal real)
- `dispatcher._claim_rate_limit_slot` (Redis) — controlamos True/False
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.services.notifications import dispatcher

from tests.conftest import make_alert


@pytest.mark.asyncio
async def test_dispatch_noop_when_notifications_disabled(
    async_session, sample_dam, monkeypatch
):
    """`notifications_enabled=False` → nem WhatsApp nem email são chamados.

    Esse é o estado default em produção até o n8n flow ser provisionado;
    se essa guarda quebrar, podemos enviar lixo pro webhook errado.
    """
    monkeypatch.setattr(settings, "notifications_enabled", False)
    alert = make_alert(dam_id=sample_dam.id, severity=5, title="Crítico")
    async_session.add(alert)
    await async_session.commit()
    await async_session.refresh(alert)

    called: list[str] = []

    async def boom_wpp(*_a, **_k):
        called.append("wpp")
        return True

    async def boom_email(*_a, **_k):
        called.append("email")
        return True

    monkeypatch.setattr(dispatcher.whatsapp_channel, "send_alert_whatsapp", boom_wpp)
    monkeypatch.setattr(dispatcher.email_channel, "send_alert_email", boom_email)

    result = await dispatcher.dispatch_alert(async_session, alert, sample_dam)

    assert called == []
    assert result.whatsapp_sent is False
    assert result.email_sent is False
    assert result.whatsapp_skipped_reason == "globally_disabled"
    assert result.email_skipped_reason == "globally_disabled"
    assert alert.notified_whatsapp is False
    assert alert.notified_email is False


@pytest.mark.asyncio
async def test_dispatch_severity_gating(async_session, sample_dam, monkeypatch):
    """Severity 3 → só WhatsApp. Severity 4 → ambos.

    Defaults: whatsapp_min=3, email_min=4. Se invertermos por engano, gestor
    de risco recebe email de chuvinha moderada — esse teste pega.
    """
    monkeypatch.setattr(settings, "notifications_enabled", True)
    # Bypass rate limit pra isolar a regra de severity.
    async def always_free(*_a, **_k):
        return True
    monkeypatch.setattr(dispatcher, "_claim_rate_limit_slot", always_free)

    wpp_calls: list[int] = []
    email_calls: list[int] = []

    async def fake_wpp(alert, _dam):
        wpp_calls.append(alert.id)
        return True

    async def fake_email(alert, _dam):
        email_calls.append(alert.id)
        return True

    monkeypatch.setattr(dispatcher.whatsapp_channel, "send_alert_whatsapp", fake_wpp)
    monkeypatch.setattr(dispatcher.email_channel, "send_alert_email", fake_email)

    sev3 = make_alert(dam_id=sample_dam.id, severity=3, title="Alto", alert_type="forecast_warning")
    sev4 = make_alert(dam_id=sample_dam.id, severity=4, title="Muito Alto", alert_type="threshold_exceeded")
    async_session.add_all([sev3, sev4])
    await async_session.commit()
    await async_session.refresh(sev3)
    await async_session.refresh(sev4)

    r3 = await dispatcher.dispatch_alert(async_session, sev3, sample_dam)
    r4 = await dispatcher.dispatch_alert(async_session, sev4, sample_dam)

    # sev3: whatsapp sim, email não (skipped por severity)
    assert r3.whatsapp_sent is True
    assert r3.email_sent is False
    assert r3.email_skipped_reason == "below_severity_threshold"
    assert sev3.notified_whatsapp is True
    assert sev3.notified_email is False

    # sev4: ambos
    assert r4.whatsapp_sent is True
    assert r4.email_sent is True
    assert sev4.notified_whatsapp is True
    assert sev4.notified_email is True

    assert wpp_calls == [sev3.id, sev4.id]
    assert email_calls == [sev4.id]


@pytest.mark.asyncio
async def test_dispatch_respects_rate_limit(async_session, sample_dam, monkeypatch):
    """Quando o slot do rate-limit está ocupado, nenhum canal é chamado.

    Cobre o caso real de produção: forecast_warning re-disparado a cada
    3h por evento prolongado — só o primeiro deve notificar.
    """
    monkeypatch.setattr(settings, "notifications_enabled", True)

    # Simula slot ocupado (Redis SETNX falhou — chave já existia).
    async def always_blocked(*_a, **_k):
        return False
    monkeypatch.setattr(dispatcher, "_claim_rate_limit_slot", always_blocked)

    wpp_calls: list[int] = []

    async def fake_wpp(alert, _dam):
        wpp_calls.append(alert.id)
        return True

    monkeypatch.setattr(dispatcher.whatsapp_channel, "send_alert_whatsapp", fake_wpp)

    alert = make_alert(dam_id=sample_dam.id, severity=4, title="Muito Alto")
    async_session.add(alert)
    await async_session.commit()
    await async_session.refresh(alert)

    result = await dispatcher.dispatch_alert(async_session, alert, sample_dam)

    assert wpp_calls == []
    assert result.whatsapp_sent is False
    assert result.whatsapp_skipped_reason == "rate_limited"
    assert alert.notified_whatsapp is False  # flag NÃO marcada — preserva re-tentativa


@pytest.mark.asyncio
async def test_channel_failure_keeps_flag_false_for_retry(
    async_session, sample_dam, monkeypatch
):
    """Se o canal falha (n8n fora, SMTP recusou), `notified_*` fica False.

    Idempotência verdadeira: o sweep do próximo tick vai re-tentar. Se
    marcássemos True em falha, o alerta crítico sumiria silenciosamente.
    """
    monkeypatch.setattr(settings, "notifications_enabled", True)

    async def always_free(*_a, **_k):
        return True
    monkeypatch.setattr(dispatcher, "_claim_rate_limit_slot", always_free)

    async def failing_wpp(_alert, _dam):
        return False  # canal sinaliza falha (sem exception)

    monkeypatch.setattr(dispatcher.whatsapp_channel, "send_alert_whatsapp", failing_wpp)

    alert = make_alert(dam_id=sample_dam.id, severity=3, title="Alto")
    async_session.add(alert)
    await async_session.commit()
    await async_session.refresh(alert)

    result = await dispatcher.dispatch_alert(async_session, alert, sample_dam)

    assert result.whatsapp_sent is False
    assert result.whatsapp_skipped_reason == "channel_failed"
    assert alert.notified_whatsapp is False  # FLAG MANTIDA FALSE — chave do retry
