"""Client CRUD — usado pelo menu /clients e referenciado por dams.client_id."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.dependencies import AuthUser, SessionDep
from app.models.client import Client
from app.models.dam import Dam
from app.schemas.client import ClientCreate, ClientRead, ClientUpdate

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("", response_model=list[ClientRead])
async def list_clients(
    session: SessionDep,
    _: AuthUser,
    is_active: bool | None = None,
    search: str | None = Query(default=None, min_length=1, max_length=80),
) -> list[ClientRead]:
    """Lista clientes com contagem de dams. Filtros por status e busca por nome."""
    stmt = (
        select(Client, func.count(Dam.id).label("dam_count"))
        .outerjoin(Dam, Dam.client_id == Client.id)
        .group_by(Client.id)
        .order_by(Client.name)
    )
    if is_active is not None:
        stmt = stmt.where(Client.is_active.is_(is_active))
    if search:
        stmt = stmt.where(Client.name.ilike(f"%{search}%"))
    rows = (await session.execute(stmt)).all()

    out: list[ClientRead] = []
    for client, dam_count in rows:
        item = ClientRead.model_validate(client)
        item.dam_count = int(dam_count or 0)
        out.append(item)
    return out


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_client(
    payload: ClientCreate, request: Request, session: SessionDep, _: AuthUser
) -> Response:
    client = Client(**payload.model_dump())
    session.add(client)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Já existe um cliente com nome '{payload.name}'",
        )
    await session.refresh(client)
    out = ClientRead.model_validate(client)
    out.dam_count = 0

    # Vindo do form HTMX → redireciona pra /clients pra ver a lista atualizada.
    # API pura (curl/SDK) recebe JSON.
    headers = (
        {"HX-Redirect": "/clients"}
        if request.headers.get("HX-Request") == "true"
        else {}
    )
    return JSONResponse(
        content=out.model_dump(mode="json"),
        status_code=status.HTTP_201_CREATED,
        headers=headers,
    )


@router.get("/{client_id}", response_model=ClientRead)
async def get_client(client_id: int, session: SessionDep, _: AuthUser) -> ClientRead:
    client = await session.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    dam_count_stmt = select(func.count(Dam.id)).where(Dam.client_id == client_id)
    dam_count = (await session.execute(dam_count_stmt)).scalar_one()
    out = ClientRead.model_validate(client)
    out.dam_count = int(dam_count or 0)
    return out


@router.patch("/{client_id}", response_model=ClientRead)
async def update_client(
    client_id: int,
    payload: ClientUpdate,
    session: SessionDep,
    _: AuthUser,
) -> ClientRead:
    client = await session.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(client, key, value)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Conflito de nome — já existe outro cliente com esse name",
        )
    await session.refresh(client)
    out = ClientRead.model_validate(client)
    return out


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(
    client_id: int, session: SessionDep, _: AuthUser
) -> Response:
    """Hard delete só se não houver dams associadas. Senão 409.

    Operador que quer parar de monitorar mas preservar histórico deve usar
    PATCH com is_active=false ao invés de DELETE.
    """
    client = await session.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    dam_count = (
        await session.execute(
            select(func.count(Dam.id)).where(Dam.client_id == client_id)
        )
    ).scalar_one()
    if dam_count:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cliente tem {dam_count} barragem(ns) associada(s). "
                "Reasocie ou apague as barragens primeiro, ou desative o "
                "cliente via PATCH (is_active=false)."
            ),
        )

    await session.delete(client)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
