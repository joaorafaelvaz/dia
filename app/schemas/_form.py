"""Helpers compartilhados pra schemas que recebem dados de forms HTML.

Forms HTML enviam string vazia ("") pra campos opcionais não preenchidos —
input number sem valor, select com option "—", etc. Pydantic não converte
"" pra None automaticamente: tenta parsear como float/int e falha com 422
(`unable to parse string`).

`empty_string_to_none` é um validator `mode="before"` que troca "" por
None antes do parsing acontecer. Aplicar nos schemas que vêm de forms
(DamBase, DamUpdate, ClientBase, ClientUpdate, etc.) preserva o tipo
declarado (`float | None`, `str | None`) sem precisar reescrever todas
as field declarations.
"""
from __future__ import annotations

from typing import Any


def empty_string_to_none(v: Any) -> Any:
    """Coerce string vazia / só whitespace pra None. Outros tipos passam direto."""
    if isinstance(v, str) and v.strip() == "":
        return None
    return v
