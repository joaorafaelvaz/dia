"""Celery tasks de geração de relatórios.

Dois caminhos de entrada:

1. **Sob demanda** (via API `POST /api/v1/reports/generate`):
   a task cria o `Report` com `status="generating"`, dispara o gerador e
   atualiza o registro com o conteúdo pronto ou com `status="error"`.

2. **Agendado** (Celery beat):
   - `generate_weekly_briefing` — segunda 7h, scope=all
   - `generate_monthly_client_reports` — dia 1 às 8h, um por owner_group

Ambos usam `task_session()` (NullPool) pela mesma razão do news_tasks:
asyncpg amarra conexões ao event-loop que as criou, e Celery cria um loop
novo por invocação de `asyncio.run`.

**Idempotência:** a task de agendamento verifica se já existe um relatório
"ready" no mesmo dia com mesmo tipo+scope, e no-op nesse caso. Útil pra
sobrevivência de reboot do worker (beat reentra na janela).
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import task_session
from app.models.dam import Dam
from app.models.report import Report
from app.services.ai.context_builder import build_context
from app.services.ai.report_generator import (
    default_title,
    generate_briefing,
    generate_client_report,
)
from app.tasks.celery_app import celery_app
from app.utils.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Core async worker — one report from scratch
# ---------------------------------------------------------------------------

async def _generate_one(
    *,
    report_id: int,
    report_type: str,
    scope: str,
    dam_ids: list[int] | None,
    period_days: int,
    forecast_days: int,
    include_test: bool = False,
) -> None:
    """Preenche um `Report` já criado com `status='generating'`.

    Fluxo:
    1. Carrega o Report pelo ID.
    2. Monta o contexto.
    3. Chama o generator apropriado (briefing ou client).
    4. Atualiza content_markdown, content_html, events_summary, dam_ids,
       status='ready', generated_at=now.

    Em erro: grava `status='error'` + `error_message` e re-raise para Celery
    aplicar sua política de retry.
    """
    async with task_session() as session:
        report = await session.get(Report, report_id)
        if report is None:
            log.error("report_task_missing_row", report_id=report_id)
            return

        try:
            ctx = await build_context(
                session,
                scope=scope,
                dam_ids=dam_ids,
                period_days=period_days,
                forecast_days=forecast_days,
                include_test=include_test,
            )

            title_suffix = default_title(report_type, scope, period_days)
            if report_type == "briefing":
                md, html = await generate_briefing(
                    session, ctx,
                    title_suffix=title_suffix,
                    forecast_days=forecast_days,
                )
            elif report_type == "client":
                md, html = await generate_client_report(
                    session, ctx,
                    title_suffix=title_suffix,
                    forecast_days=forecast_days,
                )
            else:
                raise ValueError(f"report_type inválido: {report_type!r}")

            # Resolve dam_ids finais (pra scope "all/gerdau/kinross" já expandido)
            final_dam_ids = [d.id for d in ctx.dam_profiles]

            report.content_markdown = md
            report.content_html = html
            report.events_summary = ctx.to_dict()
            report.dam_ids = final_dam_ids
            report.period_start = ctx.period_start
            report.period_end = ctx.period_end
            report.status = "ready"
            report.generated_at = datetime.now(tz=timezone.utc)
            report.error_message = None

            await session.commit()
            log.info(
                "report_generated",
                report_id=report_id,
                report_type=report_type,
                scope=scope,
                md_len=len(md),
                html_len=len(html),
            )
        except Exception as exc:
            await session.rollback()
            log.error(
                "report_generation_failed",
                report_id=report_id,
                report_type=report_type,
                scope=scope,
                error=str(exc),
            )
            # Persiste o erro num commit separado (senão perdemos o estado).
            async with task_session() as err_session:
                err_report = await err_session.get(Report, report_id)
                if err_report is not None:
                    err_report.status = "error"
                    err_report.error_message = f"{type(exc).__name__}: {exc}"[:2000]
                    await err_session.commit()
            raise


@celery_app.task(
    name="app.tasks.report_tasks.generate_report",
    bind=True,
    max_retries=1,  # Opus é caro; não queremos retry automático agressivo
    default_retry_delay=300,
)
def generate_report(
    self,
    report_id: int,
    report_type: str,
    scope: str,
    dam_ids: list[int] | None = None,
    period_days: int = 30,
    forecast_days: int = 7,
    include_test: bool = False,
) -> dict[str, Any]:
    """Entry point Celery: chama o worker async e devolve um dict curto."""
    try:
        asyncio.run(
            _generate_one(
                report_id=report_id,
                report_type=report_type,
                scope=scope,
                dam_ids=dam_ids,
                period_days=period_days,
                forecast_days=forecast_days,
                include_test=include_test,
            )
        )
        return {"report_id": report_id, "status": "ready"}
    except SoftTimeLimitExceeded:
        log.warning("report_task_soft_timeout", report_id=report_id)
        raise
    except Exception as exc:
        log.error("report_task_failed", report_id=report_id, error=str(exc))
        # Não fazemos retry automático: o erro já foi persistido no Report.
        # Retornamos status error em vez de re-raise pra evitar ruído no Flower.
        return {"report_id": report_id, "status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Scheduled: weekly briefing
# ---------------------------------------------------------------------------

async def _was_generated_today(
    session: AsyncSession, *, report_type: str, scope: str
) -> bool:
    """Idempotência: já existe um relatório 'ready' com mesmo tipo+scope hoje?"""
    today = date.today()
    stmt = select(func.count(Report.id)).where(
        and_(
            Report.report_type == report_type,
            Report.scope == scope,
            Report.status == "ready",
            func.date(Report.generated_at) == today,
        )
    )
    count = (await session.execute(stmt)).scalar_one()
    return bool(count)


async def _create_scheduled_report_row(
    *,
    report_type: str,
    scope: str,
    period_days: int,
) -> int | None:
    """Cria a linha Report com status='generating'. Retorna None se skip idempotente."""
    async with task_session() as session:
        if await _was_generated_today(session, report_type=report_type, scope=scope):
            log.info(
                "scheduled_report_skip_duplicate",
                report_type=report_type,
                scope=scope,
            )
            return None

        today = date.today()
        report = Report(
            report_type=report_type,
            title=default_title(report_type, scope, period_days),
            scope=scope,
            dam_ids=[],
            period_start=today - timedelta(days=period_days),
            period_end=today,
            status="generating",
            generated_by="auto",
        )
        session.add(report)
        await session.commit()
        await session.refresh(report)
        return report.id


@celery_app.task(name="app.tasks.report_tasks.generate_weekly_briefing")
def generate_weekly_briefing() -> dict[str, Any]:
    """Briefing semanal agendado — toda segunda 7h, scope='all'."""
    report_id = asyncio.run(
        _create_scheduled_report_row(
            report_type="briefing", scope="all", period_days=7
        )
    )
    if report_id is None:
        return {"status": "skipped_duplicate"}
    # Dispara o generator em outra task pra liberar o beat rápido.
    generate_report.delay(
        report_id=report_id,
        report_type="briefing",
        scope="all",
        dam_ids=None,
        period_days=7,
        forecast_days=7,
    )
    return {"status": "dispatched", "report_id": report_id}


# ---------------------------------------------------------------------------
# Scheduled: monthly client reports (one per owner_group)
# ---------------------------------------------------------------------------

async def _owner_groups() -> list[str]:
    async with task_session() as session:
        stmt = (
            select(Dam.owner_group)
            .where(Dam.is_active.is_(True))
            .group_by(Dam.owner_group)
            .order_by(Dam.owner_group)
        )
        rows = (await session.execute(stmt)).scalars().all()
        # Normaliza — "Outro"/"Other" não vai pra cliente.
        return [g for g in rows if g.lower() not in {"outro", "other", ""}]


@celery_app.task(name="app.tasks.report_tasks.generate_monthly_client_reports")
def generate_monthly_client_reports() -> dict[str, Any]:
    """Um relatório-cliente mensal por owner_group — dia 1 às 8h."""
    groups = asyncio.run(_owner_groups())
    dispatched: list[int] = []
    skipped: list[str] = []
    for group in groups:
        scope_key = group.lower()  # "Gerdau" → "gerdau"
        # O scope aceita só literais fechados; usamos a string lowercase.
        # O resolver (context_builder.resolve_dam_ids) trata qualquer string
        # como filtro ILIKE em owner_group, o que cobre essa convenção.
        report_id = asyncio.run(
            _create_scheduled_report_row(
                report_type="client", scope=scope_key, period_days=30
            )
        )
        if report_id is None:
            skipped.append(scope_key)
            continue
        generate_report.delay(
            report_id=report_id,
            report_type="client",
            scope=scope_key,
            dam_ids=None,
            period_days=30,
            forecast_days=7,
        )
        dispatched.append(report_id)
    return {"dispatched": dispatched, "skipped_duplicate": skipped}


__all__ = [
    "generate_monthly_client_reports",
    "generate_report",
    "generate_weekly_briefing",
]
