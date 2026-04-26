"""Client schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas._form import empty_string_to_none


_OPTIONAL_FORM_FIELDS = (
    "contact_name", "contact_email", "contact_phone", "cnpj", "notes",
)


class ClientBase(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    contact_name: str | None = Field(default=None, max_length=150)
    contact_email: str | None = Field(default=None, max_length=200)
    contact_phone: str | None = Field(default=None, max_length=50)
    cnpj: str | None = Field(default=None, max_length=20)
    notes: str | None = None
    is_active: bool = True

    _coerce_blank = field_validator(
        *_OPTIONAL_FORM_FIELDS, mode="before"
    )(lambda cls, v: empty_string_to_none(v))


class ClientCreate(ClientBase):
    pass


class ClientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    cnpj: str | None = None
    notes: str | None = None
    is_active: bool | None = None

    _coerce_blank = field_validator(
        *_OPTIONAL_FORM_FIELDS, mode="before"
    )(lambda cls, v: empty_string_to_none(v))


class ClientRead(ClientBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
    dam_count: int | None = None  # Populado opcionalmente no GET /clients
