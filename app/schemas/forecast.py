"""Forecast schemas."""
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ForecastBase(BaseModel):
    dam_id: int
    forecast_date: date
    source: str = "open_meteo"
    max_precipitation_mm: float = 0.0
    total_precipitation_mm: float = 0.0
    max_temperature_c: float | None = None
    min_temperature_c: float | None = None
    wind_speed_kmh: float | None = None
    weather_code: int | None = None
    weather_description: str | None = None
    risk_level: int = Field(default=1, ge=1, le=5)
    risk_label: str = "Baixo"
    alert_threshold_exceeded: bool = False
    ai_assessment: str | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)


class ForecastCreate(ForecastBase):
    pass


class ForecastRead(ForecastBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    generated_at: datetime
    created_at: datetime
    is_test: bool
