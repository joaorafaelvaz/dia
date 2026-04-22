"""ClimateEvent — evento climático ocorrido (histórico ou derivado de notícias)."""
from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.dam import Dam


class ClimateEvent(Base):
    __tablename__ = "climate_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dam_id: Mapped[int] = mapped_column(
        ForeignKey("dams.id", ondelete="CASCADE"), nullable=False, index=True
    )

    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # "heavy_rain" | "flood" | "drought" | "landslide" | "dam_failure_risk" | "other"

    severity: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # 1-5
    severity_label: Mapped[str] = mapped_column(String(30), nullable=False)

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="weather", index=True
    )
    # "weather" | "news" | "manual" — used for cross-source dedup

    source: Mapped[str] = mapped_column(String(500), nullable=False)
    # URL or provider name (e.g. "open_meteo_archive")

    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    precipitation_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    affected_area_km2: Mapped[float | None] = mapped_column(Float, nullable=True)
    casualties: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evacuated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    economic_damage_brl: Mapped[float | None] = mapped_column(Float, nullable=True)

    ai_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    dam: Mapped[Dam] = relationship(back_populates="events")

    def __repr__(self) -> str:
        return (
            f"<ClimateEvent id={self.id} dam_id={self.dam_id} "
            f"type={self.event_type} sev={self.severity} date={self.event_date}>"
        )
