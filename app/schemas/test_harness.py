"""Schemas dos endpoints de test-harness — alertas e forecasts sintéticos."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class TestAlertCreate(BaseModel):
    """Modo A — alerta inserido direto. Bypassa pipeline de detecção."""

    dam_id: int
    alert_type: str = Field(default="threshold_exceeded", max_length=50)
    severity: int = Field(ge=1, le=5)
    title: str = Field(max_length=300)
    message: str
    forecast_date: date | None = None
    expires_at: datetime | None = None
    # Quando False, o endpoint pré-marca notified_*=True na criação do Alert
    # pra que o sweep do dispatcher pule. Default True respeita o intent
    # principal do harness ("validar gatilho").
    send_notification: bool = True


class TestForecastCreate(BaseModel):
    """Modo B — forecast sintético. Aciona check_and_create_alerts e o
    Alert herdado vira `is_test=True` via aggregator."""

    dam_id: int
    forecast_date: date
    max_precipitation_mm: float = Field(ge=0.0, le=500.0)
    send_notification: bool = True


class TestHarnessAlertResult(BaseModel):
    """Resposta dos POSTs — id do alerta criado + se notif foi pedida."""

    alert_id: int | None = None
    forecast_id: int | None = None
    is_test: bool = True
    send_notification: bool
    detail: str


class TestHarnessPurgeResult(BaseModel):
    older_than_days: int
    alerts_deleted: int
    forecasts_deleted: int
