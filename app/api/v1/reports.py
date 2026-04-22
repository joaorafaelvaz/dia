"""Relatórios — listagem, geração async, visualização em JSON/MD, download PDF.

Endpoints:
- `GET    /reports`                      — lista com filtros (type, scope, status)
- `POST   /reports/generate`             — cria Report 'generating' e dispara task
- `GET    /reports/{id}`                 — retorna JSON (ou markdown via Accept)
- `GET    /reports/{id}/pdf`             — streaming do PDF renderizado
- `DELETE /reports/{id}`                 — apaga (hard delete)

**Polling UX:** `POST /generate` retorna 202 com `report_id`. O front
pode fazer polling em `GET /{id}` e mostrar spinner enquanto
`status=="generating"`. Em erro, `error_message` vem populado.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app.dependencies import AuthUser, SessionDep
from app.models.report import Report
from app.schemas.report import ReportGenerateRequest, ReportRead
from app.services.ai.report_generator import default_title
from app.services.reports.pdf import render_report_pdf
from app.tasks import report_tasks
from app.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@router.get("", response_model=list[ReportRead])
async def list_reports(
    session: SessionDep,
    _: AuthUser,
    report_type: str | None = Query(default=None, alias="type"),
    scope: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[Report]:
    stmt = select(Report)
    if report_type:
        stmt = stmt.where(Report.report_type == report_type)
    if scope:
        stmt = stmt.where(Report.scope == scope)
    if status_filter:
        stmt = stmt.where(Report.status == status_filter)
    stmt = stmt.order_by(Report.generated_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------

@router.post(
    "/generate",
    response_model=ReportRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_report(
    payload: ReportGenerateRequest,
    session: SessionDep,
    _: AuthUser,
) -> Report:
    """Cria o Report 'generating' e dispara a task Celery.

    O corpo devolvido já tem o `id` — o front usa pra polling.
    """
    if payload.scope == "custom" and not payload.dam_ids:
        raise HTTPException(
            status_code=400,
            detail="scope='custom' requer dam_ids não-vazio",
        )

    today = date.today()
    title = default_title(payload.report_type, payload.scope, payload.period_days)
    report = Report(
        report_type=payload.report_type,
        title=title,
        scope=payload.scope,
        dam_ids=payload.dam_ids or [],
        period_start=today - timedelta(days=payload.period_days),
        period_end=today,
        status="generating",
        generated_by="manual",
        generated_at=datetime.now(tz=timezone.utc),
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)

    report_tasks.generate_report.delay(
        report_id=report.id,
        report_type=payload.report_type,
        scope=payload.scope,
        dam_ids=payload.dam_ids,
        period_days=payload.period_days,
        forecast_days=7,
    )
    log.info(
        "report_generate_dispatched",
        report_id=report.id,
        report_type=payload.report_type,
        scope=payload.scope,
    )
    return report


# ---------------------------------------------------------------------------
# Fetch / variants
# ---------------------------------------------------------------------------

async def _get_or_404(session: SessionDep, report_id: int) -> Report:
    report = await session.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Relatório não encontrado")
    return report


@router.get("/{report_id}", response_model=ReportRead)
async def get_report(
    report_id: int, session: SessionDep, _: AuthUser
) -> Report:
    return await _get_or_404(session, report_id)


@router.get("/{report_id}/markdown", response_class=PlainTextResponse)
async def get_report_markdown(
    report_id: int, session: SessionDep, _: AuthUser
) -> str:
    report = await _get_or_404(session, report_id)
    if report.status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Relatório com status '{report.status}' ainda não disponível",
        )
    return report.content_markdown or ""


@router.get("/{report_id}/pdf")
async def get_report_pdf(
    report_id: int, session: SessionDep, _: AuthUser
) -> Response:
    report = await _get_or_404(session, report_id)
    if report.status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Relatório com status '{report.status}' ainda não disponível",
        )
    pdf_bytes = render_report_pdf(report)
    safe_title = (report.title or f"report-{report.id}").replace('"', "")
    filename = f"{safe_title[:80]}.pdf".replace(" ", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
        },
    )


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: int, session: SessionDep, _: AuthUser
) -> Response:
    report = await _get_or_404(session, report_id)
    await session.delete(report)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
