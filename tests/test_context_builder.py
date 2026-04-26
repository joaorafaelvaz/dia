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

from tests.conftest import make_client, make_dam, make_event


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
async def test_resolve_dam_ids_filters_by_client_name(async_session):
    """`scope="gerdau"` retorna só dams cujo Client.name ilike 'gerdau'.

    Pós-migration 0004 o filtro vira JOIN em Client.name (case-insensitive
    via ilike). `resolve_dam_ids` ainda exige `is_active=True` em Dam.
    """
    gerdau = make_client(name="Gerdau")
    kinross = make_client(name="Kinross")
    async_session.add_all([gerdau, kinross])
    await async_session.commit()
    await async_session.refresh(gerdau)
    await async_session.refresh(kinross)

    g1 = make_dam(name="G1", client_id=gerdau.id, is_active=True)
    g2 = make_dam(name="G2", client_id=gerdau.id, is_active=True)
    k1 = make_dam(name="K1", client_id=kinross.id, is_active=True)
    inactive = make_dam(name="GX", client_id=gerdau.id, is_active=False)
    async_session.add_all([g1, g2, k1, inactive])
    await async_session.commit()

    ids = await context_builder.resolve_dam_ids(async_session, scope="gerdau")
    # 2 ativas Gerdau, exclui inativa e Kinross
    assert len(ids) == 2
    assert g1.id in ids and g2.id in ids
    assert k1.id not in ids
    assert inactive.id not in ids


# ---------------------------------------------------------------------------
# #11 — Regressão pós-refactor 0004 (owner_group string → Client FK)
# ---------------------------------------------------------------------------
#
# Cenário real: operador renomeia "Gerdau" → "Gerdau Aços Longos S.A." pelo
# menu Cliente. Próximo briefing semanal (cron) deve pegar o nome novo no
# contexto IA. Antes do 0004 isso seria UPDATE em N rows de `dams.owner_group`;
# depois do 0004 é UPDATE de 1 row em `clients.name` e a property
# `Dam.owner_group` lê via JOIN/relationship — risco zero em tese, mas se
# alguém um dia adicionar cache desnormalizado em Dam, esses testes pegam.


@pytest.mark.asyncio
async def test_build_context_reflects_renamed_client_in_dam_profiles(
    api_client, async_session
):
    """PATCH /clients/{id} com nome novo → próximo build_context retorna
    DamProfile.owner_group com o nome atualizado, não o antigo.
    """
    # Setup inicial
    client = make_client(name="Empresa Antiga")
    async_session.add(client)
    await async_session.commit()
    await async_session.refresh(client)

    dam = make_dam(name="Barragem X", client_id=client.id, is_active=True)
    async_session.add(dam)
    await async_session.commit()

    # Briefing antes do rename
    ctx_before = await context_builder.build_context(
        async_session, scope="all", period_days=30, forecast_days=7
    )
    profiles_before = {p.name: p.owner_group for p in ctx_before.dam_profiles}
    assert profiles_before["Barragem X"] == "Empresa Antiga"

    # Rename via API real (passa por endpoint, valida flow completo)
    resp = await api_client.patch(
        f"/api/v1/clients/{client.id}", json={"name": "Empresa Nova S.A."}
    )
    assert resp.status_code == 200

    # Briefing depois — DEVE pegar o nome novo, não o cached.
    # Expira a session pra evitar identity map mascarar o teste em SQLite.
    async_session.expire_all()
    ctx_after = await context_builder.build_context(
        async_session, scope="all", period_days=30, forecast_days=7
    )
    profiles_after = {p.name: p.owner_group for p in ctx_after.dam_profiles}
    assert profiles_after["Barragem X"] == "Empresa Nova S.A."


@pytest.mark.asyncio
async def test_render_dam_profiles_md_reflects_renamed_client(
    api_client, async_session
):
    """O markdown que entra no prompt do Claude usa o nome novo.

    `render_dam_profiles_md` formata "### {dam.name} — {dam.owner_group}".
    Confirma que após rename o markdown não vaza o nome antigo pro
    contexto IA — Opus 4.7 num briefing semanal vê 'Empresa Nova', não
    'Empresa Antiga'.
    """
    client = make_client(name="Antiga Mineração")
    async_session.add(client)
    await async_session.commit()
    await async_session.refresh(client)
    dam = make_dam(name="Barragem dos Alemães", client_id=client.id)
    async_session.add(dam)
    await async_session.commit()

    # Rename
    resp = await api_client.patch(
        f"/api/v1/clients/{client.id}", json={"name": "Nova Mineração Ltda"}
    )
    assert resp.status_code == 200
    async_session.expire_all()

    ctx = await context_builder.build_context(
        async_session, scope="all", period_days=30
    )
    md = context_builder.render_dam_profiles_md(ctx.dam_profiles)
    assert "Nova Mineração Ltda" in md
    assert "Antiga Mineração" not in md


@pytest.mark.asyncio
async def test_resolve_dam_ids_uses_current_client_name_after_rename(
    api_client, async_session
):
    """Filtro `scope="novo_nome"` funciona após rename.

    Cron do relatório-cliente mensal usa `_owner_groups()` pra descobrir
    nomes ativos e dispara `generate_report(scope=name.lower())`. Se um
    cliente foi renomeado, o filtro novo (`Client.name.ilike(scope)`)
    deve continuar achando dams ativas.

    Cobre também o ramo negativo: scope com nome ANTIGO não retorna nada.
    """
    client = make_client(name="Cliente Original")
    async_session.add(client)
    await async_session.commit()
    await async_session.refresh(client)
    dam = make_dam(name="D1", client_id=client.id, is_active=True)
    async_session.add(dam)
    await async_session.commit()
    await async_session.refresh(dam)
    dam_id = dam.id

    # Rename
    resp = await api_client.patch(
        f"/api/v1/clients/{client.id}", json={"name": "Cliente Renomeado"}
    )
    assert resp.status_code == 200
    async_session.expire_all()

    # Nome novo encontra
    ids_new = await context_builder.resolve_dam_ids(
        async_session, scope="cliente renomeado"
    )
    assert dam_id in ids_new

    # Nome antigo não encontra mais (case-insensitive ilike no nome novo)
    ids_old = await context_builder.resolve_dam_ids(
        async_session, scope="cliente original"
    )
    assert dam_id not in ids_old


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
