"""Dam schemas."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas._form import empty_string_to_none


# Campos opcionais que vêm de forms HTML — strings vazias precisam virar None
# antes do parsing pra evitar 422 em capacity_m3="".
_OPTIONAL_FORM_FIELDS = (
    "anm_classification", "cri", "dpa", "notes", "capacity_m3",
)


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

    _coerce_blank = field_validator(
        *_OPTIONAL_FORM_FIELDS, mode="before"
    )(lambda cls, v: empty_string_to_none(v))


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

    # PATCH também recebe form HTML (modo edição) — mesma proteção.
    _coerce_blank = field_validator(
        *_OPTIONAL_FORM_FIELDS, mode="before"
    )(lambda cls, v: empty_string_to_none(v))


class DamRead(DamBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # Convenience: nome do client expandido pra UI/clients consumirem sem
    # JOIN extra. Populado pelo router via Dam.client (selectin eager load).
    client_name: str | None = None
