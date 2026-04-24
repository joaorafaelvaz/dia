"""Fixtures compartilhadas pela suite de smoke tests.

Scope intencional: **offline**. Toda a suite roda sem Postgres/Redis/Celery —
apenas SQLite in-memory + mocks do httpx e do Claude. Os fixtures aqui
focam em três eixos:

1. **Engine/sessão assíncrona** usando `sqlite+aiosqlite:///:memory:` com
   schema criado via `Base.metadata.create_all`. Uma engine por teste
   (scope="function") — in-memory não persiste entre conexões, então
   a cada teste temos banco limpo sem trabalho manual de truncate.
2. **Factories de modelo** (dam/event/forecast/alert) — `factory_boy`
   seria overkill aqui; funções simples que constroem instâncias com
   defaults sobrescrevíveis cobrem o caso.
3. **FastAPI TestClient async** com `httpx.AsyncClient` +
   `ASGITransport`. O `get_session` do app é sobrescrito via
   `app.dependency_overrides` pra usar a sessão do SQLite de teste.

Gotchas:
- `Base.metadata.create_all` precisa rodar numa conexão async (via
  `engine.begin()`) porque a declarative base aqui é async-native.
- `expire_on_commit=False` nas sessões do teste — sem isso as asserções
  de pós-commit quebram tentando fazer lazy-load numa sessão que a
  fixture já fechou.
- As `server_default=func.now()` em DateTime funcionam no SQLite
  (traduz pra CURRENT_TIMESTAMP), então os timestamps do banco
  populam sem configuração extra.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, BasicAuth
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.database import Base, get_session
from app.models.alert import Alert
from app.models.dam import Dam
from app.models.event import ClimateEvent
from app.models.forecast import Forecast


# Credenciais conhecidas para os testes de API — sobrescrevem o que
# estiver no settings antes de instanciar o client. `session`-scoped
# porque o valor não muda entre testes.
TEST_USER = "testuser"
TEST_PASS = "testpass"


@pytest.fixture(scope="session")
def event_loop():
    """Loop compartilhado na sessão.

    `pytest-asyncio` com `asyncio_mode=auto` cria um loop por teste
    por default — nossa fixture de engine ficaria então amarrada ao
    loop da função que a criou e quebraria quando reusada. Loop único
    de sessão evita isso e casa com como o app de verdade roda.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def async_engine():
    """Engine SQLite in-memory por teste, com schema criado."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        # SQLite in-memory + múltiplas conexões exige StaticPool — sem
        # isso cada conexão "vê" um banco diferente.
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncIterator[AsyncSession]:
    """Sessão async ligada à engine do teste."""
    factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Factories de domínio
# ---------------------------------------------------------------------------
#
# Dicts de defaults com sobrescrita via **kwargs. Prefiro isso a factory_boy
# porque são pouquíssimas factories e a semântica "override aqui e aqui"
# fica óbvia no teste.


def make_dam(
    *,
    name: str = "Barragem Teste",
    owner_group: str = "Gerdau",
    dam_type: str = "tailings",
    municipality: str = "Ouro Preto",
    state: str = "MG",
    latitude: float = -20.3855,
    longitude: float = -43.5036,
    dpa: str | None = "Alto",
    cri: str | None = "Média",
    anm_classification: str | None = "B",
    status: str = "active",
    is_active: bool = True,
    **overrides,
) -> Dam:
    kwargs = dict(
        name=name,
        owner_group=owner_group,
        dam_type=dam_type,
        municipality=municipality,
        state=state,
        latitude=latitude,
        longitude=longitude,
        dpa=dpa,
        cri=cri,
        anm_classification=anm_classification,
        status=status,
        is_active=is_active,
    )
    kwargs.update(overrides)
    return Dam(**kwargs)


def make_event(
    *,
    dam_id: int,
    event_date: date | None = None,
    event_type: str = "heavy_rain",
    severity: int = 3,
    severity_label: str = "Alto",
    title: str = "Precipitação elevada",
    description: str = "Evento de teste",
    source_type: str = "weather",
    source: str = "open_meteo_archive",
    precipitation_mm: float | None = 120.0,
    raw_data: dict | None = None,
    **overrides,
) -> ClimateEvent:
    kwargs = dict(
        dam_id=dam_id,
        event_date=event_date or date.today() - timedelta(days=5),
        event_type=event_type,
        severity=severity,
        severity_label=severity_label,
        title=title,
        description=description,
        source_type=source_type,
        source=source,
        precipitation_mm=precipitation_mm,
        raw_data=raw_data or {"open_meteo": {"precipitation_sum": precipitation_mm}},
    )
    kwargs.update(overrides)
    return ClimateEvent(**kwargs)


def make_forecast(
    *,
    dam_id: int,
    forecast_date: date | None = None,
    source: str = "open_meteo",
    max_precipitation_mm: float = 180.0,
    total_precipitation_mm: float | None = None,
    risk_level: int = 4,
    risk_label: str = "Muito Alto",
    alert_threshold_exceeded: bool = True,
    weather_code: int | None = 82,
    weather_description: str = "Aguaceiros violentos",
    **overrides,
) -> Forecast:
    kwargs = dict(
        dam_id=dam_id,
        forecast_date=forecast_date or date.today() + timedelta(days=2),
        source=source,
        max_precipitation_mm=max_precipitation_mm,
        total_precipitation_mm=total_precipitation_mm
        if total_precipitation_mm is not None
        else max_precipitation_mm,
        risk_level=risk_level,
        risk_label=risk_label,
        alert_threshold_exceeded=alert_threshold_exceeded,
        weather_code=weather_code,
        weather_description=weather_description,
        raw_data={"open_meteo": {"precipitation_sum": max_precipitation_mm}},
    )
    kwargs.update(overrides)
    return Forecast(**kwargs)


def make_alert(
    *,
    dam_id: int,
    alert_type: str = "forecast_warning",
    severity: int = 4,
    title: str = "Alerta Muito Alto",
    message: str = "Precipitação prevista alta",
    is_active: bool = True,
    forecast_date: date | None = None,
    expires_at: datetime | None = None,
    **overrides,
) -> Alert:
    kwargs = dict(
        dam_id=dam_id,
        alert_type=alert_type,
        severity=severity,
        title=title,
        message=message,
        is_active=is_active,
        forecast_date=forecast_date,
        expires_at=expires_at,
    )
    kwargs.update(overrides)
    return Alert(**kwargs)


@pytest_asyncio.fixture
async def sample_dam(async_session) -> Dam:
    """Barragem default pra testes que precisam de um FK válido."""
    dam = make_dam()
    async_session.add(dam)
    await async_session.commit()
    await async_session.refresh(dam)
    return dam


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def api_client(async_session, monkeypatch) -> AsyncIterator[AsyncClient]:
    """AsyncClient conectado ao ASGI do FastAPI com auth override pro banco de teste.

    - Força credenciais determinísticas (TEST_USER/TEST_PASS) via monkeypatch
      em `settings`, já que `require_basic_auth` lê direto de lá.
    - Sobrescreve `get_session` pra reusar a mesma sessão que o teste usa
      pra fazer setup de fixtures — sem isso o endpoint cria conexão nova
      e não enxerga o que o teste inseriu antes do request.
    """
    monkeypatch.setattr(settings, "basic_auth_user", TEST_USER)
    monkeypatch.setattr(settings, "basic_auth_pass", TEST_PASS)

    # Import tardio: importar `app.main` no topo do conftest carregaria
    # todas as rotas (incluindo reports→anthropic) antes dos fixtures de
    # API rodarem, potencialmente quebrando testes puramente de unidade.
    from app.main import app

    async def _override_get_session():
        yield async_session

    app.dependency_overrides[get_session] = _override_get_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            auth=BasicAuth(TEST_USER, TEST_PASS),
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest_asyncio.fixture
async def anon_api_client(async_session, monkeypatch) -> AsyncIterator[AsyncClient]:
    """Igual ao `api_client`, mas SEM enviar credenciais — pro teste de 401."""
    monkeypatch.setattr(settings, "basic_auth_user", TEST_USER)
    monkeypatch.setattr(settings, "basic_auth_pass", TEST_PASS)

    from app.main import app

    async def _override_get_session():
        yield async_session

    app.dependency_overrides[get_session] = _override_get_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def today() -> date:
    return date.today()


@pytest.fixture
def now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)
