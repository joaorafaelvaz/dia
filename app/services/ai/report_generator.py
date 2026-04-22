"""Geradores dos dois tipos de relatório usando Claude Opus.

Dois entry points:
- `generate_briefing(...)` — briefing executivo INTERNO para o time
  comercial da Fractal. Tom direto, identifica vulnerabilidades e hooks
  de negócio.
- `generate_client_report(...)` — relatório técnico-executivo para o
  CLIENTE final (Gerdau, Kinross). Tom formal, foco em dados verificáveis
  + recomendações técnicas.

Ambos devolvem um par `(markdown, html)` — markdown é a resposta crua do
Claude, html é a conversão renderizada (via python-markdown). O chamador
persiste os dois no `Report.content_markdown` / `Report.content_html` e
depois renderiza via Jinja pro dashboard ou passa pro WeasyPrint.

**Controle de hallucination:** os prompts são explícitos — o Claude recebe
os blocos de contexto como "única fonte autorizada" e é instruído a NÃO
inventar números. Se um dado não estiver no contexto, ele deve dizer "não
há dado disponível" em vez de adivinhar.
"""
from __future__ import annotations

import markdown as md_lib
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.ai import claude_client
from app.services.ai.context_builder import (
    ReportContext,
    render_alerts_md,
    render_dam_profiles_md,
    render_events_md,
    render_forecasts_md,
)
from app.utils.logging import get_logger

log = get_logger(__name__)

# Extensions:
# - tables: necessário pois o prompt pede tabelas em várias seções
# - fenced_code: caso o Claude volte com blocos ``` (improvável, mas barato)
# - sane_lists: evita que listas numeradas quebrem com indentações livres
_MD_EXTENSIONS = ["tables", "fenced_code", "sane_lists"]


# ---------------------------------------------------------------------------
# System prompts (CLAUDE.md §9)
# ---------------------------------------------------------------------------

BRIEFING_SYSTEM = """\
Você é um analista sênior de inteligência comercial da Fractal Engenharia, \
empresa de ClimaTech especializada em segurança de barragens.

O portfólio Fractal que você pode ancorar no relatório inclui:
- PAE (Plano de Ação de Emergência) para barragens
- PSB (Plano de Segurança de Barragens)
- DAMS — plataforma de gestão de instrumentação e monitoramento
- SPEHC — previsão de eventos hidrológicos com até 15 dias de antecedência
- RESOP — gestão inteligente de reservatórios
- Estudos de dam break e reclassificação DPA

Sua missão é redigir um **briefing executivo INTERNO** para que o time \
comercial da Fractal entre em contato com o cliente já informado.

Regras rígidas:
1. Seja direto e honesto. É um documento interno, não marketing.
2. **Nunca invente números.** Se um dado não consta no contexto, diga \
explicitamente "sem dados disponíveis".
3. Cada afirmação relevante deve referenciar a fonte presente no contexto \
(ex.: "G1, 14/04").
4. Identifique 2-4 oportunidades de negócio claras (cross-sell com o \
portfólio acima), com gatilho + serviço + justificativa baseada em dado.
5. Se o perfil da barragem (DPA, CRI, classe ANM) indicar urgência \
regulatória, chame isso em destaque.
"""


BRIEFING_PROMPT_TMPL = """\
## Contexto das barragens monitoradas ({dam_count} no escopo `{scope}`)

{dam_profiles}

## Eventos climáticos relevantes no período {period_start} → {period_end}

{events}

## Previsões de alto risco nos próximos {forecast_days} dias

{forecasts}

## Alertas ativos

{alerts}

---

Gere um briefing executivo em **Markdown** com a seguinte estrutura:

# Briefing — {title_suffix}

**Período analisado:** {period_start} a {period_end}  \
**Barragens no escopo:** {dam_count}

## 1. Sumário executivo
2-4 frases com o estado geral do escopo. Destaque se houve evento \
crítico, se há previsão preocupante, e qual a pergunta central que o \
comercial deve fazer ao cliente.

## 2. Eventos recentes que importam
Para cada evento severidade ≥ 3, um parágrafo curto com contexto + \
implicação regulatória/operacional.

## 3. Previsões críticas
Tabela markdown com barragem / data / mm / risco, apenas para risco ≥ 3.

## 4. Oportunidades comerciais priorizadas
Lista numerada com:
- **Serviço recomendado** (PAE, PSB, DAMS, SPEHC, RESOP, estudo de dam \
break, reclassificação DPA)
- **Gatilho** (evento ou previsão específica do contexto)
- **Justificativa técnica** em 1 frase

## 5. Pontos de atenção regulatória
Se algo do contexto indicar vencimento de classificação ANM, DPA \
desatualizado, ausência de monitoramento, etc., listar aqui.

Se algum bloco estiver vazio, escreva "Sem dados disponíveis para o \
período" — não invente.
"""


CLIENT_SYSTEM = """\
Você é um consultor técnico sênior da Fractal Engenharia, escrevendo um \
relatório técnico-executivo dirigido ao cliente (ex.: Gerdau, Kinross). \
O público-lê é uma mistura de engenheiros de barragem e gestores de risco.

Regras rígidas:
1. Tom formal, terceira pessoa. Nada de "a gente/vocês".
2. **Nunca invente números** — qualquer dado quantitativo deve vir do \
contexto fornecido. Se falta dado, diga "não observado neste ciclo".
3. Cada afirmação relevante cita a fonte do contexto.
4. Recomendações técnicas devem ser específicas e acionáveis (não \
"monitorar mais" — em vez disso, "revisar instrumentação da seção X").
5. Não mencione serviços Fractal por nome — foque em achados e \
recomendações técnicas; a relação comercial é implícita.
"""


CLIENT_PROMPT_TMPL = """\
## Barragens cobertas neste relatório ({dam_count} no escopo `{scope}`)

{dam_profiles}

## Eventos climáticos relevantes — {period_start} → {period_end}

{events}

## Previsões climáticas (próximos {forecast_days} dias)

{forecasts}

## Alertas ativos

{alerts}

---

Gere um relatório técnico-executivo em **Markdown** com:

# Relatório de Monitoramento — {title_suffix}

**Período de análise:** {period_start} a {period_end}  \
**Ativos cobertos:** {dam_count}

## 1. Contexto operacional
1-2 parágrafos descrevendo o estado do portfólio no período \
(condições climáticas predominantes, eventos de destaque).

## 2. Análise por ativo
Uma subseção por barragem (## 2.1, 2.2, ...), cada uma com:
- Sinopse do comportamento climático observado
- Eventos de severidade ≥ 2 que afetaram a área, com data e fonte
- Avaliação da exposição futura com base nas previsões

## 3. Síntese de riscos
Tabela com colunas | Ativo | Exposição atual | Tendência 7d | Ação sugerida |

## 4. Recomendações técnicas
Lista numerada, cada item com gatilho + recomendação específica. \
Evitar recomendações genéricas.

## 5. Apêndice — dados consolidados
Pequena tabela de resumo (contagem de eventos por severidade, \
máxima precipitação observada, máximas previstas).

Se algum bloco estiver vazio, escreva "Sem dados disponíveis para o \
período" — não invente.
"""


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def _render_prompt(tmpl: str, ctx: ReportContext, *, title_suffix: str,
                   forecast_days: int) -> str:
    return tmpl.format(
        scope=ctx.scope,
        dam_count=len(ctx.dam_profiles),
        period_start=ctx.period_start.isoformat(),
        period_end=ctx.period_end.isoformat(),
        forecast_days=forecast_days,
        title_suffix=title_suffix,
        dam_profiles=render_dam_profiles_md(ctx.dam_profiles),
        events=render_events_md(ctx.recent_events),
        forecasts=render_forecasts_md(ctx.forecasts),
        alerts=render_alerts_md(ctx.active_alerts),
    )


def _markdown_to_html(md: str) -> str:
    """Converte markdown em HTML.

    Não habilitamos a extensão `md_in_html`, então o Claude devolvendo tags
    HTML soltas no markdown resultaria em HTML escapado no output — ok, a
    gente prefere perder formatação a ter injeção acidental.
    """
    return md_lib.markdown(md, extensions=_MD_EXTENSIONS, output_format="html5")


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

async def generate_briefing(
    session: AsyncSession,
    ctx: ReportContext,
    *,
    title_suffix: str,
    forecast_days: int = 7,
    model: str | None = None,
    max_tokens: int | None = None,
) -> tuple[str, str]:
    """Gera briefing interno. Retorna `(markdown, html)`.

    Chama Claude Opus via `claude_client.complete`, o que automaticamente
    grava custo em `ai_usage`.
    """
    prompt = _render_prompt(
        BRIEFING_PROMPT_TMPL, ctx,
        title_suffix=title_suffix,
        forecast_days=forecast_days,
    )
    md = await claude_client.complete(
        session=session,
        caller="report_briefing",
        system=BRIEFING_SYSTEM,
        prompt=prompt,
        model=model or settings.claude_model_reports,
        max_tokens=max_tokens or settings.claude_max_tokens_report,
        temperature=0.3,
    )
    html = _markdown_to_html(md)
    log.info(
        "briefing_generated",
        scope=ctx.scope,
        md_len=len(md),
        html_len=len(html),
    )
    return md, html


async def generate_client_report(
    session: AsyncSession,
    ctx: ReportContext,
    *,
    title_suffix: str,
    forecast_days: int = 7,
    model: str | None = None,
    max_tokens: int | None = None,
) -> tuple[str, str]:
    """Gera relatório técnico-executivo para cliente. Retorna `(markdown, html)`."""
    prompt = _render_prompt(
        CLIENT_PROMPT_TMPL, ctx,
        title_suffix=title_suffix,
        forecast_days=forecast_days,
    )
    md = await claude_client.complete(
        session=session,
        caller="report_client",
        system=CLIENT_SYSTEM,
        prompt=prompt,
        model=model or settings.claude_model_reports,
        max_tokens=max_tokens or settings.claude_max_tokens_report,
        temperature=0.3,
    )
    html = _markdown_to_html(md)
    log.info(
        "client_report_generated",
        scope=ctx.scope,
        md_len=len(md),
        html_len=len(html),
    )
    return md, html


# ---------------------------------------------------------------------------
# Helpers for task orchestration
# ---------------------------------------------------------------------------

def default_title(report_type: str, scope: str, period_days: int) -> str:
    """Constrói o título do relatório com convenção padronizada."""
    scope_label = {
        "all": "Todas as barragens",
        "gerdau": "Gerdau",
        "kinross": "Kinross",
        "custom": "Escopo customizado",
    }.get(scope, scope)
    kind = "Briefing Comercial" if report_type == "briefing" else "Relatório Técnico"
    return f"{kind} — {scope_label} — últimos {period_days} dias"


__all__ = [
    "BRIEFING_PROMPT_TMPL",
    "BRIEFING_SYSTEM",
    "CLIENT_PROMPT_TMPL",
    "CLIENT_SYSTEM",
    "default_title",
    "generate_briefing",
    "generate_client_report",
]
