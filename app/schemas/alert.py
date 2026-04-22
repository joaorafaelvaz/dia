"""Alert schemas."""
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class AlertBase(BaseModel):
    dam_id: int
    alert_type: str
    severity: int = Field(ge=1, le=5)
    title: str
    message: str
    forecast_date: date | None = None
    expires_at: datetime | None = None


class AlertCreate(AlertBase):
    pass


class AlertAcknowledge(BaseModel):
    acknowledged_by: str | None = None


class AlertRead(AlertBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    acknowledged: bool
    acknowledged_at: datetime | None
    acknowledged_by: str | None
    created_at: datetime
    notified_whatsapp: bool
    notified_email: bool
