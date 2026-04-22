"""Severity scale and risk scoring helpers."""
from typing import TypedDict

from app.config import settings


class SeverityInfo(TypedDict):
    label: str
    color: str
    description: str


SEVERITY_SCALE: dict[int, SeverityInfo] = {
    1: {
        "label": "Baixo",
        "color": "#22c55e",
        "description": "Evento monitorável, sem risco imediato",
    },
    2: {
        "label": "Moderado",
        "color": "#eab308",
        "description": "Atenção recomendada, monitoramento intensificado",
    },
    3: {
        "label": "Alto",
        "color": "#f97316",
        "description": "Risco significativo, ação preventiva recomendada",
    },
    4: {
        "label": "Muito Alto",
        "color": "#ef4444",
        "description": "Risco iminente, acionar PAE e autoridades",
    },
    5: {
        "label": "Crítico",
        "color": "#a855f7",
        "description": "Emergência. Evacuação e comunicação imediata",
    },
}


def label_for(severity: int) -> str:
    return SEVERITY_SCALE.get(severity, SEVERITY_SCALE[1])["label"]


def color_for(severity: int) -> str:
    return SEVERITY_SCALE.get(severity, SEVERITY_SCALE[1])["color"]


def severity_from_precipitation(
    precipitation_mm: float,
    dam_type: str | None = None,
    dpa: str | None = None,
) -> int:
    """Return severity 1-5 from a precipitation value, adjusting for dam risk profile.

    Thresholds come from settings.alert_rain_mm_24h_*. Tailings dams have 20% lower
    thresholds; DPA=Alto reduces another 10%.
    """
    multiplier = 1.0
    if dam_type == "tailings":
        multiplier *= 0.8
    if dpa == "Alto":
        multiplier *= 0.9

    moderate = settings.alert_rain_mm_24h_moderate * multiplier
    high = settings.alert_rain_mm_24h_high * multiplier
    very_high = settings.alert_rain_mm_24h_very_high * multiplier
    critical = settings.alert_rain_mm_24h_critical * multiplier

    if precipitation_mm >= critical:
        return 5
    if precipitation_mm >= very_high:
        return 4
    if precipitation_mm >= high:
        return 3
    if precipitation_mm >= moderate:
        return 2
    return 1
