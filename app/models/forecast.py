"""Forecast — previsão climática futura para uma barragem."""
from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.dam import Dam


class Forecast(Base):
    __tablename__ = "forecasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dam_id: Mapped[int] = mapped_column(
        ForeignKey("dams.id", ondelete="CASCADE"), nullable=False, index=True
    )

    forecast_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source: Mapped[str] = mapped_column(String(50), nullable=False, default="open_meteo")
    # "open_meteo" | "inmet" | "cemaden"

    max_precipitation_mm: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_precipitation_mm: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_speed_kmh: Mapped[float | None] = mapped_column(Float, nullable=True)

    weather_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weather_description: Mapped[str | None] = mapped_column(String(200), nullable=True)

    risk_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    risk_label: Mapped[str] = mapped_column(String(30), nullable=False, default="Baixo")

    alert_threshold_exceeded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    ai_assessment: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    dam: Mapped[Dam] = relationship(back_populates="forecasts")

    def __repr__(self) -> str:
        return (
            f"<Forecast id={self.id} dam_id={self.dam_id} "
            f"date={self.forecast_date} risk={self.risk_level}>"
        )
