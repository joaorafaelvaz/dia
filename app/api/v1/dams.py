"""Dam CRUD + nested resources (events, forecasts)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.dependencies import AuthUser, SessionDep
from app.models.dam import Dam
from app.models.event import ClimateEvent
from app.models.forecast import Forecast
from app.schemas.dam import DamCreate, DamRead, DamUpdate
from app.schemas.event import ClimateEventRead
from app.schemas.forecast import ForecastRead

router = APIRouter(prefix="/dams", tags=["dams"])


@router.get("", response_model=list[DamRead])
async def list_dams(
    session: SessionDep,
    _: AuthUser,
    owner_group: str | None = None,
    state: str | None = None,
    is_active: bool | None = None,
) -> list[Dam]:
    stmt = select(Dam)
    if owner_group:
        stmt = stmt.where(Dam.owner_group == owner_group)
    if state:
        stmt = stmt.where(Dam.state == state)
    if is_active is not None:
        stmt = stmt.where(Dam.is_active.is_(is_active))
    stmt = stmt.order_by(Dam.owner_group, Dam.name)
    return list((await session.execute(stmt)).scalars().all())


@router.post("", response_model=DamRead, status_code=status.HTTP_201_CREATED)
async def create_dam(payload: DamCreate, session: SessionDep, _: AuthUser) -> Dam:
    dam = Dam(**payload.model_dump())
    session.add(dam)
    await session.commit()
    await session.refresh(dam)
    return dam


@router.get("/{dam_id}", response_model=DamRead)
async def get_dam(dam_id: int, session: SessionDep, _: AuthUser) -> Dam:
    dam = await session.get(Dam, dam_id)
    if not dam:
        raise HTTPException(status_code=404, detail="Dam not found")
    return dam


@router.patch("/{dam_id}", response_model=DamRead)
async def update_dam(
    dam_id: int, payload: DamUpdate, session: SessionDep, _: AuthUser
) -> Dam:
    dam = await session.get(Dam, dam_id)
    if not dam:
        raise HTTPException(status_code=404, detail="Dam not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(dam, key, value)
    await session.commit()
    await session.refresh(dam)
    return dam


@router.get("/{dam_id}/events", response_model=list[ClimateEventRead])
async def list_dam_events(
    dam_id: int,
    session: SessionDep,
    _: AuthUser,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[ClimateEvent]:
    stmt = (
        select(ClimateEvent)
        .where(ClimateEvent.dam_id == dam_id)
        .order_by(ClimateEvent.event_date.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


@router.get("/{dam_id}/forecasts", response_model=list[ForecastRead])
async def list_dam_forecasts(
    dam_id: int,
    session: SessionDep,
    _: AuthUser,
    days: int = Query(default=16, ge=1, le=30),
) -> list[Forecast]:
    from datetime import date, timedelta

    today = date.today()
    horizon = today + timedelta(days=days)
    stmt = (
        select(Forecast)
        .where(
            Forecast.dam_id == dam_id,
            Forecast.forecast_date >= today,
            Forecast.forecast_date <= horizon,
        )
        .order_by(Forecast.forecast_date)
    )
    return list((await session.execute(stmt)).scalars().all())
