"""SMTP email notifications for critical alerts.

Fase 1: stubbed — returns False unless NOTIFICATIONS_ENABLED=true AND SMTP
credentials are configured. Real template + retry behavior lands in Fase 4.
"""
from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib

from app.config import settings
from app.models.alert import Alert
from app.models.dam import Dam
from app.utils.logging import get_logger

log = get_logger(__name__)


def _build_email(alert: Alert, dam: Dam) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = settings.alert_email_to
    msg["Subject"] = (
        f"[DIA] Alerta {alert.severity}/5 — {dam.name} ({dam.municipality}/{dam.state})"
    )
    body = (
        f"Alerta gerado pelo DIA (Dam Intelligence Agent).\n\n"
        f"Barragem: {dam.name}\n"
        f"Proprietário: {dam.owner_group}\n"
        f"Município: {dam.municipality}/{dam.state}\n"
        f"Severidade: {alert.severity}/5\n"
        f"Tipo: {alert.alert_type}\n"
        f"Data prevista: {alert.forecast_date.isoformat() if alert.forecast_date else '-'}\n\n"
        f"{alert.title}\n\n{alert.message}\n"
    )
    msg.set_content(body)
    return msg


async def send_alert_email(alert: Alert, dam: Dam) -> bool:
    """Send SMTP email. Returns True on success.

    Feature-flagged: no-op unless notifications_enabled and SMTP fully configured.
    """
    if not settings.notifications_enabled:
        log.debug("email_disabled", alert_id=alert.id, dam_id=dam.id)
        return False

    if not (settings.smtp_host and settings.smtp_user and settings.alert_email_to):
        log.warning("email_not_configured", alert_id=alert.id)
        return False

    msg = _build_email(alert, dam)
    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_pass,
            start_tls=True,
        )
        log.info("email_sent", alert_id=alert.id, dam_id=dam.id)
        return True
    except Exception as exc:  # aiosmtplib.SMTPException and friends
        log.error("email_failed", alert_id=alert.id, error=str(exc))
        return False
