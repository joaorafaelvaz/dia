"""Web (Jinja2) routes — dashboard, dams, events, alerts.

All pages gated by Basic Auth (delivered via the same dependency as the API).
HTMX partials are served from `/partials/*` for table/card refreshes.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.dependencies import AuthUser, SessionDep
from app.models.alert import Alert
from app.models.dam import Dam
from app.models.event import ClimateEvent
from app.models.forecast import Forecast

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

    counts = {
        "dams_total": len(dams),
        "dams_active": sum(1 for d in dams if d.is_active),
        "alerts_active": len(active_alerts),
        "alerts_critical": sum(1 for a in active_alerts if a.severity >= 4),
        "events_30d": len(recent_events),
        "forecasts_high_risk": len(upcoming_forecasts),
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
    dams = list(
        (await session.execute(select(Dam).order_by(Dam.owner_group, Dam.name)))
        .scalars()
        .all()
    )
    return templates.TemplateResponse(request, "dams/list.html", {"dams": dams})


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
