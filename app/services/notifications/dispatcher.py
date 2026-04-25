"""Dispatcher de notificações: aplica rate-limit + severity gating, chama canais.

**Por quê este módulo:** as funções `send_alert_whatsapp` / `send_alert_email`
em si são burras — só fazem o POST/SMTP. Toda a política (qual severity vai
pra qual canal, quanto tempo entre dois alertas da mesma barragem, marcar
flags `notified_*` no banco) fica concentrada aqui pra ser fácil de testar
sem mockar HTTP/SMTP.

**Regras de policy** (espelhadas em `settings`):

- **Rate limit por (dam_id, alert_type):** janela de 6h. Quando uma chuva
  forte cria múltiplos `forecast_warning` em sequência (ex.: rodadas a cada
  3h elevam o nível esperado), o operador recebe **um** WhatsApp, não cinco.
  Implementado via Redis SETNX com TTL — sobrevive a restart do worker.
- **Severity gating:** WhatsApp em ≥3 (Alto/Muito Alto/Crítico); email só
  em ≥4 (Muito Alto/Crítico). Caixa de gestor não pode virar barulho.
- **Marcação de canal:** `notified_whatsapp=True` / `notified_email=True`
  só são gravados quando o canal **realmente** confirma sucesso. Se o n8n
  estiver fora ou o SMTP recusar, a flag fica `False` e a próxima passada
  do sweep tenta de novo. Idempotência é via rate-limit, não via flag.

**Trade-off intencional:** o rate limit é por canal+alert_type, não por
alerta individual. Isso significa que dois alertas distintos (mesma dam,
mesmo `alert_type`) colapsam em uma notificação dentro da janela. A
alternativa — notificar cada um — gera barulho em estação chuvosa. Spec
§15 explicitamente prefere agregação.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.alert import Alert
from app.models.dam import Dam
from app.services.notifications import email as email_channel
from app.services.notifications import whatsapp as whatsapp_channel
from app.utils.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Redis helper — per event loop
# ---------------------------------------------------------------------------
#
# Mesma armadilha que `ana._get_redis`: Celery cria um event loop novo por
# task (`asyncio.run`), e um cliente Redis amarrado a um loop morto explode
# com "Event loop is closed" na próxima task. Mantemos o cache por
# `id(loop)` e podamos entradas de loops fechados na entrada.

_redis_by_loop: dict[int, tuple[asyncio.AbstractEventLoop, aioredis.Redis]] = {}


def _get_redis() -> aioredis.Redis:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as exc:
        raise RuntimeError("dispatcher._get_redis chamado fora de event loop") from exc

    stale = [lid for lid, (lp, _c) in _redis_by_loop.items() if lp.is_closed()]
    for lid in stale:
        _redis_by_loop.pop(lid, None)

    loop_id = id(loop)
    cached = _redis_by_loop.get(loop_id)
    if cached is not None:
        return cached[1]

    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    _redis_by_loop[loop_id] = (loop, client)
    return client


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------

_RATE_LIMIT_KEY_TMPL = "notif:rate:{channel}:{dam_id}:{alert_type}"


async def _claim_rate_limit_slot(channel: str, alert: Alert) -> bool:
    """Tenta reservar o slot de envio para (channel, dam, alert_type).

    Retorna True se foi a primeira tentativa dentro da janela (pode enviar);
    False se já existe uma reserva ativa (suprimir).

    Implementação: `SET NX EX` é atômico no Redis — se a chave já existe,
    falha sem sobrescrever. TTL é a janela em segundos.
    """
    key = _RATE_LIMIT_KEY_TMPL.format(
        channel=channel, dam_id=alert.dam_id, alert_type=alert.alert_type
    )
    ttl_seconds = max(60, int(settings.notification_rate_limit_hours * 3600))
    try:
        r = _get_redis()
        # set(nx=True, ex=ttl) → True se gravou (slot livre), None se não.
        ok = await r.set(key, str(alert.id), nx=True, ex=ttl_seconds)
        return bool(ok)
    except Exception as exc:
        # Falha de Redis não pode segurar notificação — log e libera. O risco
        # é mandar um WhatsApp duplicado num cenário de Redis caído; prefiro
        # isso a silenciar um alerta crítico por uma falha de cache.
        log.warning("notif_rate_limit_check_failed", channel=channel, alert_id=alert.id, error=str(exc))
        return True


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------


@dataclass
class DispatchResult:
    """Resumo do que foi efetivamente enviado para um único alerta."""

    alert_id: int
    whatsapp_sent: bool = False
    whatsapp_skipped_reason: str | None = None
    email_sent: bool = False
    email_skipped_reason: str | None = None


async def dispatch_alert(
    session: AsyncSession, alert: Alert, dam: Dam
) -> DispatchResult:
    """Despacha um alerta pelos canais configurados e marca flags em sucesso.

    Não dá commit — quem chama é responsável pelo `await session.commit()`.
    Isso permite que o sweep agrupe N alertas num único commit.
    """
    result = DispatchResult(alert_id=alert.id)

    if not settings.notifications_enabled:
        result.whatsapp_skipped_reason = "globally_disabled"
        result.email_skipped_reason = "globally_disabled"
        return result

    # --- WhatsApp ---
    if alert.notified_whatsapp:
        result.whatsapp_skipped_reason = "already_sent"
    elif alert.severity < settings.notification_min_severity_whatsapp:
        result.whatsapp_skipped_reason = "below_severity_threshold"
    elif not await _claim_rate_limit_slot("whatsapp", alert):
        result.whatsapp_skipped_reason = "rate_limited"
    else:
        ok = await whatsapp_channel.send_alert_whatsapp(alert, dam)
        if ok:
            alert.notified_whatsapp = True
            result.whatsapp_sent = True
        else:
            result.whatsapp_skipped_reason = "channel_failed"

    # --- Email ---
    if alert.notified_email:
        result.email_skipped_reason = "already_sent"
    elif alert.severity < settings.notification_min_severity_email:
        result.email_skipped_reason = "below_severity_threshold"
    elif not await _claim_rate_limit_slot("email", alert):
        result.email_skipped_reason = "rate_limited"
    else:
        ok = await email_channel.send_alert_email(alert, dam)
        if ok:
            alert.notified_email = True
            result.email_sent = True
        else:
            result.email_skipped_reason = "channel_failed"

    log.info(
        "alert_dispatched",
        alert_id=alert.id,
        dam_id=dam.id,
        severity=alert.severity,
        whatsapp_sent=result.whatsapp_sent,
        whatsapp_reason=result.whatsapp_skipped_reason,
        email_sent=result.email_sent,
        email_reason=result.email_skipped_reason,
    )
    return result


__all__ = ["DispatchResult", "dispatch_alert"]
