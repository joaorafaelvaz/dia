"""Configuração declarativa das fontes de notícia monitoradas.

Cada fonte escolhe uma das estratégias:

- `rss`: baixa um feed RSS/Atom via httpx e filtra por palavras-chave presentes
  em título ou resumo. Mais rápido, sem browser. É o caminho feliz — use quando
  possível.
- `html_search`: abre uma URL de busca com Playwright (Chromium headless),
  aguarda seletor, extrai `<article>` / cartões. Mais caro e frágil — seletor
  quebra quando a página muda.

Feature-flag por fonte no `settings.news_source_*_enabled` permite desligar
uma fonte quebrada sem redeploy do código.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.config import settings

Strategy = Literal["rss", "html_search"]


@dataclass(frozen=True)
class NewsSource:
    key: str
    name: str
    strategy: Strategy
    url_template: str
    # Para html_search: seletor CSS que garante que a página de resultado carregou.
    ready_selector: str | None = None
    # Para html_search: seletor CSS que enumera os "cartões" de notícia.
    card_selector: str | None = None
    # Janela máxima de dias — descartamos notícias mais antigas.
    max_age_days: int = 30
    # Se `True`, exige que o artigo contenha alguma palavra-chave climática
    # (CLIMATE_HINTS). Para RSS genérico (feed institucional, últimas notícias)
    # isso filtra ruído. Para fontes onde a *query* já é específica (ex.:
    # Google Notícias busca "Barragem Alemães Ouro Preto"), o filtro passa a
    # descartar matches legítimos — marque `False` nesses casos.
    require_climate_hint: bool = True


NEWS_SOURCES: list[NewsSource] = [
    # --- Google Notícias RSS (fonte primária) ----------------------------------
    # Feed agregador estável há 15+ anos: uma busca textual retorna um feed
    # RSS por query. Cobre G1, Folha, UOL, Estado de Minas, Agência Brasil e
    # centenas de outros veículos automaticamente. Trocamos manutenção de
    # seletores CSS (quebram a cada redesign) por dependência num produto
    # Google estável. Operator `when:30d` limita por idade no próprio search.
    #
    # A query já é específica (nome da barragem + município), então não
    # precisamos filtrar por CLIMATE_HINTS — isso descartaria matches
    # legítimos que não mencionem "chuva/enchente" explicitamente no título.
    NewsSource(
        key="google_news",
        name="Google Notícias",
        strategy="rss",
        url_template=(
            "https://news.google.com/rss/search"
            "?q={query}+when:30d&hl=pt-BR&gl=BR&ceid=BR:pt"
        ),
        max_age_days=30,
        require_climate_hint=False,
    ),
    # --- RSS feeds diretos (placeholders / desativados) ------------------------
    NewsSource(
        key="agencia_brasil",
        name="Agência Brasil",
        strategy="rss",
        # EBC tem devolvido HTTP 500 consistente em todos os endpoints RSS
        # testados (/rss/geral, /rss/ultimasnoticias) — backend quebrado em
        # 2026-04. Mantemos placeholder + flag default False. Reative quando
        # um endpoint voltar a responder 200.
        url_template="",
        max_age_days=30,
    ),
    NewsSource(
        key="mpmg",
        name="MPMG — Ministério Público de MG",
        strategy="rss",
        # URL histórica (/rss.xml) passou a devolver 404 em 2026. Deixamos
        # placeholder vazio — a flag news_source_mpmg_enabled começa False.
        # Substitua por URL atual conhecida e ative o flag se descobrir.
        url_template="",
        max_age_days=60,
    ),
    # --- HTML search (Playwright) — descontinuados em favor do Google News -----
    # G1 e EM mudaram o markup das páginas de busca e nossos seletores CSS
    # pararam de casar (raw_cards=0 em todos os testes de 2026-04). Em vez de
    # manter scraper frágil, roteamos essas fontes pelo Google Notícias acima.
    # Mantemos as entradas para documentação + facilidade de reativar se
    # quisermos voltar ao scraping direto.
    NewsSource(
        key="g1",
        name="G1",
        strategy="html_search",
        url_template="https://g1.globo.com/busca/?q={query}&order=recent",
        ready_selector="div.widget--info",
        card_selector="div.widget--info",
        max_age_days=30,
    ),
    NewsSource(
        key="em",
        name="Estado de Minas",
        strategy="html_search",
        url_template="https://www.em.com.br/busca/?q={query}",
        ready_selector="article, div.results",
        card_selector="article",
        max_age_days=30,
    ),
    NewsSource(
        key="anm",
        name="ANM SIGBM",
        # A ANM não expõe RSS nem busca textual estável — por enquanto
        # o módulo está stubbed e esta entrada é apenas placeholder.
        strategy="html_search",
        url_template="https://app.anm.gov.br/SIGBM/Publico/{query}",
        max_age_days=365,
    ),
]


def _enabled_for(key: str) -> bool:
    """Reflete `settings.news_source_<key>_enabled`."""
    attr = f"news_source_{key}_enabled"
    return bool(getattr(settings, attr, False))


def active_sources() -> list[NewsSource]:
    """Retorna somente as fontes ativas segundo o .env."""
    return [s for s in NEWS_SOURCES if _enabled_for(s.key)]
