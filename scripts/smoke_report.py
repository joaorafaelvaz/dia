"""Smoke test do pipeline de relatórios (F3 Batch 1).

Gera um briefing real chamando o Claude Opus, imprime o markdown e salva
um arquivo `.md` em `/tmp` (ou cwd) pra inspeção visual. Não persiste em
`reports` — uso é só pra verificar que o context builder + generator
casam antes de montar o resto da infra.

Uso:
    docker compose exec api python -m scripts.smoke_report --scope all
    docker compose exec api python -m scripts.smoke_report --scope gerdau --type client

Custo: 1 chamada Opus (~US$0.05-0.15 dependendo do tamanho do contexto).
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.database import task_session
from app.services.ai.context_builder import build_context
from app.services.ai.report_generator import (
    default_title,
    generate_briefing,
    generate_client_report,
)
from app.utils.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)


async def run(scope: str, report_type: str, period_days: int, forecast_days: int) -> None:
    async with task_session() as session:
        ctx = await build_context(
            session,
            scope=scope,
            period_days=period_days,
            forecast_days=forecast_days,
        )
        title = default_title(report_type, scope, period_days)
        if report_type == "briefing":
            md, html = await generate_briefing(
                session, ctx, title_suffix=title, forecast_days=forecast_days
            )
        else:
            md, html = await generate_client_report(
                session, ctx, title_suffix=title, forecast_days=forecast_days
            )
        # `complete()` fez um flush mas a transação precisa de commit
        # explícito pra gravar a linha de ai_usage.
        await session.commit()

    print("=" * 80)
    print(f"{report_type.upper()} — scope={scope} — período={period_days}d")
    print(f"Dam profiles: {len(ctx.dam_profiles)} · "
          f"events: {len(ctx.recent_events)} · "
          f"forecasts: {len(ctx.forecasts)} · "
          f"alerts: {len(ctx.active_alerts)}")
    print("=" * 80)
    print(md)
    print("=" * 80)

    out_md = Path(f"smoke_{report_type}_{scope}.md")
    out_md.write_text(md, encoding="utf-8")
    out_html = Path(f"smoke_{report_type}_{scope}.html")
    out_html.write_text(html, encoding="utf-8")
    print(f"\n[ok] markdown salvo em {out_md.resolve()}")
    print(f"[ok] html     salvo em {out_html.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", default="all",
                        choices=["all", "gerdau", "kinross"])
    parser.add_argument("--type", default="briefing",
                        choices=["briefing", "client"])
    parser.add_argument("--period-days", type=int, default=30)
    parser.add_argument("--forecast-days", type=int, default=7)
    args = parser.parse_args()
    asyncio.run(run(args.scope, args.type, args.period_days, args.forecast_days))


if __name__ == "__main__":
    main()
