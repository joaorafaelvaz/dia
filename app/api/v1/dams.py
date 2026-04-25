"""Dam CRUD + nested resources (events, forecasts).

POST /dams: ao criar, dispara `fetch_climate_data_for_dam.delay(dam.id)` pra
forecasts aparecerem no dashboard em ~30s sem esperar o cron de 3h. O dispatch
é "best-effort": se Celery estiver fora ou broker indisponível, o endpoint
loga warning e prossegue (próximo beat alcança).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.dependencies import AuthUser, SessionDep
from app.models.client import Client
from app.models.dam import Dam
from app.models.event import ClimateEvent
from app.models.forecast import Forecast
from app.schemas.dam import DamCreate, DamRead, DamUpdate
from app.schemas.event import ClimateEventRead
from app.schemas.forecast import ForecastRead
from app.utils.audit import record_audit
from app.utils.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/dams", tags=["dams"])


@router.get("", response_model=list[DamRead])
async def list_dams(
    session: SessionDep,
    _: AuthUser,
    client_id: int | None = None,
    state: str | None = None,
    is_active: bool | None = None,
) -> list[Dam]:
    stmt = select(Dam).join(Client, Dam.client_id == Client.id)
    if client_id is not None:
        stmt = stmt.where(Dam.client_id == client_id)
    if state:
        stmt = stmt.where(Dam.state == state)
    if is_active is not None:
        stmt = stmt.where(Dam.is_active.is_(is_active))
    stmt = stmt.order_by(Client.name, Dam.name)
    return list((await session.execute(stmt)).scalars().all())


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_dam(
    payload: DamCreate, request: Request, session: SessionDep, user: AuthUser
) -> Response:
    """Cria barragem e dispara coleta climática inicial (best-effort).

    Em request HTMX, devolve HX-Redirect /dams/{id} pra o operador ver a
    barragem recém-criada com os primeiros forecasts (que chegam em ~30s).
    """
    client = await session.get(Client, payload.client_id)
    if client is None:
        raise HTTPException(
            status_code=404,
            detail=f"Cliente client_id={payload.client_id} não encontrado",
        )

    dam = Dam(**payload.model_dump())
    session.add(dam)
    await session.commit()
    await session.refresh(dam)

    # Dispatch async — Celery vai pegar quando worker estiver livre. Não
    # bloqueamos a resposta. Falhas de broker (Redis fora) viram warning
    # mas não derrubam a criação.
    try:
        from app.tasks import climate_tasks
        climate_tasks.fetch_climate_data_for_dam.delay(dam.id)
        log.info("dam_created_climate_dispatched", dam_id=dam.id, name=dam.name)
    except Exception as exc:
        log.warning(
            "dam_created_dispatch_failed",
            dam_id=dam.id,
            name=dam.name,
            error=str(exc),
        )

    await record_audit(
        session, user=user, action="dam.create",
        entity_type="dam", entity_id=dam.id,
        details={"name": dam.name, "client_id": dam.client_id, "state": dam.state},
    )

    out = DamRead.model_validate(dam)
    headers = (
        {"HX-Redirect": f"/dams/{dam.id}"}
        if request.headers.get("HX-Request") == "true"
        else {}
    )
    return JSONResponse(
        content=out.model_dump(mode="json"),
        status_code=status.HTTP_201_CREATED,
        headers=headers,
    )


@router.get("/{dam_id}", response_model=DamRead)
async def get_dam(dam_id: int, session: SessionDep, _: AuthUser) -> Dam:
    dam = await session.get(Dam, dam_id)
    if not dam:
        raise HTTPException(status_code=404, detail="Dam not found")
    return dam


@router.delete("/{dam_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dam(
    dam_id: int, request: Request, session: SessionDep, user: AuthUser
) -> Response:
    """Hard delete da barragem.

    Cascade: ClimateEvent, Forecast e Alert ligados via FK com
    `cascade='all, delete-orphan'` somem junto. Notificações já enviadas
    (n8n) não são afetadas — só o histórico no banco.

    Operador que quer parar de monitorar mas preservar histórico deve usar
    PATCH is_active=false ao invés de DELETE. Esse endpoint existe pra
    erros de cadastro e remoção de dados de teste.

    Em request HTMX, retorna HX-Refresh:true pra a página recarregar e a
    tabela do cliente refletir a remoção.
    """
    dam = await session.get(Dam, dam_id)
    if not dam:
        raise HTTPException(status_code=404, detail="Dam not found")
    dam_name = dam.name
    dam_client_id = dam.client_id
    await session.delete(dam)
    await session.commit()
    log.info("dam_deleted", dam_id=dam_id, name=dam_name)
    await record_audit(
        session, user=user, action="dam.delete",
        entity_type="dam", entity_id=dam_id,
        details={"name": dam_name, "client_id": dam_client_id},
    )

    if request.headers.get("HX-Request") == "true":
        return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"HX-Refresh": "true"})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{dam_id}")
async def update_dam(
    dam_id: int,
    payload: DamUpdate,
    request: Request,
    session: SessionDep,
    user: AuthUser,
) -> Response:
    dam = await session.get(Dam, dam_id)
    if not dam:
        raise HTTPException(status_code=404, detail="Dam not found")
    update_data = payload.model_dump(exclude_unset=True)
    if "client_id" in update_data:
        new_client = await session.get(Client, update_data["client_id"])
        if new_client is None:
            raise HTTPException(
                status_code=404,
                detail=f"Cliente client_id={update_data['client_id']} não encontrado",
            )
    for key, value in update_data.items():
        setattr(dam, key, value)
    await session.commit()
    await session.refresh(dam)

    # Distinção semântica: update vs deactivate. Operador no menu cliente
    # clica "Desativar" e isso dispara PATCH com {is_active: false}; pra
    # auditoria, vale rotular distinto pra busca futura por "quem desativou X".
    is_deactivation = update_data.get("is_active") is False
    is_reactivation = update_data.get("is_active") is True and len(update_data) == 1
    action = (
        "dam.deactivate" if is_deactivation
        else "dam.reactivate" if is_reactivation
        else "dam.update"
    )
    await record_audit(
        session, user=user, action=action,
        entity_type="dam", entity_id=dam.id,
        details={"changes": update_data},
    )

    out = DamRead.model_validate(dam)
    headers = (
        {"HX-Refresh": "true"}
        if request.headers.get("HX-Request") == "true"
        else {}
    )
    return JSONResponse(content=out.model_dump(mode="json"), headers=headers)


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
