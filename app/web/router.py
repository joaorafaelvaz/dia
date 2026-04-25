"""Web (Jinja2) routes — dashboard, dams, events, alerts.

All pages gated by Basic Auth (delivered via the same dependency as the API).
HTMX partials are served from `/partials/*` for table/card refreshes.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.dependencies import AuthUser, SessionDep
from app.models.ai_usage import AIUsage
from app.models.alert import Alert
from app.models.client import Client
from app.models.dam import Dam
from app.models.event import ClimateEvent
from app.models.forecast import Forecast
from app.models.report import Report

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

web_router = APIRouter(tags=["web"], include_in_schema=False)


async def _dashboard_context(session) -> dict:
    today = date.today()
    horizon = today + timedelta(days=7)

    dams = list((await session.execute(select(Dam).order_by(Dam.name))).scalars().all())

    active_alerts = list(
        (
            await session.execute(
                select(Alert)
                .where(Alert.is_active.is_(True))
                .order_by(Alert.severity.desc(), Alert.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )

    recent_events = list(
        (
            await session.execute(
                select(ClimateEvent)
                .where(ClimateEvent.event_date >= today - timedelta(days=30))
                .order_by(ClimateEvent.event_date.desc())
                .limit(25)
            )
        )
        .scalars()
        .all()
    )

    upcoming_forecasts = list(
        (
            await session.execute(
                select(Forecast)
                .where(
                    Forecast.forecast_date >= today,
                    Forecast.forecast_date <= horizon,
                    Forecast.risk_level >= 3,
                )
                .order_by(Forecast.forecast_date, Forecast.risk_level.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )

    # Custo acumulado de IA nos últimos 30 dias + chamadas
    cutoff_30d = datetime.now(tz=timezone.utc) - timedelta(days=30)
    ai_row = (
        await session.execute(
            select(
                func.coalesce(func.sum(AIUsage.cost_usd), 0.0).label("cost"),
                func.count(AIUsage.id).label("calls"),
            ).where(AIUsage.created_at >= cutoff_30d)
        )
    ).one()

    # Contagem de eventos por source_type (weather vs news) para o card
    ev_sources = dict(
        (r.source_type, int(r.n))
        for r in (
            await session.execute(
                select(
                    ClimateEvent.source_type.label("source_type"),
                    func.count(ClimateEvent.id).label("n"),
                )
                .where(ClimateEvent.event_date >= today - timedelta(days=30))
                .group_by(ClimateEvent.source_type)
            )
        ).all()
    )

    counts = {
        "dams_total": len(dams),
        "dams_active": sum(1 for d in dams if d.is_active),
        "alerts_active": len(active_alerts),
        "alerts_critical": sum(1 for a in active_alerts if a.severity >= 4),
        "events_30d": len(recent_events),
        "events_weather_30d": ev_sources.get("weather", 0),
        "events_news_30d": ev_sources.get("news", 0),
        "forecasts_high_risk": len(upcoming_forecasts),
        "ai_cost_30d": float(ai_row.cost or 0.0),
        "ai_calls_30d": int(ai_row.calls or 0),
    }

    dams_by_id = {d.id: d for d in dams}

    return {
        "dams": dams,
        "dams_by_id": dams_by_id,
        "active_alerts": active_alerts,
        "recent_events": recent_events,
        "upcoming_forecasts": upcoming_forecasts,
        "counts": counts,
        "now": datetime.utcnow(),
    }


@web_router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request, session: SessionDep, _: AuthUser
) -> HTMLResponse:
    ctx = await _dashboard_context(session)
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@web_router.get("/dams", response_class=HTMLResponse)
async def dams_list(
    request: Request, session: SessionDep, _: AuthUser
) -> HTMLResponse:
    # JOIN client pra ordenar por nome do cliente; eager-load via selectin
    # já cobre a property dam.owner_group nas linhas do template.
    dams = list(
        (
            await session.execute(
                select(Dam).join(Client, Dam.client_id == Client.id).order_by(
                    Client.name, Dam.name
                )
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(request, "dams/list.html", {"dams": dams})


@web_router.get("/dams/new", response_class=HTMLResponse)
async def dams_new(
    request: Request,
    session: SessionDep,
    _: AuthUser,
    client_id: int | None = None,
) -> HTMLResponse:
    """Formulário de nova barragem. Carrega clients ativos pro dropdown.

    `?client_id=N` pré-seleciona aquele cliente — usado pelo botão "+ Adicionar
    barragem" dentro de /clients/{id}, pra o operador não precisar escolher
    de novo.
    """
    clients = list(
        (
            await session.execute(
                select(Client).where(Client.is_active.is_(True)).order_by(Client.name)
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "dams/form.html",
        {"dam": None, "clients": clients, "preselected_client_id": client_id},
    )


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

@web_router.get("/clients", response_class=HTMLResponse)
async def clients_list(
    request: Request, session: SessionDep, _: AuthUser
) -> HTMLResponse:
    """Tabela de clientes + count de barragens via outerjoin."""
    stmt = (
        select(Client, func.count(Dam.id).label("dam_count"))
        .outerjoin(Dam, Dam.client_id == Client.id)
        .group_by(Client.id)
        .order_by(Client.name)
    )
    rows = (await session.execute(stmt)).all()
    clients = []
    for client, dam_count in rows:
        # Espelha o que ClientRead exporia, mas como objeto simples pro template
        client.dam_count = int(dam_count or 0)
        clients.append(client)
    return templates.TemplateResponse(request, "clients/list.html", {"clients": clients})


@web_router.get("/clients/new", response_class=HTMLResponse)
async def clients_new(
    request: Request, _: AuthUser
) -> HTMLResponse:
    return templates.TemplateResponse(request, "clients/form.html", {"client": None})


@web_router.get("/clients/{client_id}", response_class=HTMLResponse)
async def clients_edit(
    client_id: int,
    request: Request,
    session: SessionDep,
    _: AuthUser,
) -> HTMLResponse:
    """Form de edição de cliente + sub-tabela de barragens associadas.

    Operador adiciona/remove dams direto daqui:
      - "+ Adicionar barragem" → /dams/new?client_id=X (pré-seleciona)
      - Por linha: "Desativar" (PATCH is_active=false) | "Apagar" (DELETE)
    """
    client = await session.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    dams = list(
        (
            await session.execute(
                select(Dam).where(Dam.client_id == client_id).order_by(Dam.name)
            )
        )
        .scalars()
        .all()
    )
    client.dam_count = len(dams)
    return templates.TemplateResponse(
        request,
        "clients/form.html",
        {"client": client, "dams": dams},
    )


@web_router.get("/dams/{dam_id}", response_class=HTMLResponse)
async def dam_detail(
    dam_id: int, request: Request, session: SessionDep, _: AuthUser
) -> HTMLResponse:
    dam = await session.get(Dam, dam_id)
    if not dam:
        raise HTTPException(status_code=404, detail="Dam not found")

    today = date.today()
    horizon = today + timedelta(days=16)

    forecasts = list(
        (
            await session.execute(
                select(Forecast)
                .where(
                    Forecast.dam_id == dam_id,
                    Forecast.forecast_date >= today,
                    Forecast.forecast_date <= horizon,
                )
                .order_by(Forecast.forecast_date)
            )
        )
        .scalars()
        .all()
    )

    events = list(
        (
            await session.execute(
                select(ClimateEvent)
                .where(ClimateEvent.dam_id == dam_id)
                .order_by(ClimateEvent.event_date.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )

    alerts = list(
        (
            await session.execute(
                select(Alert)
                .where(Alert.dam_id == dam_id, Alert.is_active.is_(True))
                .order_by(Alert.severity.desc())
            )
        )
        .scalars()
        .all()
    )

    return templates.TemplateResponse(
        request,
        "dams/detail.html",
        {
            "dam": dam,
            "forecasts": forecasts,
            "events": events,
            "alerts": alerts,
        },
    )


@web_router.get("/events", response_class=HTMLResponse)
async def events_list(
    request: Request, session: SessionDep, _: AuthUser
) -> HTMLResponse:
    since = date.today() - timedelta(days=90)
    events = list(
        (
            await session.execute(
                select(ClimateEvent)
                .where(ClimateEvent.event_date >= since)
                .order_by(ClimateEvent.event_date.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    dam_ids = {e.dam_id for e in events}
    dams_by_id = {
        d.id: d
        for d in (
            await session.execute(select(Dam).where(Dam.id.in_(dam_ids)))
        )
        .scalars()
        .all()
    }
    return templates.TemplateResponse(
        request,
        "events/list.html",
        {"events": events, "dams_by_id": dams_by_id},
    )


@web_router.get("/test-harness", response_class=HTMLResponse)
async def test_harness_page(
    request: Request, session: SessionDep, _: AuthUser
) -> HTMLResponse:
    """Página de inserção manual de alertas/forecasts sintéticos."""
    dams = list(
        (
            await session.execute(
                select(Dam).where(Dam.is_active.is_(True)).order_by(Dam.name)
            )
        )
        .scalars()
        .all()
    )
    test_alerts = list(
        (
            await session.execute(
                select(Alert)
                .where(Alert.is_test.is_(True))
                .order_by(Alert.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    tomorrow_iso = (date.today() + timedelta(days=2)).isoformat()
    return templates.TemplateResponse(
        request,
        "test_harness.html",
        {
            "dams": dams,
            "test_alerts": test_alerts,
            "tomorrow_iso": tomorrow_iso,
        },
    )


@web_router.get("/reports", response_class=HTMLResponse)
async def reports_list(
    request: Request,
    session: SessionDep,
    _: AuthUser,
) -> HTMLResponse:
    """Página /reports — lista + filtros + botão 'Gerar agora'."""
    type_f = request.query_params.get("type")
    scope_f = request.query_params.get("scope")
    status_f = request.query_params.get("status")

    stmt = select(Report)
    if type_f:
        stmt = stmt.where(Report.report_type == type_f)
    if scope_f:
        stmt = stmt.where(Report.scope == scope_f)
    if status_f:
        stmt = stmt.where(Report.status == status_f)
    stmt = stmt.order_by(Report.generated_at.desc()).limit(100)

    reports = list((await session.execute(stmt)).scalars().all())
    return templates.TemplateResponse(
        request,
        "reports/list.html",
        {
            "reports": reports,
            "filters": {"type": type_f, "scope": scope_f, "status": status_f},
        },
    )


@web_router.get("/reports/{report_id}", response_class=HTMLResponse)
async def report_detail(
    report_id: int,
    request: Request,
    session: SessionDep,
    _: AuthUser,
) -> HTMLResponse:
    report = await session.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Relatório não encontrado")
    return templates.TemplateResponse(
        request, "reports/detail.html", {"report": report}
    )


@web_router.get("/partials/reports/{report_id}/status", response_class=HTMLResponse)
async def partial_report_status(
    report_id: int,
    request: Request,
    session: SessionDep,
    _: AuthUser,
) -> HTMLResponse:
    """HTMX: fragment que substitui o corpo do relatório quando `status=generating`.

    Polling no detail ativa em 4s. Quando status != generating, o fragment
    devolve o HTML completo (já renderizado) + `hx-swap-oob` para parar o
    polling dinamicamente.
    """
    report = await session.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Relatório não encontrado")
    return templates.TemplateResponse(
        request, "partials/report_status.html", {"report": report}
    )


@web_router.get("/partials/counters", response_class=HTMLResponse)
async def partial_counters(
    request: Request, session: SessionDep, _: AuthUser
) -> HTMLResponse:
    """HTMX fragment: refresh header counters without reloading the page."""
    ctx = await _dashboard_context(session)
    return templates.TemplateResponse(
        request, "partials/counters.html", {"counts": ctx["counts"]}
    )


@web_router.get("/partials/alerts", response_class=HTMLResponse)
async def partial_alerts(
    request: Request, session: SessionDep, _: AuthUser
) -> HTMLResponse:
    alerts = list(
        (
            await session.execute(
                select(Alert)
                .where(Alert.is_active.is_(True))
                .order_by(Alert.severity.desc(), Alert.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    dam_ids = {a.dam_id for a in alerts}
    dams_by_id = {
        d.id: d
        for d in (
            await session.execute(select(Dam).where(Dam.id.in_(dam_ids)))
        )
        .scalars()
        .all()
    }
    return templates.TemplateResponse(
        request,
        "partials/alerts.html",
        {"alerts": alerts, "dams_by_id": dams_by_id},
    )
