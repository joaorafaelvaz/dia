"""ClimateEvent schemas."""
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ClimateEventBase(BaseModel):
    dam_id: int
    event_type: str
    severity: int = Field(ge=1, le=5)
    severity_label: str
    title: str
    description: str
    source_type: str = "weather"
    source: str
    event_date: date
    precipitation_mm: float | None = None
    affected_area_km2: float | None = None
    casualties: int | None = None
    evacuated: int | None = None
    economic_damage_brl: float | None = None
    ai_analysis: str | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)


class ClimateEventCreate(ClimateEventBase):
    pass


class ClimateEventRead(ClimateEventBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
