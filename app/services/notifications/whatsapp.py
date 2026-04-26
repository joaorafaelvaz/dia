"""WhatsApp notifications via n8n webhook.

Fase 1: stubbed — returns False unless NOTIFICATIONS_ENABLED=true AND the n8n
flow is provisioned (see docs/n8n-flows/dam-alerts.json, delivered in Fase 4).
"""
from __future__ import annotations

import httpx

from app.config import settings
from app.models.alert import Alert
from app.models.dam import Dam
from app.utils.logging import get_logger

log = get_logger(__name__)

REQUEST_TIMEOUT = 15.0


def _format_message(alert: Alert, dam: Dam) -> str:
    return (
        f"🚨 *DIA — Alerta {alert.severity}/5*\n"
        f"*{dam.name}* ({dam.municipality}/{dam.state})\n"
        f"Proprietário: {dam.owner_group}\n\n"
        f"{alert.title}\n{alert.message}"
    )


async def send_alert_whatsapp(alert: Alert, dam: Dam, *, force: bool = False) -> bool:
    """POST alert payload to n8n webhook. Returns True on success.

    Feature-flagged: if notifications_enabled is False OR webhook not configured,
    logs intent and returns False without making a request.

    `force=True` bypassa o check de `notifications_enabled` — usado pelo
    test harness pra validar integração com n8n+WAHA antes de o operador
    ativar notif global. URL/token ainda são obrigatórios.
    """
    if not force and not settings.notifications_enabled:
        log.debug("whatsapp_disabled", alert_id=alert.id, dam_id=dam.id)
        return False

    if not settings.n8n_webhook_url:
        log.warning("whatsapp_webhook_not_configured", alert_id=alert.id)
        return False

    payload = {
        "alert_id": alert.id,
        "severity": alert.severity,
        # whatsapp_to vai junto pra o flow n8n não precisar ler env var
        # (n8n bloqueia $env por default). Operador configura DIA_WHATSAPP_TO
        # no .env do DIA, daí pro payload, daí pro WAHA.
        "whatsapp_to": settings.dia_whatsapp_to,
        "dam": {
            "id": dam.id,
            "name": dam.name,
            "owner_group": dam.owner_group,
            "municipality": dam.municipality,
            "state": dam.state,
        },
        "title": alert.title,
        "message": _format_message(alert, dam),
        "forecast_date": alert.forecast_date.isoformat() if alert.forecast_date else None,
    }
    headers = {}
    if settings.n8n_webhook_token:
        headers["Authorization"] = f"Bearer {settings.n8n_webhook_token}"

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(
                settings.n8n_webhook_url, json=payload, headers=headers
            )
            resp.raise_for_status()
        log.info("whatsapp_sent", alert_id=alert.id, dam_id=dam.id)
        return True
    except httpx.HTTPError as exc:
        log.error("whatsapp_failed", alert_id=alert.id, error=str(exc))
        return False
