"""Report — relatório gerado (briefing interno ou cliente)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    report_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # "briefing" | "client"

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    scope: Mapped[str] = mapped_column(String(30), nullable=False)
    # "gerdau" | "kinross" | "all" | "custom"

    dam_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)

    content_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")

    events_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="generating")
    # "generating" | "ready" | "error"

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    generated_by: Mapped[str] = mapped_column(String(20), nullable=False, default="auto")
    # "auto" | "manual"

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    def __repr__(self) -> str:
        return (
            f"<Report id={self.id} type={self.report_type} "
            f"scope={self.scope} status={self.status}>"
        )
