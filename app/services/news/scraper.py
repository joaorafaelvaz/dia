"""Scraper de notícias multi-estratégia (RSS + Playwright).

API pública:

    articles = await fetch_articles_for_dam(dam)

Retorna lista de `Article` (dict) com título, URL, lead, data, fonte.

Design:
- **Deduplicação** via Redis: `news:seen:<sha1(url)>` com TTL 30d.
  Antes de retornar um artigo, marcamos como visto — garante que a mesma
  URL não reentrará no classificador duas vezes.
- **Queries** geradas a partir da barragem (nome, município, grupo).
- **Retries silenciosos** — se uma fonte quebra (404, seletor não encontrado),
  logamos warning e continuamos com as outras. Nunca derrubamos a task.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus

import feedparser
import httpx
from redis.asyncio import Redis

from app.config import settings
from app.models.dam import Dam
from app.services.news.sources import NEWS_SOURCES, NewsSource, active_sources
from app.utils.logging import get_logger

log = get_logger(__name__)

SEEN_KEY_PREFIX = "news:seen:"
SEEN_TTL_SECONDS = 30 * 24 * 3600


@dataclass
class Article:
    url: str
    title: str
    lead: str
    published_at: datetime | None
    source_key: str
    source_name: str
    query: str
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------

# Palavras que sugerem risco relacionado a barragem / evento climático extremo.
# Se nenhuma aparecer no artigo, provavelmente é ruído institucional.
CLIMATE_HINTS = (
    "chuva", "chuvas", "enchente", "alagamento", "inundação", "inundacao",
    "barragem", "rejeito", "deslizamento", "tromba", "desastre",
    "alerta", "temporal", "tempestade", "cedeu", "rompimento",
)


def build_queries(dam: Dam) -> list[str]:
    """Gera 2-4 queries distintas que buscam diferentes ângulos da notícia.

    Evita queries redundantes para não gastar requests à toa.
    """
    qs = {
        f"{dam.name} {dam.municipality}",
        f"barragem {dam.municipality} {dam.state}",
        f"{dam.municipality} chuvas enchente",
    }
    if dam.owner_group and dam.owner_group.lower() not in {"outro", "other"}:
        qs.add(f"{dam.owner_group} barragem {dam.municipality}")
    return sorted(qs)


# ---------------------------------------------------------------------------
# Dedup via Redis
# ---------------------------------------------------------------------------

def _seen_key(url: str) -> str:
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return SEEN_KEY_PREFIX + h


async def _filter_unseen(
    redis: Redis, articles: list[Article]
) -> list[Article]:
    """Remove artigos cuja URL já foi processada nos últimos 30 dias."""
    if not articles:
        return []
    keys = [_seen_key(a.url) for a in articles]
    # MGET suporta * operator — um único round-trip
    existing = await redis.mget(*keys)
    return [a for a, seen in zip(articles, existing, strict=False) if not seen]


async def mark_seen(redis: Redis, url: str) -> None:
    await redis.setex(_seen_key(url), SEEN_TTL_SECONDS, "1")


# ---------------------------------------------------------------------------
# RSS fetcher
# ---------------------------------------------------------------------------

def _article_from_rss_entry(
    entry: Any, source: NewsSource, query: str
) -> Article | None:
    """Mapeia um entry do feedparser para nosso Article.

    Para Google News, o entry vem com um sub-elemento `<source url="...">Nome</source>`
    que identifica o veículo real (G1, EM, Folha…). Preservamos em
    `source_name` para exibir no dashboard — útil para o usuário entender
    a origem mesmo sem clicar no link.
    """
    url = getattr(entry, "link", None)
    title = getattr(entry, "title", None)
    if not url or not title:
        return None

    lead = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
    lead = re.sub(r"<[^>]+>", " ", lead).strip()[:500]

    pub = None
    if getattr(entry, "published_parsed", None):
        pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    elif getattr(entry, "updated_parsed", None):
        pub = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)

    # Google News: sub-elemento <source> traz o publisher real.
    display_name = source.name
    entry_source = getattr(entry, "source", None)
    if entry_source:
        # feedparser expõe como dict-like: {"href": ..., "title": "G1"}
        pub_name = None
        if isinstance(entry_source, dict):
            pub_name = entry_source.get("title") or entry_source.get("value")
        else:
            pub_name = getattr(entry_source, "title", None) or str(entry_source)
        if pub_name:
            display_name = f"{source.name} — {pub_name}"

    return Article(
        url=url,
        title=title.strip(),
        lead=lead,
        published_at=pub,
        source_key=source.key,
        source_name=display_name,
        query=query,
        raw={"summary": lead},
    )


def _matches_query(art: Article, query: str) -> bool:
    """Filtro rudimentar: alguma palavra da query precisa bater em título/lead."""
    haystack = f"{art.title} {art.lead}".lower()
    # usa tokens >= 4 letras pra evitar matchar "de/do/em"
    tokens = [t for t in re.findall(r"\w+", query.lower()) if len(t) >= 4]
    if not tokens:
        return True
    return any(t in haystack for t in tokens)


def _has_climate_hint(art: Article) -> bool:
    haystack = f"{art.title} {art.lead}".lower()
    return any(hint in haystack for hint in CLIMATE_HINTS)


async def _fetch_one_rss_url(
    client: httpx.AsyncClient, url: str, source_key: str
) -> feedparser.FeedParserDict | None:
    """Baixa + parseia um feed RSS. Retorna None em qualquer falha (logada)."""
    try:
        resp = await client.get(url)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("news_rss_fetch_failed", source=source_key, url=url, error=str(exc))
        return None
    parsed = feedparser.parse(resp.content)
    return parsed


async def _fetch_rss(
    source: NewsSource, queries: list[str], now: datetime
) -> list[Article]:
    """Baixa RSS e filtra por queries + (opcionalmente) pistas climáticas.

    Dois modos, escolhidos pela presença de `{query}` no `url_template`:

    - **Feed estático** (ex.: `/rss.xml` de um veículo): baixamos uma vez e
      filtramos os entries localmente contra todas as queries da barragem.
    - **Busca RSS** (ex.: Google News `/rss/search?q=...`): fazemos uma
      request por query, pois cada feed já vem pré-filtrado pelo provedor.

    `source.require_climate_hint=False` desabilita o filtro de palavras-chave
    — útil quando a query já é específica o bastante (Google News com o nome
    da barragem no q).
    """
    if not source.url_template:
        # Fonte desabilitada implicitamente (URL vazia = aguardando descobrir
        # endpoint estável). Mantemos silencioso para não poluir logs.
        return []

    cutoff = now - timedelta(days=source.max_age_days)
    out: list[Article] = []
    seen_urls: set[str] = set()

    async with httpx.AsyncClient(
        timeout=15.0,
        follow_redirects=True,
        headers={
            # User-Agent explícito: Google News 403a default do httpx.
            "User-Agent": (
                "Mozilla/5.0 (compatible; DIA-bot/1.0; "
                "+https://dia.linkwise.digital)"
            )
        },
    ) as client:
        if "{query}" in source.url_template:
            # Uma request por query — cada feed já vem filtrado.
            for q in queries:
                url = source.url_template.format(query=quote_plus(q))
                parsed = await _fetch_one_rss_url(client, url, source.key)
                if not parsed or not parsed.entries:
                    continue
                for entry in parsed.entries:
                    art = _article_from_rss_entry(entry, source, q)
                    if not art:
                        continue
                    if art.url in seen_urls:
                        continue
                    if art.published_at and art.published_at < cutoff:
                        continue
                    if source.require_climate_hint and not _has_climate_hint(art):
                        continue
                    seen_urls.add(art.url)
                    out.append(art)
        else:
            # Feed estático — baixamos uma vez e cruzamos com todas as queries.
            parsed = await _fetch_one_rss_url(client, source.url_template, source.key)
            if not parsed or not parsed.entries:
                log.info("news_rss_fetched", source=source.key, candidates=0)
                return []
            for entry in parsed.entries:
                for q in queries:
                    art = _article_from_rss_entry(entry, source, q)
                    if not art:
                        continue
                    if art.url in seen_urls:
                        continue
                    if art.published_at and art.published_at < cutoff:
                        continue
                    if not _matches_query(art, q):
                        continue
                    if source.require_climate_hint and not _has_climate_hint(art):
                        continue
                    seen_urls.add(art.url)
                    out.append(art)
                    break  # bastou uma query casar — não duplica

    log.info("news_rss_fetched", source=source.key, candidates=len(out))
    return out


# ---------------------------------------------------------------------------
# Playwright (HTML search)
# ---------------------------------------------------------------------------

async def _fetch_html_search(
    source: NewsSource, queries: list[str], now: datetime
) -> list[Article]:
    """Abre a página de busca em Chromium headless e extrai cards.

    Import do Playwright é lazy — se o pacote/binário não estiver disponível,
    a fonte é pulada com warning (não derruba a task inteira).
    """
    if not source.url_template or "{query}" not in source.url_template:
        return []

    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        log.warning("news_playwright_missing", source=source.key)
        return []

    articles: list[Article] = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120 Safari/537.36 DIA-bot"
                    )
                )
                for q in queries:
                    page = await context.new_page()
                    url = source.url_template.format(query=quote_plus(q))
                    # Counters para diagnóstico por query
                    raw_cards = 0
                    empty_title = 0
                    bad_href = 0
                    no_climate_hint = 0
                    kept = 0
                    sample_titles: list[str] = []
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                        if source.ready_selector:
                            try:
                                await page.wait_for_selector(
                                    source.ready_selector, timeout=8000
                                )
                            except Exception:
                                # página pode não ter resultados — seguimos
                                pass

                        card_sel = source.card_selector or "article"
                        cards = await page.query_selector_all(card_sel)
                        raw_cards = len(cards)
                        for card in cards[:10]:  # limita 10 por query/fonte
                            title_el = await card.query_selector("a, h2, h3")
                            if not title_el:
                                empty_title += 1
                                continue
                            title = (await title_el.inner_text()).strip()
                            if not title:
                                empty_title += 1
                                continue
                            if len(sample_titles) < 3:
                                sample_titles.append(title[:80])
                            href = await title_el.get_attribute("href") or ""
                            if href.startswith("/"):
                                base = re.match(r"(https?://[^/]+)", url)
                                if base:
                                    href = base.group(1) + href
                            if not href.startswith("http"):
                                bad_href += 1
                                continue
                            lead_el = await card.query_selector("p")
                            lead = (await lead_el.inner_text()).strip() if lead_el else ""
                            art = Article(
                                url=href,
                                title=title[:300],
                                lead=lead[:500],
                                published_at=None,  # páginas de busca raramente trazem data estruturada
                                source_key=source.key,
                                source_name=source.name,
                                query=q,
                                raw={},
                            )
                            if not _has_climate_hint(art):
                                no_climate_hint += 1
                                continue
                            articles.append(art)
                            kept += 1
                    except Exception as exc:
                        log.warning(
                            "news_html_page_failed",
                            source=source.key,
                            query=q,
                            error=str(exc),
                        )
                    finally:
                        if settings.news_scraper_debug:
                            log.info(
                                "news_html_query_debug",
                                source=source.key,
                                query=q,
                                url=url,
                                raw_cards=raw_cards,
                                empty_title=empty_title,
                                bad_href=bad_href,
                                no_climate_hint=no_climate_hint,
                                kept=kept,
                                sample_titles=sample_titles,
                            )
                        await page.close()
            finally:
                await browser.close()
    except Exception as exc:
        log.warning("news_playwright_failed", source=source.key, error=str(exc))
        return []

    # Dedup por URL dentro do mesmo batch
    dedup: dict[str, Article] = {}
    for a in articles:
        dedup.setdefault(a.url, a)
    out = list(dedup.values())
    log.info("news_html_fetched", source=source.key, candidates=len(out))
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def fetch_articles_for_dam(
    dam: Dam,
    redis: Redis | None = None,
) -> list[Article]:
    """Roda todas as fontes ativas para uma barragem e devolve artigos novos.

    Filtros aplicados em ordem:
      1. Janela de idade (`max_age_days` por fonte)
      2. Match textual com as queries da barragem
      3. Pelo menos uma palavra-chave climática (`CLIMATE_HINTS`)
      4. Dedup via Redis (URLs vistas nos últimos 30 dias)
    """
    queries = build_queries(dam)
    now = datetime.now(tz=timezone.utc)

    tasks = []
    for source in active_sources():
        if source.strategy == "rss":
            tasks.append(_fetch_rss(source, queries, now))
        elif source.strategy == "html_search":
            tasks.append(_fetch_html_search(source, queries, now))

    if not tasks:
        return []

    results = await asyncio.gather(*tasks, return_exceptions=True)
    articles: list[Article] = []
    for res in results:
        if isinstance(res, Exception):
            log.warning("news_source_exception", error=str(res))
            continue
        articles.extend(res)

    if not articles:
        return []

    # Dedup global por URL dentro deste batch
    dedup: dict[str, Article] = {}
    for a in articles:
        dedup.setdefault(a.url, a)
    candidates = list(dedup.values())

    if redis is None:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)

    fresh = await _filter_unseen(redis, candidates)
    log.info(
        "news_fetch_done",
        dam_id=dam.id,
        candidates=len(candidates),
        unseen=len(fresh),
        sources=[s.key for s in active_sources()],
    )
    return fresh


# Re-export para outros módulos
__all__ = [
    "Article",
    "NEWS_SOURCES",
    "active_sources",
    "build_queries",
    "fetch_articles_for_dam",
    "mark_seen",
]
