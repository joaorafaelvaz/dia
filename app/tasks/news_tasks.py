"""Celery tasks: news scraping + Claude classification + evento persistido.

Fluxo por barragem:
  1. scraper.fetch_articles_for_dam(dam)  → artigos novos (já filtrados por dedup Redis)
  2. classifier.classify_article(article, dam) → Classification
  3. Se relevante (>=0.7), persiste como ClimateEvent com source_type="news"
     OU anexa ao evento weather/news existente no mesmo (dam, type, date±2d).
  4. Marca URL como vista (TTL 30d) para não reprocessar.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from celery.exceptions import SoftTimeLimitExceeded
from redis.asyncio import Redis
from sqlalchemy import and_, select

from app.config import settings
from app.database import task_session
from app.models.dam import Dam
from app.models.event import ClimateEvent
from app.services.news import classifier, scraper
from app.tasks.celery_app import celery_app
from app.utils.logging import get_logger
from app.utils.severity import label_for

log = get_logger(__name__)


async def _persist_from_article(
    session, dam: Dam, article: scraper.Article, cls: classifier.Classification
) -> tuple[bool, bool]:
    """Persiste ou atualiza ClimateEvent a partir de um artigo classificado.

    Retorna (created, updated). Pelo menos um dos dois é True quando chamamos.
    """
    event_date = (
        article.published_at.date() if article.published_at else date.today()
    )

    # Dedup cross-source: mesmo (dam, event_type, event_date ± 2d) → atualiza
    stmt = select(ClimateEvent).where(
        and_(
            ClimateEvent.dam_id == dam.id,
            ClimateEvent.event_type == cls.event_type,
            ClimateEvent.event_date >= event_date - timedelta(days=2),
            ClimateEvent.event_date <= event_date + timedelta(days=2),
        )
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()

    source_entry = {
        "url": article.url,
        "title": article.title,
        "source": article.source_name,
        "relevance": cls.relevance,
        "severity": cls.severity,
        "mentions_dam_directly": cls.mentions_dam_directly,
        "published_at": article.published_at.isoformat() if article.published_at else None,
    }

    if existing:
        raw = dict(existing.raw_data or {})
        sources = list(raw.get("news_sources") or [])
        # não duplica o mesmo URL dentro do raw_data
        if not any(s.get("url") == article.url for s in sources):
            sources.append(source_entry)
        raw["news_sources"] = sources
        existing.raw_data = raw

        if cls.severity > existing.severity:
            existing.severity = cls.severity
            existing.severity_label = label_for(cls.severity)

        # Se o evento existia apenas como "weather" sem análise IA, adiciona
        if not existing.ai_analysis:
            existing.ai_analysis = cls.summary

        return (False, True)

    session.add(
        ClimateEvent(
            dam_id=dam.id,
            event_type=cls.event_type,
            severity=cls.severity,
            severity_label=label_for(cls.severity),
            title=article.title[:300],
            description=(cls.summary or article.lead or article.title)[:2000],
            source_type="news",
            source=article.source_name[:500],
            event_date=event_date,
            ai_analysis=cls.summary,
            raw_data={"news_sources": [source_entry]},
        )
    )
    return (True, False)


async def _scrape_for_dam(dam_id: int) -> dict[str, int]:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with task_session() as session:
            dam = (
                await session.execute(select(Dam).where(Dam.id == dam_id))
            ).scalar_one_or_none()
            if not dam:
                log.warning("news_dam_not_found", dam_id=dam_id)
                return {"articles": 0, "created": 0, "updated": 0, "skipped": 0}
            if not dam.is_active:
                return {"articles": 0, "created": 0, "updated": 0, "skipped": 0}

            try:
                articles = await scraper.fetch_articles_for_dam(dam, redis=redis)
            except Exception as exc:
                log.error("news_scrape_failed", dam_id=dam_id, error=str(exc))
                return {"articles": 0, "created": 0, "updated": 0, "skipped": 0}

            created = updated = skipped = 0
            for art in articles:
                try:
                    cls = await classifier.classify_article(
                        session=session, redis=redis, article=art, dam=dam
                    )
                except Exception as exc:
                    log.warning(
                        "news_classify_failed",
                        dam_id=dam_id,
                        url=art.url,
                        error=str(exc),
                    )
                    cls = None

                if not cls or not cls.is_relevant():
                    skipped += 1
                    await scraper.mark_seen(redis, art.url)
                    continue

                try:
                    c, u = await _persist_from_article(session, dam, art, cls)
                    created += int(c)
                    updated += int(u)
                except Exception as exc:
                    log.error(
                        "news_persist_failed",
                        dam_id=dam_id,
                        url=art.url,
                        error=str(exc),
                    )
                    continue

                await scraper.mark_seen(redis, art.url)

            try:
                await session.commit()
            except Exception as exc:
                log.error("news_commit_failed", dam_id=dam_id, error=str(exc))
                await session.rollback()
                raise

            log.info(
                "news_scrape_done",
                dam_id=dam.id,
                dam_name=dam.name,
                articles=len(articles),
                created=created,
                updated=updated,
                skipped=skipped,
            )
            return {
                "articles": len(articles),
                "created": created,
                "updated": updated,
                "skipped": skipped,
            }
    finally:
        await redis.aclose()


@celery_app.task(
    name="app.tasks.news_tasks.scrape_news_for_dam",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def scrape_news_for_dam(self, dam_id: int) -> dict[str, int]:
    try:
        return asyncio.run(_scrape_for_dam(dam_id))
    except SoftTimeLimitExceeded:
        log.warning("news_scrape_soft_timeout", dam_id=dam_id)
        raise
    except Exception as exc:
        log.error("news_scrape_task_failed", dam_id=dam_id, error=str(exc))
        # backoff exponencial via retry_delay
        raise self.retry(exc=exc) from exc


async def _all_active_dam_ids() -> list[int]:
    async with task_session() as session:
        result = await session.execute(
            select(Dam.id).where(Dam.is_active.is_(True)).order_by(Dam.id)
        )
        return list(result.scalars().all())


@celery_app.task(name="app.tasks.news_tasks.scrape_all_news")
def scrape_all_news() -> dict[str, int]:
    """Fan-out: dispara um scrape por barragem ativa."""
    dam_ids = asyncio.run(_all_active_dam_ids())
    log.info("news_scrape_all_start", dam_count=len(dam_ids))
    for dam_id in dam_ids:
        scrape_news_for_dam.delay(dam_id)
    return {"dispatched": len(dam_ids)}
