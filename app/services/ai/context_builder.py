"""Monta o contexto estruturado que entra no prompt dos relatórios IA.

O Claude vai gerar briefing e relatório-cliente; os prompts da §9 esperam
quatro blocos bem definidos:

1. `dam_profiles`  — ficha de cada barragem (nome, grupo, tipo, DPA, etc.)
2. `recent_events` — eventos climáticos relevantes do período
3. `forecasts`     — previsões dos próximos dias, ordenadas por risco
4. `active_alerts` — alertas abertos que ainda demandam ação

A função pública é `build_context()` — ela consulta o banco e devolve um
`ReportContext` serializável (via `to_dict()`) que é injetado no prompt como
blocos markdown. O relatório em si fica com o `report_generator`.

**Design:**
- Mantemos o texto dos blocos em português — o Claude responde no idioma
  do prompt e os relatórios são para times PT-BR.
- Filtros são intencionalmente *conservadores* pra reduzir ruído no prompt:
  eventos com severity < 2 são ignorados (barulho natural), previsões com
  risk_level < 3 idem. Se o operador quiser algo mais verboso, pode
  chamar `build_context(..., min_event_severity=1)`.
- **Sem hallucination pela estrutura:** só incluímos números que vêm do
  banco. O prompt do Opus depois pode resumir/interpretar, mas não deve
  inventar dado — os blocos são a única fonte autorizada.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.dam import Dam
from app.models.event import ClimateEvent
from app.models.forecast import Forecast
from app.utils.logging import get_logger
from app.utils.severity import label_for

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------

async def resolve_dam_ids(
    session: AsyncSession,
    *,
    scope: str,
    dam_ids: list[int] | None = None,
) -> list[int]:
    """Converte `scope` (+ `dam_ids` opcional) na lista final de IDs.

    Regras:
    - `scope="custom"` exige `dam_ids` não-vazio; se vier vazio, lança ValueError.
    - `scope="all"`   → todas as barragens com `is_active=True`.
    - `scope="gerdau"` / `"kinross"` → filtro por `owner_group` case-insensitive.
    """
    if scope == "custom":
        if not dam_ids:
            raise ValueError("scope='custom' requer dam_ids não-vazio")
        return list(dam_ids)

    stmt = select(Dam.id).where(Dam.is_active.is_(True))
    if scope != "all":
        stmt = stmt.where(Dam.owner_group.ilike(scope))

    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


# ---------------------------------------------------------------------------
# Context containers
# ---------------------------------------------------------------------------

@dataclass
class DamProfile:
    id: int
    name: str
    owner_group: str
    dam_type: str
    municipality: str
    state: str
    anm_classification: str | None
    cri: str | None
    dpa: str | None
    capacity_m3: float | None
    status: str
    notes: str | None


@dataclass
class EventSummary:
    dam_id: int
    dam_name: str
    event_type: str
    severity: int
    severity_label: str
    event_date: date
    source_type: str  # "weather" | "news" | "manual"
    source: str
    title: str
    description: str
    ai_analysis: str | None
    precipitation_mm: float | None


@dataclass
class ForecastSummary:
    dam_id: int
    dam_name: str
    forecast_date: date
    risk_level: int
    risk_label: str
    max_precipitation_mm: float
    total_precipitation_mm: float
    weather_description: str | None
    alert_threshold_exceeded: bool


@dataclass
class AlertSummary:
    dam_id: int
    dam_name: str
    alert_type: str
    severity: int
    title: str
    message: str
    created_at: datetime
    forecast_date: date | None


@dataclass
class ReportContext:
    """Resultado final do context builder — pronto pra virar prompt."""

    scope: str
    period_start: date
    period_end: date
    generated_at: datetime
    dam_profiles: list[DamProfile] = field(default_factory=list)
    recent_events: list[EventSummary] = field(default_factory=list)
    forecasts: list[ForecastSummary] = field(default_factory=list)
    active_alerts: list[AlertSummary] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serializa em primitivos — usado como `events_summary` no Report."""
        return {
            "scope": self.scope,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "dam_count": len(self.dam_profiles),
            "event_count": len(self.recent_events),
            "forecast_count": len(self.forecasts),
            "alert_count": len(self.active_alerts),
            "events_by_severity": _group_by_severity(self.recent_events),
            "events_by_type": _group_by_type(self.recent_events),
        }


def _group_by_severity(events: list[EventSummary]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in events:
        out[e.severity_label] = out.get(e.severity_label, 0) + 1
    return out


def _group_by_type(events: list[EventSummary]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in events:
        out[e.event_type] = out.get(e.event_type, 0) + 1
    return out


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def render_dam_profiles_md(profiles: list[DamProfile]) -> str:
    """Gera um bloco markdown com uma ficha por barragem."""
    if not profiles:
        return "_(Nenhuma barragem no escopo)_"
    lines: list[str] = []
    for d in profiles:
        lines.append(f"### {d.name} — {d.owner_group}")
        lines.append(f"- **Município/UF:** {d.municipality}/{d.state}")
        lines.append(f"- **Tipo:** {d.dam_type}")
        parts = []
        if d.anm_classification:
            parts.append(f"Classe ANM {d.anm_classification}")
        if d.cri:
            parts.append(f"CRI {d.cri}")
        if d.dpa:
            parts.append(f"DPA {d.dpa}")
        if parts:
            lines.append(f"- **Classificação:** {', '.join(parts)}")
        if d.capacity_m3:
            lines.append(f"- **Capacidade:** {d.capacity_m3:,.0f} m³")
        lines.append(f"- **Status:** {d.status}")
        if d.notes:
            lines.append(f"- **Obs:** {d.notes}")
        lines.append("")
    return "\n".join(lines).strip()


def render_events_md(events: list[EventSummary]) -> str:
    """Gera um bloco markdown com os eventos recentes, mais severos primeiro."""
    if not events:
        return "_(Nenhum evento significativo no período)_"
    lines: list[str] = []
    for e in events:
        src_tag = {"news": "📰", "weather": "🌧️", "manual": "✍️"}.get(e.source_type, "•")
        lines.append(
            f"- **{e.event_date.isoformat()}** — {e.dam_name} — "
            f"`{e.event_type}` · severidade {e.severity}/{e.severity_label} {src_tag}"
        )
        lines.append(f"  - {e.title}")
        if e.ai_analysis:
            lines.append(f"  - Análise: {e.ai_analysis}")
        elif e.description and e.description != e.title:
            snippet = e.description[:240].replace("\n", " ").strip()
            lines.append(f"  - {snippet}")
        if e.precipitation_mm:
            lines.append(f"  - Precipitação: {e.precipitation_mm:.0f} mm")
        lines.append(f"  - Fonte: {e.source}")
    return "\n".join(lines)


def render_forecasts_md(forecasts: list[ForecastSummary]) -> str:
    if not forecasts:
        return "_(Nenhuma previsão de risco relevante nos próximos dias)_"
    lines: list[str] = []
    for f in forecasts:
        marker = "🔴" if f.alert_threshold_exceeded else "🟠" if f.risk_level >= 4 else "🟡"
        lines.append(
            f"- {marker} **{f.forecast_date.isoformat()}** — {f.dam_name} — "
            f"risco {f.risk_level}/{f.risk_label}"
        )
        lines.append(
            f"  - Precipitação prevista: {f.max_precipitation_mm:.0f} mm "
            f"(total {f.total_precipitation_mm:.0f} mm)"
        )
        if f.weather_description:
            lines.append(f"  - Condição: {f.weather_description}")
    return "\n".join(lines)


def render_alerts_md(alerts: list[AlertSummary]) -> str:
    if not alerts:
        return "_(Nenhum alerta ativo)_"
    lines: list[str] = []
    for a in alerts:
        lines.append(
            f"- **{a.dam_name}** — `{a.alert_type}` · severidade {a.severity}"
        )
        lines.append(f"  - {a.title}")
        if a.message:
            snippet = a.message[:240].replace("\n", " ").strip()
            lines.append(f"  - {snippet}")
        if a.forecast_date:
            lines.append(f"  - Data-alvo da previsão: {a.forecast_date.isoformat()}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

async def build_context(
    session: AsyncSession,
    *,
    scope: str,
    dam_ids: list[int] | None = None,
    period_days: int = 30,
    forecast_days: int = 7,
    min_event_severity: int = 2,
    min_forecast_risk: int = 3,
    include_test: bool = False,
) -> ReportContext:
    """Busca no banco e monta o `ReportContext` para o relatório.

    Args:
        scope: "all" | "gerdau" | "kinross" | "custom".
        dam_ids: obrigatório quando `scope="custom"`; ignorado nos demais.
        period_days: janela histórica considerada (eventos passados).
        forecast_days: janela futura considerada (previsões).
        min_event_severity: corta eventos abaixo desse valor (reduz ruído).
        min_forecast_risk: idem para forecasts.
        include_test: se False (default), filtra Alert/Forecast com
            `is_test=True` — relatórios automáticos NUNCA passam True. Geração
            manual via API pode optar por incluir pra validar pipeline.

    Returns:
        `ReportContext` pronto pra passar ao `report_generator`.
    """
    now = datetime.now(tz=timezone.utc)
    period_end = now.date()
    period_start = period_end - timedelta(days=period_days)
    forecast_end = period_end + timedelta(days=forecast_days)

    resolved_ids = await resolve_dam_ids(session, scope=scope, dam_ids=dam_ids)
    if not resolved_ids:
        log.warning("report_context_empty_scope", scope=scope)
        return ReportContext(
            scope=scope,
            period_start=period_start,
            period_end=period_end,
            generated_at=now,
        )

    dams_stmt = select(Dam).where(Dam.id.in_(resolved_ids)).order_by(Dam.name)
    dams = (await session.execute(dams_stmt)).scalars().all()
    dam_by_id = {d.id: d for d in dams}
    dam_profiles = [
        DamProfile(
            id=d.id,
            name=d.name,
            owner_group=d.owner_group,
            dam_type=d.dam_type,
            municipality=d.municipality,
            state=d.state,
            anm_classification=d.anm_classification,
            cri=d.cri,
            dpa=d.dpa,
            capacity_m3=d.capacity_m3,
            status=d.status,
            notes=d.notes,
        )
        for d in dams
    ]

    # Eventos — ordenados por severidade desc, depois data desc. Limitamos a
    # 50 para caber no prompt (Opus 4.7 aguenta bem, mas gasta token à toa).
    events_stmt = (
        select(ClimateEvent)
        .where(
            and_(
                ClimateEvent.dam_id.in_(resolved_ids),
                ClimateEvent.event_date >= period_start,
                ClimateEvent.event_date <= period_end,
                ClimateEvent.severity >= min_event_severity,
            )
        )
        .order_by(ClimateEvent.severity.desc(), ClimateEvent.event_date.desc())
        .limit(50)
    )
    events = (await session.execute(events_stmt)).scalars().all()
    recent_events = [
        EventSummary(
            dam_id=e.dam_id,
            dam_name=dam_by_id[e.dam_id].name if e.dam_id in dam_by_id else "?",
            event_type=e.event_type,
            severity=e.severity,
            severity_label=e.severity_label or label_for(e.severity),
            event_date=e.event_date,
            source_type=e.source_type,
            source=e.source,
            title=e.title,
            description=e.description,
            ai_analysis=e.ai_analysis,
            precipitation_mm=e.precipitation_mm,
        )
        for e in events
    ]

    # Previsões — só as de alto risco, ordenadas por data asc (cronológico).
    fc_conditions = [
        Forecast.dam_id.in_(resolved_ids),
        Forecast.forecast_date >= period_end,
        Forecast.forecast_date <= forecast_end,
        Forecast.risk_level >= min_forecast_risk,
    ]
    if not include_test:
        fc_conditions.append(Forecast.is_test.is_(False))
    fc_stmt = (
        select(Forecast)
        .where(and_(*fc_conditions))
        .order_by(Forecast.forecast_date.asc(), Forecast.risk_level.desc())
    )
    fcs = (await session.execute(fc_stmt)).scalars().all()
    forecasts = [
        ForecastSummary(
            dam_id=f.dam_id,
            dam_name=dam_by_id[f.dam_id].name if f.dam_id in dam_by_id else "?",
            forecast_date=f.forecast_date,
            risk_level=f.risk_level,
            risk_label=f.risk_label,
            max_precipitation_mm=f.max_precipitation_mm,
            total_precipitation_mm=f.total_precipitation_mm,
            weather_description=f.weather_description,
            alert_threshold_exceeded=f.alert_threshold_exceeded,
        )
        for f in fcs
    ]

    # Alertas ativos.
    alerts_conditions = [Alert.dam_id.in_(resolved_ids), Alert.is_active.is_(True)]
    if not include_test:
        alerts_conditions.append(Alert.is_test.is_(False))
    alerts_stmt = (
        select(Alert)
        .where(and_(*alerts_conditions))
        .order_by(Alert.severity.desc(), Alert.created_at.desc())
    )
    alerts = (await session.execute(alerts_stmt)).scalars().all()
    active_alerts = [
        AlertSummary(
            dam_id=a.dam_id,
            dam_name=dam_by_id[a.dam_id].name if a.dam_id in dam_by_id else "?",
            alert_type=a.alert_type,
            severity=a.severity,
            title=a.title,
            message=a.message,
            created_at=a.created_at,
            forecast_date=a.forecast_date,
        )
        for a in alerts
    ]

    ctx = ReportContext(
        scope=scope,
        period_start=period_start,
        period_end=period_end,
        generated_at=now,
        dam_profiles=dam_profiles,
        recent_events=recent_events,
        forecasts=forecasts,
        active_alerts=active_alerts,
    )
    log.info(
        "report_context_built",
        scope=scope,
        dam_count=len(dam_profiles),
        event_count=len(recent_events),
        forecast_count=len(forecasts),
        alert_count=len(active_alerts),
    )
    return ctx


__all__ = [
    "AlertSummary",
    "DamProfile",
    "EventSummary",
    "ForecastSummary",
    "ReportContext",
    "build_context",
    "render_alerts_md",
    "render_dam_profiles_md",
    "render_events_md",
    "render_forecasts_md",
    "resolve_dam_ids",
]
