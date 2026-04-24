"""Smoke do context_builder + report_generator (mock Claude).

`build_context` é o pré-prompt — se ela quebra, os relatórios IA viram
hallucination ou ficam vazios. Cobrimos:

- Filtro por `min_event_severity` (padrão 2 → exclui ruído)
- Resolução de scope `gerdau`/`kinross` por owner_group
- `generate_briefing` com mock do Claude — confirma que o pipeline
  prompt → markdown → HTML termina escrevendo conteúdo no Report.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.ai import context_builder, report_generator

from tests.conftest import make_dam, make_event


@pytest.mark.asyncio
async def test_build_context_filters_low_severity_events(async_session, sample_dam):
    """`min_event_severity=2` (default) corta severity=1 — eventos triviais
    não inflam o prompt."""
    today = date.today()
    trivial = make_event(
        dam_id=sample_dam.id,
        event_date=today - timedelta(days=3),
        severity=1,
        severity_label="Baixo",
        title="Garoa",
    )
    relevant = make_event(
        dam_id=sample_dam.id,
        event_date=today - timedelta(days=2),
        severity=3,
        severity_label="Alto",
        title="Chuva forte",
    )
    async_session.add_all([trivial, relevant])
    await async_session.commit()

    ctx = await context_builder.build_context(
        async_session, scope="all", period_days=30, forecast_days=7
    )
    titles = [e.title for e in ctx.recent_events]
    assert "Chuva forte" in titles
    assert "Garoa" not in titles
    # `events_by_severity` não conta o trivial
    summary = ctx.to_dict()
    assert summary["event_count"] == 1
    assert "Alto" in summary["events_by_severity"]


@pytest.mark.asyncio
async def test_resolve_dam_ids_filters_by_owner_group(async_session):
    """`scope="gerdau"` retorna só dams com owner_group ilike 'gerdau'.

    Note: `resolve_dam_ids` exige `is_active=True` — dams inativas não
    entram em escopo nominal (regra explícita do builder).
    """
    g1 = make_dam(name="G1", owner_group="Gerdau", is_active=True)
    g2 = make_dam(name="G2", owner_group="gerdau", is_active=True)  # case-insensitive
    k1 = make_dam(name="K1", owner_group="Kinross", is_active=True)
    inactive = make_dam(name="GX", owner_group="Gerdau", is_active=False)
    async_session.add_all([g1, g2, k1, inactive])
    await async_session.commit()

    ids = await context_builder.resolve_dam_ids(async_session, scope="gerdau")
    # 2 ativas Gerdau, exclui inativa e Kinross
    assert len(ids) == 2
    assert g1.id in ids and g2.id in ids
    assert k1.id not in ids
    assert inactive.id not in ids


@pytest.mark.asyncio
async def test_generate_briefing_persists_html_when_claude_responds(
    async_session, sample_dam, monkeypatch
):
    """Pipeline end-to-end com Claude mockado: contexto → prompt → markdown
    → HTML. Confirma que o markdown vira HTML não-vazio (extensão `tables`
    do python-markdown está ativa) e que o `claude_client.complete` foi
    chamado com o sistema do briefing.
    """
    captured: dict = {}

    async def fake_complete(*, session, caller, system, prompt, model, max_tokens, temperature, extra_messages=None):
        captured["caller"] = caller
        captured["system"] = system
        captured["prompt"] = prompt
        return (
            "# Briefing — Teste\n\n"
            "## 1. Sumário\n"
            "Tudo OK.\n\n"
            "## 3. Previsões críticas\n\n"
            "| Barragem | Data | mm |\n"
            "|---|---|---|\n"
            "| Teste | 2026-04-25 | 120 |\n"
        )

    monkeypatch.setattr(report_generator.claude_client, "complete", fake_complete)

    ctx = await context_builder.build_context(
        async_session, scope="all", period_days=30, forecast_days=7
    )
    md, html = await report_generator.generate_briefing(
        async_session, ctx, title_suffix="Smoke", forecast_days=7
    )

    # markdown carrega o título do briefing; HTML traduz a tabela.
    assert "Briefing — Teste" in md
    assert "<h1>" in html
    assert "<table>" in html  # extensão `tables` produziu uma tabela
    # Confirma que o caller correto foi propagado pra contabilidade de custos
    assert captured["caller"] == "report_briefing"
    assert "Fractal Engenharia" in captured["system"]
