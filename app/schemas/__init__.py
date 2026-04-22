"""Pydantic v2 schemas (request/response models)."""
from app.schemas.alert import AlertAcknowledge, AlertCreate, AlertRead
from app.schemas.dam import DamCreate, DamRead, DamUpdate
from app.schemas.event import ClimateEventCreate, ClimateEventRead
from app.schemas.forecast import ForecastCreate, ForecastRead
from app.schemas.report import ReportCreate, ReportGenerateRequest, ReportRead

__all__ = [
    "DamCreate",
    "DamRead",
    "DamUpdate",
    "ClimateEventCreate",
    "ClimateEventRead",
    "ForecastCreate",
    "ForecastRead",
    "AlertCreate",
    "AlertRead",
    "AlertAcknowledge",
    "ReportCreate",
    "ReportRead",
    "ReportGenerateRequest",
]
