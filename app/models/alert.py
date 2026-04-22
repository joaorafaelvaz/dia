"""Alert — alerta ativo para uma barragem."""
from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.dam import Dam


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dam_id: Mapped[int] = mapped_column(
        ForeignKey("dams.id", ondelete="CASCADE"), nullable=False, index=True
    )

    alert_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # "forecast_warning" | "threshold_exceeded" | "news_event"

    severity: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # 1-5
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True
    )
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    forecast_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    notified_whatsapp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notified_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    dam: Mapped[Dam] = relationship(back_populates="alerts")

    def __repr__(self) -> str:
        return (
            f"<Alert id={self.id} dam_id={self.dam_id} "
            f"type={self.alert_type} sev={self.severity} active={self.is_active}>"
        )
