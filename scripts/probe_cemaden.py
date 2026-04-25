"""Probe dos endpoints CEMADEN — research task pra Fase 4.

A spec original (CLAUDE.md §7) lista endpoints que podem ou não existir em
2026. Este script bate em cada um deles, registra status/content-type/sample
e ajuda a decidir entre 3 caminhos:

1. Endpoint X funciona → implementar `app/services/climate/cemaden.py`
2. Funciona parcialmente → fallback documentado (RSS Defesa Civil, etc.)
3. Tudo morto → README marca CEMADEN como "não integrado nesta release"

Uso:
    uv run python scripts/probe_cemaden.py

Saída: tabela human-readable + JSON em scripts/fixtures/cemaden/probe-<timestamp>.json
pra preservar evidência da decisão (URLs ficam offline com frequência —
o JSON é o que justifica a escolha 6 meses depois).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

# Lista deliberadamente ampla: spec oficial + endpoints históricos
# encontrados em integrações de terceiros + variantes prováveis (www2,
# subdomínios). Testar uma vez é barato; descobrir 2 meses depois que
# tinha um endpoint vivo é caro.
TARGETS: list[dict[str, str]] = [
    # --- URLs da spec original (CLAUDE.md §7) ---
    {
        "label": "spec_mapainterativo_php",
        "url": "http://www.cemaden.gov.br/mapainterativo/load/carregaMapaEmpreendimento.php",
        "method": "GET",
    },
    {
        "label": "spec_alertas_emitidos",
        "url": "http://www.cemaden.gov.br/alertas/alertas-emitidos/",
        "method": "GET",
    },
    # --- Variantes prováveis (www2 é comum em portais governo BR) ---
    {
        "label": "www2_mapainterativo_root",
        "url": "http://www2.cemaden.gov.br/mapainterativo/",
        "method": "GET",
    },
    {
        "label": "www2_carrega_empreendimento",
        "url": "http://www2.cemaden.gov.br/mapainterativo/load/carregaMapaEmpreendimento.php",
        "method": "GET",
    },
    # --- RSS / categorias (formato editorial dos alertas CEMADEN) ---
    {
        "label": "rss_alertas_categoria",
        "url": "http://www.cemaden.gov.br/categoria/alertas-cemaden/feed/",
        "method": "GET",
    },
    {
        "label": "rss_root_feed",
        "url": "http://www.cemaden.gov.br/feed/",
        "method": "GET",
    },
    # --- Lista de municípios monitorados (referência institucional) ---
    {
        "label": "municipios_monitorados",
        "url": "http://www.cemaden.gov.br/municipios-monitorados-2/",
        "method": "GET",
    },
    # --- API legada de gráficos (descoberta em integrações de 3os) ---
    {
        "label": "salvar_graficos_legacy",
        "url": "http://sjc.salvar.cemaden.gov.br/resources/graficos/interativo/getJson2.php?est=311860603A",
        "method": "GET",
    },
    # --- HTTPS (quem ainda serve só HTTP em 2026 é minoria) ---
    {
        "label": "https_root",
        "url": "https://www.cemaden.gov.br/",
        "method": "GET",
    },
]

USER_AGENT = "DIA-Probe/1.0 (Fractal Engenharia; research task)"
TIMEOUT_S = 15.0


def probe_one(client: httpx.Client, target: dict[str, str]) -> dict[str, object]:
    label = target["label"]
    url = target["url"]
    method = target["method"]
    out: dict[str, object] = {"label": label, "url": url, "method": method}
    try:
        resp = client.request(method, url, follow_redirects=True)
        out["status"] = resp.status_code
        out["final_url"] = str(resp.url)
        out["content_type"] = resp.headers.get("content-type", "")
        body = resp.text
        out["content_length"] = len(body)
        out["sample"] = body[:300] if body else ""
        # Heurística simples pra etiquetar utilidade
        ct = out["content_type"].lower()
        if resp.status_code != 200:
            out["verdict"] = f"http_{resp.status_code}"
        elif "xml" in ct or "rss" in ct or body.lstrip().startswith(("<?xml", "<rss")):
            out["verdict"] = "rss_or_xml"
        elif "json" in ct or body.lstrip().startswith(("{", "[")):
            out["verdict"] = "json_api"
        elif "html" in ct:
            out["verdict"] = "html_page"
        else:
            out["verdict"] = f"other:{ct or 'no-content-type'}"
    except httpx.TimeoutException:
        out["verdict"] = "timeout"
    except httpx.HTTPError as exc:
        out["verdict"] = f"http_error:{type(exc).__name__}"
        out["error"] = str(exc)
    except Exception as exc:
        out["verdict"] = f"exception:{type(exc).__name__}"
        out["error"] = str(exc)
    return out


def main() -> int:
    print(f"== CEMADEN probe — {datetime.now(timezone.utc).isoformat()} ==\n")
    results: list[dict[str, object]] = []
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    with httpx.Client(timeout=TIMEOUT_S, headers=headers) as client:
        for target in TARGETS:
            r = probe_one(client, target)
            results.append(r)
            verdict = r.get("verdict", "?")
            status = r.get("status", "-")
            length = r.get("content_length", 0)
            print(f"[{verdict:>20}] {status} {length:>7}b  {r['label']}")
            print(f"                       {r['url']}")
            if r.get("final_url") and r["final_url"] != r["url"]:
                print(f"                    -> {r['final_url']}")
            if "error" in r:
                print(f"                       error: {r['error']}")
            print()

    # Persist evidência
    outdir = Path(__file__).parent / "fixtures" / "cemaden"
    outdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outfile = outdir / f"probe-{ts}.json"
    outfile.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"== persisted {len(results)} probes to {outfile.relative_to(Path.cwd())} ==")

    # Sumário pra decisão
    alive = [r for r in results if r.get("verdict") in ("rss_or_xml", "json_api", "html_page")]
    print(f"\n== {len(alive)}/{len(results)} endpoints retornaram 200 com conteúdo ==")
    return 0 if alive else 1


if __name__ == "__main__":
    raise SystemExit(main())
