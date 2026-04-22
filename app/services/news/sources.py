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


NEWS_SOURCES: list[NewsSource] = [
    # --- RSS feeds (preferidos) -------------------------------------------------
    NewsSource(
        key="agencia_brasil",
        name="Agência Brasil",
        strategy="rss",
        # Feed de "Geral" cobre desastres naturais, enchentes, meio ambiente.
        url_template="https://agenciabrasil.ebc.com.br/rss/geral/feed.xml",
        max_age_days=30,
    ),
    NewsSource(
        key="mpmg",
        name="MPMG — Ministério Público de MG",
        strategy="rss",
        url_template="https://www.mpmg.mp.br/rss.xml",
        max_age_days=60,
    ),
    # --- HTML search (Playwright) ----------------------------------------------
    # Para essas, a query é interpolada no URL e extraímos resultados.
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
