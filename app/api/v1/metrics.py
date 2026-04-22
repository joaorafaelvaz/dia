"""Métricas agregadas — começa com AI cost tracking (Fase 2).

Uso típico:
    GET /api/v1/metrics/ai-costs        → agregação 24h / 7d / 30d
    GET /api/v1/metrics/ai-costs?window=7d
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from app.dependencies import AuthUser, SessionDep
from app.models.ai_usage import AIUsage

router = APIRouter(prefix="/metrics", tags=["metrics"])

Window = Literal["24h", "7d", "30d", "all"]

_WINDOW_HOURS: dict[str, int | None] = {
    "24h": 24,
    "7d": 24 * 7,
    "30d": 24 * 30,
    "all": None,
}


async def _aggregate(session, window: Window) -> dict:
    hours = _WINDOW_HOURS.get(window)
    stmt = select(
        func.coalesce(func.sum(AIUsage.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(AIUsage.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(AIUsage.cost_usd), 0.0).label("cost_usd"),
        func.count(AIUsage.id).label("calls"),
        func.coalesce(
            func.sum(case((AIUsage.cache_hit.is_(True), 1), else_=0)), 0
        ).label("cache_hits"),
        func.coalesce(
            func.sum(case((AIUsage.error.isnot(None), 1), else_=0)), 0
        ).label("errors"),
    )
    if hours is not None:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
        stmt = stmt.where(AIUsage.created_at >= cutoff)

    row = (await session.execute(stmt)).one()
    calls = int(row.calls or 0)
    cache_hits = int(row.cache_hits or 0)
    hit_rate = (cache_hits / calls) if calls else 0.0

    return {
        "window": window,
        "calls": calls,
        "cache_hits": cache_hits,
        "cache_hit_rate": round(hit_rate, 4),
        "errors": int(row.errors or 0),
        "input_tokens": int(row.input_tokens or 0),
        "output_tokens": int(row.output_tokens or 0),
        "cost_usd": round(float(row.cost_usd or 0.0), 6),
    }


async def _breakdown_by(session, field, window: Window) -> list[dict]:
    hours = _WINDOW_HOURS.get(window)
    stmt = select(
        field.label("key"),
        func.count(AIUsage.id).label("calls"),
        func.coalesce(func.sum(AIUsage.cost_usd), 0.0).label("cost_usd"),
    ).group_by(field).order_by(func.sum(AIUsage.cost_usd).desc())
    if hours is not None:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
        stmt = stmt.where(AIUsage.created_at >= cutoff)

    return [
        {"key": r.key, "calls": int(r.calls), "cost_usd": round(float(r.cost_usd), 6)}
        for r in (await session.execute(stmt)).all()
    ]


@router.get("/ai-costs")
async def ai_costs(
    session: SessionDep,
    _: AuthUser,
    window: Window = Query("30d"),
) -> dict:
    """Agregação de custos Claude para a janela dada.

    Retorna totais + breakdown por modelo e por caller (news_classifier, report_*, …).
    """
    totals = await _aggregate(session, window)
    by_model = await _breakdown_by(session, AIUsage.model, window)
    by_caller = await _breakdown_by(session, AIUsage.caller, window)
    return {
        "totals": totals,
        "by_model": by_model,
        "by_caller": by_caller,
    }


@router.get("/ai-costs/summary")
async def ai_costs_summary(session: SessionDep, _: AuthUser) -> dict:
    """Versão compacta para o dashboard: 24h / 7d / 30d em um payload."""
    return {
        "24h": await _aggregate(session, "24h"),
        "7d": await _aggregate(session, "7d"),
        "30d": await _aggregate(session, "30d"),
    }
