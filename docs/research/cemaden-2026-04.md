# CEMADEN — não integrado nesta release

**Data:** 2026-04-25
**Decisão:** Skip CEMADEN. Não implementar `app/services/climate/cemaden.py`.
**Reabrir quando:** CEMADEN publicar API REST estável OU mapa interativo expor
endpoint JSON documentado.

## Por que estava no plano

A CLAUDE.md original (§7) listava CEMADEN como terceira fonte climática junto
com Open-Meteo e INMET, citando dois endpoints:

- `http://www.cemaden.gov.br/mapainterativo/load/carregaMapaEmpreendimento.php`
- RSS de alertas por estado em `/alertas/alertas-emitidos/`

A spec já marcava esses endpoints como especulativos ("research task na Fase 4").

## O que foi sondado

`scripts/probe_cemaden.py` bate em 9 alvos. Resultado bruto em
`scripts/fixtures/cemaden/probe-20260425T075135Z.json`. Sumário:

| Endpoint | Status | Veredito |
|---|---|---|
| `www.cemaden.gov.br/mapainterativo/load/carregaMapaEmpreendimento.php` | 404 | morto |
| `www.cemaden.gov.br/alertas/alertas-emitidos/` | 404 | morto |
| `www2.cemaden.gov.br/mapainterativo/load/carregaMapaEmpreendimento.php` | 404 | morto |
| `www2.cemaden.gov.br/mapainterativo/` | 200 HTML | redirect → `mapainterativo.cemaden.gov.br` |
| `mapainterativo.cemaden.gov.br/` | 200 HTML | server-rendered, sem API JSON |
| `mapainterativo.cemaden.gov.br/api`, `/api/v1/alertas`, `/data/alertas.json` etc. | 404 | sem API descoberta |
| `www.cemaden.gov.br/categoria/alertas-cemaden/feed/` | 200 RSS | **0 items** (vazio) |
| `www.cemaden.gov.br/feed/` | 200 RSS | 10 items, último de **jan/2022** |
| `sjc.salvar.cemaden.gov.br/...` (API legada) | DNS fail | subdomínio morto |
| `https://www.cemaden.gov.br/` | 302 → `gov.br/cemaden/pt-br` | site institucional Plone |

## Análise

1. O CEMADEN migrou comunicação institucional pro portal gov.br
   (`https://www.gov.br/cemaden/pt-br`). O site WordPress legado
   (`www2.cemaden.gov.br`) ainda responde, mas o feed editorial está congelado
   desde jan/2022 e a categoria "alertas-cemaden" está vazia.
2. O mapa interativo (`mapainterativo.cemaden.gov.br`) é uma página HTML
   tradicional, sem API JSON pública descoberta. Provavelmente consome layers
   WMS / pluviômetros via AJAX interno não documentado — engenharia reversa
   seria frágil.
3. O endpoint legado `sjc.salvar.cemaden.gov.br` (descoberto em integrações de
   terceiros pré-2024) tem DNS morto.

## Por que skip e não scraping

O sinal que CEMADEN traria (pluviômetros em tempo real + alertas hidrológicos)
já é coberto pelas fontes integradas:

- **Open-Meteo** — sinal preditivo até 16d, sem chave, alta confiabilidade
- **ANA Hidrowebservice** — pluviometria histórica oficial (lag 2-6 meses) com
  fallback de top-15 estações por distância
- **Notícias** (Fase 2) — eventos extremos com cobertura editorial

Scraping do mapa interativo do CEMADEN traria pouco sinal incremental e
quebraria com qualquer redesign. Custo de manutenção > benefício.

## Caminho para reabrir

Re-rodar o probe periodicamente:

```bash
uv run python scripts/probe_cemaden.py
```

Se aparecer endpoint JSON estável (status 200 + `application/json`), reabrir
esta decisão e implementar `app/services/climate/cemaden.py` seguindo o
padrão de `ana.py` (per-loop httpx client, cache Redis, retry com tenacity).

## Side-effects desta decisão

- `.env.example`: `CEMADEN_BASE_URL` marcado como deprecated, mantido pra não
  quebrar configs existentes.
- `app/config.py`: settings de CEMADEN não criadas (não eram usadas).
- README: nota em "fontes de dados" explicando o skip.
