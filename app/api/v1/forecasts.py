"""Forecast listing with filters."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.dependencies import AuthUser, SessionDep
from app.models.forecast import Forecast
from app.schemas.forecast import ForecastRead

router = APIRouter(prefix="/forecasts", tags=["forecasts"])


@router.get("", response_model=list[ForecastRead])
async def list_forecasts(
    session: SessionDep,
    _: AuthUser,
    dam_id: int | None = None,
    days: int = Query(default=16, ge=1, le=30),
    risk_level_min: int | None = Query(default=None, ge=1, le=5),
) -> list[Forecast]:
    today = date.today()
    horizon = today + timedelta(days=days)
    stmt = select(Forecast).where(
        Forecast.forecast_date >= today,
        Forecast.forecast_date <= horizon,
    )
    if dam_id is not None:
        stmt = stmt.where(Forecast.dam_id == dam_id)
    if risk_level_min is not None:
        stmt = stmt.where(Forecast.risk_level >= risk_level_min)
    stmt = stmt.order_by(Forecast.forecast_date, Forecast.dam_id)
    return list((await session.execute(stmt)).scalars().all())
