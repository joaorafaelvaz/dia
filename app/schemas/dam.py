"""Dam schemas."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DamBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    client_id: int
    dam_type: str = Field(min_length=1, max_length=50)
    municipality: str = Field(min_length=1, max_length=150)
    state: str = Field(min_length=2, max_length=3)
    country: str = Field(default="BR", max_length=3)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    anm_classification: str | None = None
    cri: str | None = None
    dpa: str | None = None
    capacity_m3: float | None = None
    status: str = "active"
    notes: str | None = None


class DamCreate(DamBase):
    is_active: bool = True


class DamUpdate(BaseModel):
    name: str | None = None
    client_id: int | None = None
    dam_type: str | None = None
    municipality: str | None = None
    state: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    anm_classification: str | None = None
    cri: str | None = None
    dpa: str | None = None
    capacity_m3: float | None = None
    status: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class DamRead(DamBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # Convenience: nome do client expandido pra UI/clients consumirem sem
    # JOIN extra. Populado pelo router via Dam.client (selectin eager load).
    client_name: str | None = None
