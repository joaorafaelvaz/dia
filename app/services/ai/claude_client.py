"""Wrapper fino em volta do AsyncAnthropic que:

1. Loga cada chamada em `ai_usage` (tokens, custo, latência).
2. Retorna a string da resposta já extraída do TextBlock.
3. Propaga exceções, gravando linha com `error=<msg>` e cost_usd=0.

Todas as chamadas ao Claude passam por `complete()`. Não instanciar AsyncAnthropic
em outro lugar — senão os custos não aparecem no dashboard.
"""
from __future__ import annotations

import time
from typing import Iterable

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ai_usage import AIUsage
from app.utils.ai_costs import compute_cost_usd, is_known_model
from app.utils.logging import get_logger

log = get_logger(__name__)

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY não configurado no .env — nenhuma chamada IA possível."
            )
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


async def complete(
    *,
    session: AsyncSession,
    caller: str,
    system: str,
    prompt: str,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float | None = 0.2,
    extra_messages: Iterable[MessageParam] | None = None,
) -> str:
    """Executa uma chamada ao Claude e grava uso em ai_usage.

    Args:
        session: sessão SQLAlchemy usada para gravar a linha de AIUsage.
        caller: identificador do chamador ("news_classifier", "report_briefing", …).
        system: prompt de sistema.
        prompt: primeira mensagem do usuário.
        model: override; se None usa `settings.claude_model_classify` por default.
        max_tokens: passado direto à API.
        temperature: se `None`, omitimos o parâmetro da chamada — Opus 4.7+
            deprecou `temperature` e qualquer valor dispara 400. Mantemos
            default 0.2 para Haiku (classificação) onde ainda é aceito.
        extra_messages: mensagens adicionais (para few-shot).

    Returns:
        Texto bruto concatenado dos TextBlocks de resposta.
    """
    model = model or settings.claude_model_classify
    client = _get_client()

    messages: list[MessageParam] = [{"role": "user", "content": prompt}]
    if extra_messages:
        messages = [*extra_messages, *messages]

    if not is_known_model(model):
        log.warning("ai_model_unknown_pricing", model=model)

    t0 = time.perf_counter()
    usage = AIUsage(model=model, caller=caller)

    # Monta kwargs dinamicamente para poder omitir `temperature` quando None
    # (Opus 4.7 deprecou o parâmetro e rejeita com 400).
    create_kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if temperature is not None:
        create_kwargs["temperature"] = temperature

    try:
        msg = await client.messages.create(**create_kwargs)
        usage.input_tokens = msg.usage.input_tokens
        usage.output_tokens = msg.usage.output_tokens
        usage.cost_usd = compute_cost_usd(
            model, msg.usage.input_tokens, msg.usage.output_tokens
        )

        # Concatena blocos de texto — ignora tool_use/image se aparecerem.
        text = "".join(
            block.text for block in msg.content if getattr(block, "type", None) == "text"
        )
        return text
    except Exception as exc:
        usage.error = f"{type(exc).__name__}: {exc}"
        log.error("claude_call_failed", caller=caller, model=model, error=str(exc))
        raise
    finally:
        usage.latency_ms = int((time.perf_counter() - t0) * 1000)
        session.add(usage)
        # flush para liberar o ID — commit fica na task que orquestra a operação
        await session.flush()


async def record_cache_hit(
    *,
    session: AsyncSession,
    caller: str,
    model: str,
) -> None:
    """Grava linha sintética para medir cache hit rate (tokens=0, cost=0)."""
    session.add(
        AIUsage(model=model, caller=caller, cache_hit=True, latency_ms=0)
    )
    await session.flush()
