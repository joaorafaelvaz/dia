"""Conversão HTML → PDF via WeasyPrint.

A função pública é `render_report_pdf(report)` — recebe uma instância de
`Report` já com `content_html` preenchido e devolve bytes do PDF.

**Design:**
- Usamos um **wrapper HTML mínimo** aqui mesmo (sem Jinja/templates), o que
  mantém o PDF independente do dashboard. O conteúdo do relatório vem do
  Claude como HTML sanitizado; a gente só envelopa com um `<html>` com
  metadata, CSS de print, e um cabeçalho institucional.
- Em Batch 3, quando houver um template Jinja `reports/briefing.html`
  mais rico (com logo Fractal, tabelas de dam_profiles, etc.), este
  módulo pode ser refatorado pra consumir esse template em vez do wrapper
  inline. Por ora, simples é melhor.
- WeasyPrint importa libs nativas (cairo, pango) na hora do import;
  mantemos o import no topo do módulo porque o Dockerfile já inclui
  essas libs (mesmas usadas pelo Chromium do Playwright).
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from weasyprint import CSS, HTML

from app.models.report import Report
from app.utils.logging import get_logger

log = get_logger(__name__)


# Logo Fractal — carregada uma vez no import e embutida como data URI.
# Colocar o arquivo em `app/web/static/img/fractal-logo.png` (ou .svg). Se
# ausente, o PDF renderiza sem logo (fallback silencioso) pra não quebrar
# geração quando o asset não foi commitado ainda.
_LOGO_PATH_PNG = Path(__file__).resolve().parent.parent.parent / "web" / "static" / "img" / "fractal-logo.png"
_LOGO_PATH_SVG = Path(__file__).resolve().parent.parent.parent / "web" / "static" / "img" / "fractal-logo.svg"


def _load_logo_data_uri() -> str | None:
    """Carrega o logo como data URI. Prefere SVG (vetor, escala bem)."""
    for path, mime in ((_LOGO_PATH_SVG, "image/svg+xml"), (_LOGO_PATH_PNG, "image/png")):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        b64 = base64.b64encode(data).decode("ascii")
        log.info("report_pdf_logo_loaded", path=str(path), bytes=len(data))
        return f"data:{mime};base64,{b64}"
    log.warning("report_pdf_logo_missing", searched=[str(_LOGO_PATH_PNG), str(_LOGO_PATH_SVG)])
    return None


_LOGO_DATA_URI: str | None = _load_logo_data_uri()


# CSS pensado para A4 com margens razoáveis e boa legibilidade em impressão.
# Mantemos paleta sóbria (tons de cinza e azul) — o relatório é documento
# executivo, não peça de marketing.
_BASE_CSS = """
@page {
    size: A4;
    margin: 22mm 18mm 20mm 18mm;
    @top-right {
        content: "DIA · Fractal Engenharia";
        font-size: 8pt;
        color: #6b7280;
    }
    @bottom-right {
        content: "Página " counter(page) " / " counter(pages);
        font-size: 8pt;
        color: #6b7280;
    }
}

html {
    font-family: "Helvetica", "Arial", sans-serif;
    font-size: 10.5pt;
    line-height: 1.45;
    color: #1f2937;
}

.header {
    border-bottom: 2px solid #1e3a8a;
    padding-bottom: 8pt;
    margin-bottom: 14pt;
}
.header .logo {
    max-height: 36pt;
    max-width: 150pt;
    margin-bottom: 8pt;
}
.header .title {
    font-size: 15pt;
    font-weight: bold;
    color: #1e3a8a;
    margin: 0;
}
.header .subtitle {
    font-size: 9pt;
    color: #6b7280;
    margin-top: 2pt;
}

h1 { font-size: 16pt; color: #1e3a8a; margin-top: 14pt; }
h2 { font-size: 13pt; color: #1e3a8a; margin-top: 12pt; border-bottom: 1px solid #e5e7eb; padding-bottom: 3pt; }
h3 { font-size: 11pt; color: #374151; margin-top: 10pt; }

p { margin: 4pt 0; }

ul, ol { margin: 4pt 0 6pt 18pt; }
li { margin: 2pt 0; }

strong { color: #111827; }
em { color: #374151; }

code {
    font-family: "Courier New", monospace;
    font-size: 9pt;
    background: #f3f4f6;
    padding: 1pt 3pt;
    border-radius: 2pt;
    color: #1f2937;
}

table {
    border-collapse: collapse;
    width: 100%;
    margin: 6pt 0;
    font-size: 9.5pt;
}
th, td {
    border: 1px solid #d1d5db;
    padding: 4pt 6pt;
    text-align: left;
    vertical-align: top;
}
th {
    background: #f3f4f6;
    font-weight: 600;
    color: #111827;
}

blockquote {
    border-left: 3px solid #1e3a8a;
    padding-left: 10pt;
    color: #374151;
    margin: 6pt 0;
    font-style: italic;
}

.footer {
    margin-top: 18pt;
    padding-top: 6pt;
    border-top: 1px solid #e5e7eb;
    font-size: 8pt;
    color: #6b7280;
}
"""


def _wrap_html(report: Report) -> str:
    """Envolve o `content_html` do relatório num documento completo."""
    kind_label = {
        "briefing": "Briefing Comercial Interno",
        "client": "Relatório Técnico-Executivo",
    }.get(report.report_type, "Relatório")

    generated_at = report.generated_at or datetime.now(tz=timezone.utc)
    generated_str = generated_at.strftime("%d/%m/%Y %H:%M UTC")
    period_str = f"{report.period_start:%d/%m/%Y} – {report.period_end:%d/%m/%Y}"

    title = escape(report.title or kind_label)
    subtitle = escape(
        f"{kind_label} · escopo {report.scope} · período {period_str} · "
        f"gerado em {generated_str}"
    )

    # O content_html vem do python-markdown — já é HTML válido, usamos direto.
    body = report.content_html or "<p><em>Conteúdo vazio.</em></p>"

    logo_img = (
        f'<img class="logo" src="{_LOGO_DATA_URI}" alt="Fractal Engenharia"/>'
        if _LOGO_DATA_URI
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8"/>
<title>{title}</title>
</head>
<body>
  <header class="header">
    {logo_img}
    <p class="title">{title}</p>
    <p class="subtitle">{subtitle}</p>
  </header>
  <main>
    {body}
  </main>
  <footer class="footer">
    Documento gerado automaticamente pelo DIA (Dam Intelligence Agent) da
    Fractal Engenharia. As recomendações derivam de dados climáticos e
    notícias agregadas no período — revise antes de compartilhar externamente.
  </footer>
</body>
</html>"""


def render_report_pdf(report: Report) -> bytes:
    """Renderiza o `Report` como PDF (bytes). Levanta se WeasyPrint falhar."""
    html_str = _wrap_html(report)
    pdf_bytes = HTML(string=html_str).write_pdf(
        stylesheets=[CSS(string=_BASE_CSS)],
    )
    log.info(
        "report_pdf_rendered",
        report_id=report.id,
        report_type=report.report_type,
        pdf_bytes=len(pdf_bytes),
    )
    return pdf_bytes


__all__ = ["render_report_pdf"]
