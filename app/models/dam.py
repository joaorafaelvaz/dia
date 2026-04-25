"""Dam — barragem monitorada."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.client import Client
    from app.models.event import ClimateEvent
    from app.models.forecast import Forecast


class Dam(Base):
    __tablename__ = "dams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    dam_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # "tailings" | "flood_control" | "hydropower" | "sediment" | other

    municipality: Mapped[str] = mapped_column(String(150), nullable=False)
    state: Mapped[str] = mapped_column(String(3), nullable=False)
    country: Mapped[str] = mapped_column(String(3), nullable=False, default="BR")

    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    anm_classification: Mapped[str | None] = mapped_column(String(5), nullable=True)
    cri: Mapped[str | None] = mapped_column(String(20), nullable=True)
    dpa: Mapped[str | None] = mapped_column(String(20), nullable=True)
    capacity_m3: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    # "active" | "decharacterizing" | "inactive"

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    client: Mapped[Client] = relationship(back_populates="dams", lazy="selectin")

    # passive_deletes=True diz "confia no DDL ON DELETE CASCADE pra apagar
    # os filhos" — sem isso, session.delete(dam) só dispara DELETE nos filhos
    # que já estão carregados em memória, deixando órfãos os que não foram
    # tocados nessa sessão. Em Postgres prod o CASCADE roda no banco;
    # em SQLite teste precisa de PRAGMA foreign_keys=ON (já habilitado).
    events: Mapped[list[ClimateEvent]] = relationship(
        back_populates="dam",
        cascade="all, delete-orphan",
        lazy="selectin",
        passive_deletes=True,
    )
    forecasts: Mapped[list[Forecast]] = relationship(
        back_populates="dam",
        cascade="all, delete-orphan",
        lazy="selectin",
        passive_deletes=True,
    )
    alerts: Mapped[list[Alert]] = relationship(
        back_populates="dam",
        cascade="all, delete-orphan",
        lazy="selectin",
        passive_deletes=True,
    )

    @property
    def owner_group(self) -> str:
        """Compat: locais de leitura ainda usam dam.owner_group como string.

        Antes era coluna; depois da migration 0004 vira proxy pra
        client.name. Quem precisa filtrar/ordenar SQL deve usar
        Client.name diretamente via JOIN.
        """
        return self.client.name if self.client else ""

    @property
    def client_name(self) -> str | None:
        """Helper pra schemas Pydantic (`DamRead.client_name`) lerem via
        from_attributes sem precisar JOIN explícito."""
        return self.client.name if self.client else None

    def __repr__(self) -> str:
        return f"<Dam id={self.id} name={self.name!r} client_id={self.client_id}>"
