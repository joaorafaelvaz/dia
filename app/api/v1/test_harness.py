"""Test harness — inserção manual de alertas e forecasts sintéticos.

Existe pra validar end-to-end:
  1. Dispatcher de notificações (n8n + WAHA, email SMTP)
  2. Pipeline detecção → alerta (modo forecast)
  3. Geração de relatórios IA (briefing/cliente) com `include_test=True`

Tudo o que entra aqui ganha `is_test=True`. Cron de relatórios filtra com
default. Operador pode purgar via DELETE /api/v1/test-harness/data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select

from app.dependencies import AuthUser, SessionDep
from app.models.alert import Alert
from app.models.dam import Dam
from app.models.forecast import Forecast
from app.schemas.alert import AlertRead
from app.schemas.test_harness import (
    TestAlertCreate,
    TestForecastCreate,
    TestHarnessAlertResult,
    TestHarnessPurgeResult,
)
from app.services.climate.aggregator import check_and_create_alerts, compute_risk_score
from app.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/test-harness", tags=["test-harness"])


# ---------------------------------------------------------------------------
# Modo A — alerta direto
# ---------------------------------------------------------------------------

@router.post(
    "/alerts",
    response_model=TestHarnessAlertResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_test_alert(
    payload: TestAlertCreate,
    session: SessionDep,
    _: AuthUser,
) -> TestHarnessAlertResult:
    """Cria Alert(is_test=True) direto. Sweep do dispatcher pega no próximo tick.

    Quando `send_notification=False`, marca notified_* True na criação pra
    o sweep nunca mandar mensagem. Útil pra popular dado pra teste de
    relatório sem disparar WhatsApp/email.
    """
    dam = await session.get(Dam, payload.dam_id)
    if dam is None:
        raise HTTPException(status_code=404, detail="Dam not found")

    suppress = not payload.send_notification
    alert = Alert(
        dam_id=payload.dam_id,
        alert_type=payload.alert_type,
        severity=payload.severity,
        title=payload.title,
        message=payload.message,
        forecast_date=payload.forecast_date,
        expires_at=payload.expires_at,
        is_active=True,
        is_test=True,
        # Se suprimindo notif, pré-marca como já enviado pra dispatcher pular.
        notified_whatsapp=suppress,
        notified_email=suppress,
    )
    session.add(alert)
    await session.commit()
    await session.refresh(alert)

    log.info(
        "test_harness_alert_created",
        alert_id=alert.id,
        dam_id=alert.dam_id,
        severity=alert.severity,
        send_notification=payload.send_notification,
    )

    detail = (
        "alerta criado; sweep do dispatcher dispara em até 5min"
        if payload.send_notification
        else "alerta criado em modo silencioso (notified_* pré-marcado)"
    )
    return TestHarnessAlertResult(
        alert_id=alert.id,
        is_test=True,
        send_notification=payload.send_notification,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Modo B — forecast sintético
# ---------------------------------------------------------------------------

@router.post(
    "/forecasts",
    response_model=TestHarnessAlertResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_test_forecast(
    payload: TestForecastCreate,
    session: SessionDep,
    _: AuthUser,
) -> TestHarnessAlertResult:
    """Cria Forecast(is_test=True) e dispara check_and_create_alerts síncrono.

    Resposta inclui o `alert_id` se a precipitação cruzou o threshold do
    aggregator (severity ≥ 3 ajustado por dam_type/dpa). Caso contrário só
    o forecast é criado — útil pra testar limites de detecção.
    """
    dam = await session.get(Dam, payload.dam_id)
    if dam is None:
        raise HTTPException(status_code=404, detail="Dam not found")

    risk_level, risk_label, exceeded = compute_risk_score(
        payload.max_precipitation_mm, dam
    )

    forecast = Forecast(
        dam_id=payload.dam_id,
        forecast_date=payload.forecast_date,
        source="test_harness",
        max_precipitation_mm=payload.max_precipitation_mm,
        total_precipitation_mm=payload.max_precipitation_mm,
        weather_description="Forecast sintético (test harness)",
        risk_level=risk_level,
        risk_label=risk_label,
        alert_threshold_exceeded=exceeded,
        raw_data={"test_harness": {"injected_at": datetime.now(tz=timezone.utc).isoformat()}},
        is_test=True,
    )
    session.add(forecast)
    await session.flush()
    forecast_id = forecast.id

    # Pipeline de detecção: aggregator vê o forecast e cria alert se
    # risk_level >= 3. is_test=True propaga via aggregator (mudança em
    # check_and_create_alerts).
    created_alerts = await check_and_create_alerts(session, dam)
    await session.commit()

    if not created_alerts:
        log.info(
            "test_harness_forecast_below_threshold",
            forecast_id=forecast_id,
            risk_level=risk_level,
            precipitation_mm=payload.max_precipitation_mm,
        )
        return TestHarnessAlertResult(
            forecast_id=forecast_id,
            is_test=True,
            send_notification=payload.send_notification,
            detail=(
                f"forecast criado mas precipitação {payload.max_precipitation_mm:.0f} mm "
                f"não cruzou threshold (risk_level={risk_level}). "
                "Nenhum alerta gerado — aumente o valor pra exercitar pipeline."
            ),
        )

    # Quando o operador NÃO quer notif real, suprime nos alerts recém-criados.
    # Marca em loop ao invés de UPDATE bulk pra preservar logs claros.
    if not payload.send_notification:
        for alert in created_alerts:
            alert.notified_whatsapp = True
            alert.notified_email = True
        await session.commit()

    primary = created_alerts[0]
    log.info(
        "test_harness_forecast_created_alerts",
        forecast_id=forecast_id,
        alert_ids=[a.id for a in created_alerts],
        send_notification=payload.send_notification,
    )
    return TestHarnessAlertResult(
        alert_id=primary.id,
        forecast_id=forecast_id,
        is_test=True,
        send_notification=payload.send_notification,
        detail=(
            f"forecast criado e {len(created_alerts)} alerta(s) gerado(s) via aggregator. "
            f"{'Sweep dispara notificação em até 5min.' if payload.send_notification else 'Notif suprimida (notified_* pré-marcado).'}"
        ),
    )


# ---------------------------------------------------------------------------
# Listagem (UI mostra os últimos N)
# ---------------------------------------------------------------------------

@router.get("/alerts", response_model=list[AlertRead])
async def list_test_alerts(
    session: SessionDep,
    _: AuthUser,
    limit: int = Query(default=20, ge=1, le=200),
) -> list[Alert]:
    """Últimos N alerts com is_test=True, ordenados por created_at desc."""
    stmt = (
        select(Alert)
        .where(Alert.is_test.is_(True))
        .order_by(Alert.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# Limpeza
# ---------------------------------------------------------------------------

@router.delete("/data")
async def purge_test_data(
    request: Request,
    session: SessionDep,
    _: AuthUser,
    older_than_days: int = Query(default=7, ge=0, le=365),
    purge_all: bool = Query(
        default=False,
        description="Se true, ignora older_than_days e apaga TODOS os "
        "registros is_test=True (reset total ao estado pré-testes).",
    ),
) -> Response:
    """Hard delete de Alert/Forecast com is_test=True.

    Modo padrão: filtra por `older_than_days` (default 7d). Modo `purge_all=true`:
    apaga tudo que tem is_test=True ignorando idade — botão "reverter ao estado
    original" do menu /test-harness.
    """
    # synchronize_session=False: deixa o SQL executar server-side e não tenta
    # avaliar o WHERE em Python. Sem isso o ORM dá TypeError comparando
    # datetime naive vs aware quando timestamps vêm de drivers diferentes
    # (SQLite ⇄ Postgres). O custo é pequeno: a session vê stale rows até
    # o próximo refresh, e nesse endpoint não usamos os objetos depois.
    alert_conditions = [Alert.is_test.is_(True)]
    fc_conditions = [Forecast.is_test.is_(True)]
    if not purge_all:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=older_than_days)
        alert_conditions.append(Alert.created_at < cutoff)
        fc_conditions.append(Forecast.created_at < cutoff)

    alerts_result = await session.execute(
        delete(Alert).where(*alert_conditions),
        execution_options={"synchronize_session": False},
    )
    alerts_deleted = alerts_result.rowcount or 0

    fcs_result = await session.execute(
        delete(Forecast).where(*fc_conditions),
        execution_options={"synchronize_session": False},
    )
    fcs_deleted = fcs_result.rowcount or 0

    await session.commit()

    log.info(
        "test_harness_purged",
        purge_all=purge_all,
        older_than_days=older_than_days if not purge_all else None,
        alerts_deleted=alerts_deleted,
        forecasts_deleted=fcs_deleted,
    )

    body = TestHarnessPurgeResult(
        older_than_days=0 if purge_all else older_than_days,
        alerts_deleted=alerts_deleted,
        forecasts_deleted=fcs_deleted,
    )

    # Quando vem do menu HTMX, força refresh full da página pra a lista
    # lateral "Últimos testes" atualizar — sem isso o operador clica e
    # parece que nada aconteceu (a lista é renderizada server-side).
    # Clientes API/curl recebem JSON limpo (sem header HX-Request).
    if request.headers.get("HX-Request") == "true":
        return JSONResponse(
            content=body.model_dump(),
            headers={"HX-Refresh": "true"},
        )
    return JSONResponse(content=body.model_dump())
