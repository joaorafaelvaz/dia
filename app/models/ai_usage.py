"""AIUsage — loga cada chamada ao Claude (tokens, custo, latência, caller)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AIUsage(Base):
    """Uma linha por chamada à Anthropic API. Usada para:

    1. Dashboard de custos acumulados (24h / 7d / 30d).
    2. Circuit breaker orçamentário em Fase 2+ (se ultrapassar limite mensal, desliga IA).
    3. Debug de picos de consumo (qual caller, qual barragem disparou).
    """

    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    model: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    caller: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    # "news_classifier" | "report_briefing" | "report_client" | "event_analyzer" | ...

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Hit de cache Redis (resposta reutilizada sem bater na API) → gravamos linha
    # com tokens=0/cost=0 apenas para métrica de cache hit rate.
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    def __repr__(self) -> str:
        return (
            f"<AIUsage {self.model} caller={self.caller} "
            f"in={self.input_tokens} out={self.output_tokens} ${self.cost_usd:.4f}>"
        )
