"""Classifica relevância de uma notícia em relação a uma barragem via Claude Haiku.

Entrada: `Article` + `Dam`
Saída: `Classification` (dataclass) com relevance, event_type, severity, summary,
       mentions_dam_directly, custo da chamada.

Design:
- Cache Redis por `hash(title+lead)` evita reclassificar a mesma notícia com TTL 90d.
  Hit de cache grava `AIUsage(cache_hit=True, cost=0)` para métrica.
- Parsing robusto: localiza JSON dentro da resposta via regex; se falhar, re-tenta
  uma vez com prompt reforçado "retorne SOMENTE JSON".
- Erros de parsing após retry → retorna `Classification(relevance=0.0, ...)`.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.dam import Dam
from app.services.ai.claude_client import complete, record_cache_hit
from app.services.news.scraper import Article
from app.utils.logging import get_logger

log = get_logger(__name__)

CACHE_KEY_PREFIX = "news:classify:"
CACHE_TTL_SECONDS = 90 * 24 * 3600

EventType = Literal[
    "heavy_rain", "flood", "dam_failure_risk", "landslide", "drought",
    "other", "not_relevant",
]

SYSTEM_PROMPT = (
    "Você é um analista da Fractal Engenharia classificando notícias quanto à "
    "relevância para a segurança de barragens específicas. Você responde SEMPRE "
    "em JSON estrito, sem prefácio nem comentários."
)

USER_PROMPT_TEMPLATE = """Analise esta notícia em relação à barragem "{dam_name}" \
do grupo {owner} em {municipality}/{state} (tipo: {dam_type}).

Notícia:
Título: {title}
Lead: {lead}
Fonte: {source_name} ({url})

Responda SOMENTE em JSON válido com estas chaves exatas:
{{
  "relevance": <float 0.0 a 1.0>,
  "event_type": "heavy_rain" | "flood" | "dam_failure_risk" | "landslide" | "drought" | "other" | "not_relevant",
  "severity": <int 1 a 5>,
  "summary": "<resumo em 1-2 frases, em português>",
  "mentions_dam_directly": <true|false>
}}

Regras:
- relevance alta (>=0.7) só quando há conexão geográfica OU temática clara com a barragem.
- event_type="not_relevant" quando a notícia não tem relação com clima/desastre/barragem.
- severity 1=informativo, 2=atenção, 3=alerta, 4=grave, 5=crítico/rompimento."""

_JSON_BLOCK_RE = re.compile(r"\{.*?\}", re.DOTALL)


@dataclass
class Classification:
    relevance: float
    event_type: EventType
    severity: int
    summary: str
    mentions_dam_directly: bool
    cached: bool = False

    def is_relevant(self, threshold: float = 0.7) -> bool:
        return (
            self.relevance >= threshold
            and self.event_type != "not_relevant"
        )


def _cache_key(article: Article, dam: Dam) -> str:
    # Hash do conteúdo + dam_id — mesma notícia em contexto de outra barragem
    # pode ter relevância diferente, então dam_id entra na chave.
    payload = f"{dam.id}|{article.title}|{article.lead}".encode("utf-8")
    return CACHE_KEY_PREFIX + hashlib.sha1(payload).hexdigest()


def _parse_json_response(text: str) -> dict | None:
    """Tenta extrair um objeto JSON da resposta do Claude.

    Modelos às vezes embrulham a resposta em markdown (```json ... ```) ou
    prefixos. Tentamos json.loads direto primeiro; se falhar, buscamos o
    primeiro bloco {...}.
    """
    text = text.strip()
    # strip fences markdown
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _coerce_classification(data: dict) -> Classification:
    relevance = float(data.get("relevance", 0.0))
    relevance = max(0.0, min(1.0, relevance))

    event_type = str(data.get("event_type", "not_relevant"))
    if event_type not in {
        "heavy_rain", "flood", "dam_failure_risk", "landslide",
        "drought", "other", "not_relevant",
    }:
        event_type = "other"

    severity = int(data.get("severity", 1))
    severity = max(1, min(5, severity))

    summary = str(data.get("summary", "")).strip()[:500]
    mentions = bool(data.get("mentions_dam_directly", False))

    return Classification(
        relevance=relevance,
        event_type=event_type,  # type: ignore[arg-type]
        severity=severity,
        summary=summary,
        mentions_dam_directly=mentions,
    )


async def classify_article(
    *,
    session: AsyncSession,
    redis: Redis,
    article: Article,
    dam: Dam,
) -> Classification | None:
    """Classifica `article` em relação a `dam`. Retorna None em falha irrecuperável."""
    key = _cache_key(article, dam)
    cached_raw = await redis.get(key)
    if cached_raw:
        try:
            data = json.loads(cached_raw)
            await record_cache_hit(
                session=session,
                caller="news_classifier",
                model=settings.claude_model_classify,
            )
            result = _coerce_classification(data)
            result.cached = True
            return result
        except (json.JSONDecodeError, ValueError, TypeError):
            # Cache corrompido — sobrescrevemos abaixo
            log.warning("news_classify_cache_corrupt", key=key)

    prompt = USER_PROMPT_TEMPLATE.format(
        dam_name=dam.name,
        owner=dam.owner_group,
        municipality=dam.municipality,
        state=dam.state,
        dam_type=dam.dam_type,
        title=article.title,
        lead=article.lead or "(sem resumo)",
        source_name=article.source_name,
        url=article.url,
    )

    for attempt in (1, 2):
        try:
            text = await complete(
                session=session,
                caller="news_classifier",
                system=SYSTEM_PROMPT,
                prompt=prompt if attempt == 1 else (
                    prompt
                    + "\n\nIMPORTANTE: sua resposta DEVE ser um único objeto JSON válido "
                      "começando com { e terminando com }. Não inclua prefácio, markdown nem comentário."
                ),
                model=settings.claude_model_classify,
                max_tokens=settings.claude_max_tokens_classify,
                temperature=0.1,
            )
        except Exception as exc:
            log.error(
                "news_classify_call_failed",
                attempt=attempt,
                url=article.url,
                error=str(exc),
            )
            if attempt == 2:
                return None
            continue

        data = _parse_json_response(text)
        if data is not None:
            break
        log.warning(
            "news_classify_bad_json", attempt=attempt, raw=text[:200], url=article.url
        )
    else:
        return None

    result = _coerce_classification(data)
    try:
        await redis.setex(
            key,
            CACHE_TTL_SECONDS,
            json.dumps(
                {
                    "relevance": result.relevance,
                    "event_type": result.event_type,
                    "severity": result.severity,
                    "summary": result.summary,
                    "mentions_dam_directly": result.mentions_dam_directly,
                },
                ensure_ascii=False,
            ),
        )
    except Exception as exc:
        log.warning("news_classify_cache_write_failed", error=str(exc))
    return result
