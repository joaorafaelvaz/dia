"""Pricing table + cost math para Claude API.

Preços hardcoded por milhão de tokens (USD). Atualizar quando a Anthropic
anunciar mudanças. Se chamarmos um modelo não listado, caímos em `UNKNOWN`
e logamos warning — evita silenciar um modelo novo com custo estimado zero.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    input_per_mtok: float   # USD por 1_000_000 input tokens
    output_per_mtok: float  # USD por 1_000_000 output tokens


# Preços referência (final de 2025 / início de 2026).
# Fonte: https://www.anthropic.com/pricing
_PRICING: dict[str, ModelPricing] = {
    # Opus 4.x — usado em relatórios
    "claude-opus-4-7":              ModelPricing(input_per_mtok=15.0, output_per_mtok=75.0),
    "claude-opus-4-5":              ModelPricing(input_per_mtok=15.0, output_per_mtok=75.0),
    # Haiku 4.x — usado em classificação rápida
    "claude-haiku-4-5-20251001":    ModelPricing(input_per_mtok=1.0,  output_per_mtok=5.0),
    "claude-haiku-4-5":             ModelPricing(input_per_mtok=1.0,  output_per_mtok=5.0),
    # Sonnet como fallback/teste
    "claude-sonnet-4-5":            ModelPricing(input_per_mtok=3.0,  output_per_mtok=15.0),
}

_UNKNOWN = ModelPricing(input_per_mtok=0.0, output_per_mtok=0.0)


def compute_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calcula custo em dólares para uma chamada Claude.

    Prefixos são aceitos: `claude-haiku-4-5-20251001` bate em `claude-haiku-4-5-*`
    porque passamos tanto o ID datado quanto a família sem data na tabela.
    """
    pricing = _PRICING.get(model, _UNKNOWN)
    return (
        (input_tokens / 1_000_000.0) * pricing.input_per_mtok
        + (output_tokens / 1_000_000.0) * pricing.output_per_mtok
    )


def is_known_model(model: str) -> bool:
    return model in _PRICING
