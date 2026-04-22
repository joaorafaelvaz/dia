"""Alerts: list active, acknowledge."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.dependencies import AuthUser, SessionDep
from app.models.alert import Alert
from app.models.dam import Dam
from app.schemas.alert import AlertAcknowledge, AlertRead

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertRead])
async def list_alerts(
    session: SessionDep,
    _: AuthUser,
    is_active: bool = True,
    severity_min: int | None = Query(default=None, ge=1, le=5),
    owner_group: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[Alert]:
    stmt = select(Alert).where(Alert.is_active.is_(is_active))
    if severity_min is not None:
        stmt = stmt.where(Alert.severity >= severity_min)
    if owner_group:
        stmt = stmt.join(Dam, Dam.id == Alert.dam_id).where(Dam.owner_group == owner_group)
    stmt = stmt.order_by(Alert.severity.desc(), Alert.created_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


@router.post("/{alert_id}/acknowledge", response_model=AlertRead)
async def acknowledge_alert(
    alert_id: int,
    payload: AlertAcknowledge,
    session: SessionDep,
    user: AuthUser,
) -> Alert:
    alert = await session.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.acknowledged = True
    alert.acknowledged_at = datetime.utcnow()
    alert.acknowledged_by = payload.acknowledged_by or user
    alert.is_active = False
    await session.commit()
    await session.refresh(alert)
    return alert
