"""Helper pra registrar audit_log a partir de endpoints de mutação.

Padrão de uso:

    from app.utils.audit import record_audit
    ...
    @router.post(...)
    async def create_dam(payload, session, user: AuthUser):
        dam = Dam(...)
        session.add(dam)
        await session.commit()
        await record_audit(
            session, user=user, action="dam.create",
            entity_type="dam", entity_id=dam.id,
            details={"name": dam.name, "client_id": dam.client_id},
        )

Não levanta — falha de audit não deve derrubar a operação principal. Loga
warning se algo der errado e segue. Faz commit próprio (audit não compete
pela transação do endpoint).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.utils.logging import get_logger

log = get_logger(__name__)


async def record_audit(
    session: AsyncSession,
    *,
    user: str,
    action: str,
    entity_type: str,
    entity_id: int | None,
    details: dict[str, Any] | None = None,
) -> None:
    """Insere uma linha em audit_log. Best-effort.

    Faz commit próprio: o registro de audit não bloqueia a operação principal
    (que já foi commitada antes). Em caso de erro, só loga warning.
    """
    try:
        entry = AuditLog(
            user=user,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
        session.add(entry)
        await session.commit()
        log.info(
            "audit_recorded",
            user=user,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
        )
    except Exception as exc:
        log.warning(
            "audit_record_failed",
            user=user,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            error=str(exc),
        )
        try:
            await session.rollback()
        except Exception:
            pass
