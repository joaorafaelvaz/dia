"""Climate events listing with filters."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.dependencies import AuthUser, SessionDep
from app.models.client import Client
from app.models.dam import Dam
from app.models.event import ClimateEvent
from app.schemas.event import ClimateEventRead

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[ClimateEventRead])
async def list_events(
    session: SessionDep,
    _: AuthUser,
    severity_min: int | None = Query(default=None, ge=1, le=5),
    owner_group: str | None = None,
    source_type: str | None = None,
    days: int = Query(default=90, ge=1, le=730),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[ClimateEvent]:
    since = date.today() - timedelta(days=days)
    stmt = select(ClimateEvent).where(ClimateEvent.event_date >= since)
    if severity_min is not None:
        stmt = stmt.where(ClimateEvent.severity >= severity_min)
    if source_type:
        stmt = stmt.where(ClimateEvent.source_type == source_type)
    if owner_group:
        # Filtro retro-compat: param 'owner_group' agora bate em Client.name.
        stmt = (
            stmt.join(Dam, Dam.id == ClimateEvent.dam_id)
            .join(Client, Client.id == Dam.client_id)
            .where(Client.name == owner_group)
        )
    stmt = stmt.order_by(ClimateEvent.event_date.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())
