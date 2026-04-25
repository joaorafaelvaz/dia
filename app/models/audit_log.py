"""AuditLog — registro de quem mutou o quê.

Usa coluna "user" (string livre) ao invés de FK porque o sistema é Basic
Auth single-user e não temos tabela Users. Quando migrar pra SSO/multi-user,
isso pode virar FK; até lá string serve.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user: Mapped[str] = mapped_column(String(100), nullable=False)
    # Ex: "client.create", "dam.update", "alert.acknowledge"
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} user={self.user!r} "
            f"action={self.action!r} entity={self.entity_type}/{self.entity_id}>"
        )
