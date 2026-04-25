"""Celery tasks: sweep de alertas pendentes de notificação.

**Por que separado em vez de notificar dentro de `check_and_create_alerts`:**

1. *Decoupling de transação:* o aggregator é DB-only. Se WhatsApp/SMTP
   estourar timeout (15s), travaria o ciclo de detecção. Sweep separado
   significa que a detecção termina rápido e a notificação tenta no próximo
   tick.
2. *Re-tentativa natural:* se o n8n estiver fora numa janela de 5min, a
   próxima passada do sweep pega o alerta com `notified_whatsapp=False` e
   tenta de novo. Sem retry-loop manual.
3. *Throttling:* o rate limit de 6h por (dam, alert_type) é **central** —
   um alerta novo dentro da janela vai ser visto pelo sweep mas o
   dispatcher recusa antes de chamar o canal. Visível no log.

Schedule: a cada 5 minutos via `settings.schedule_notifications`. Curto
para alerta crítico chegar rápido; o rate limit do dispatcher protege
contra tempestade.
"""
from __future__ import annotations

import asyncio

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import task_session
from app.models.alert import Alert
from app.services.notifications.dispatcher import dispatch_alert
from app.tasks.celery_app import celery_app
from app.utils.logging import get_logger

log = get_logger(__name__)


async def _dispatch_pending_async() -> dict[str, int]:
    """Encontra alertas ativos sem notificação completa e despacha cada um.

    Critério de "pendente": `is_active=True` E (severity ≥ wpp_min com
    `notified_whatsapp=False`) OU (severity ≥ email_min com
    `notified_email=False`).

    Em cada chamada:
      - Carrega o `Dam` junto via selectinload (precisamos dos campos
        nome/município/state pra montar a mensagem).
      - Despacha sequencialmente. Não usamos gather pra evitar 5 webhooks
        do n8n disputando o mesmo socket — o volume é baixo (na pior das
        hipóteses 15 dams × poucos alertas).
      - Commita uma vez no fim cobrindo todas as flags atualizadas.
    """
    if not settings.notifications_enabled:
        log.debug("notifications_disabled_skip_sweep")
        return {"scanned": 0, "whatsapp_sent": 0, "email_sent": 0}

    wpp_min = settings.notification_min_severity_whatsapp
    email_min = settings.notification_min_severity_email
    severity_floor = min(wpp_min, email_min)

    async with task_session() as session:
        stmt = (
            select(Alert)
            .options(selectinload(Alert.dam))
            .where(
                Alert.is_active.is_(True),
                Alert.severity >= severity_floor,
                # Otimização: pula alertas já notificados em ambos os canais
                # que importam pra esta severidade. SQLAlchemy traduz pra OR.
                # (A lógica fina fica no dispatcher.)
                (Alert.notified_whatsapp.is_(False))
                | (Alert.notified_email.is_(False)),
            )
            .order_by(Alert.severity.desc(), Alert.created_at.asc())
            .limit(200)  # ceiling defensivo; em operação normal <50
        )
        alerts = list((await session.execute(stmt)).scalars().all())

        whatsapp_sent = 0
        email_sent = 0
        for alert in alerts:
            try:
                result = await dispatch_alert(session, alert, alert.dam)
                whatsapp_sent += int(result.whatsapp_sent)
                email_sent += int(result.email_sent)
            except Exception as exc:
                # Defensivo: uma falha num alerta não pode parar o sweep.
                log.error("dispatch_alert_failed", alert_id=alert.id, error=str(exc))

        if whatsapp_sent or email_sent:
            await session.commit()

        log.info(
            "notifications_sweep_complete",
            scanned=len(alerts),
            whatsapp_sent=whatsapp_sent,
            email_sent=email_sent,
        )
        return {
            "scanned": len(alerts),
            "whatsapp_sent": whatsapp_sent,
            "email_sent": email_sent,
        }


@celery_app.task(
    name="app.tasks.notification_tasks.dispatch_pending_notifications",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def dispatch_pending_notifications(self) -> dict[str, int]:
    """Beat-driven sweep: notifica alertas pendentes (WhatsApp + email)."""
    try:
        return asyncio.run(_dispatch_pending_async())
    except SoftTimeLimitExceeded:
        log.warning("notifications_sweep_soft_timeout")
        raise
    except Exception as exc:
        log.error("notifications_sweep_failed", error=str(exc))
        raise self.retry(exc=exc) from exc


__all__ = ["dispatch_pending_notifications"]
