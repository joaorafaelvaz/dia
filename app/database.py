"""Async SQLAlchemy engine + session factory + declarative Base.

Two session entry points:

- `SessionLocal` + `get_session`: shared pooled engine used by FastAPI. Safe
  because uvicorn runs a single persistent event loop.
- `task_session()`: fresh engine per invocation with NullPool, for Celery
  tasks that call `asyncio.run()`. Using the shared engine there would tie
  asyncpg connections to the first loop created, causing
  "Event loop is closed / attached to a different loop" on the second task.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: dict[Any, Any] = {}


engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=settings.app_debug,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Yields a session and ensures close."""
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@asynccontextmanager
async def task_session() -> AsyncIterator[AsyncSession]:
    """Per-Celery-task async session.

    Creates a disposable engine with `NullPool` so no connection object
    outlives the current `asyncio.run()` event loop. Without this, reusing
    the module-level `SessionLocal` across multiple `asyncio.run()` calls in
    the same worker process raises
      `RuntimeError: got Future attached to a different loop`
    during connection cleanup, because asyncpg binds low-level futures to
    the loop that created the connection.
    """
    task_engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        echo=settings.app_debug,
    )
    factory = async_sessionmaker(
        bind=task_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    try:
        async with factory() as session:
            yield session
    finally:
        await task_engine.dispose()
