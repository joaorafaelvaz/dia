# DIA — Dam Intelligence Agent

Monitoramento autônomo de barragens para a **Fractal Engenharia**. Coleta dados climáticos, detecta eventos atípicos, gera alertas preditivos e produz relatórios de prospecção com IA.

> **Status:** Fases 1–4 implementadas. Em produção em `https://dia.linkwise.digital`.

## Stack

- **Runtime:** Python 3.11, FastAPI, PostgreSQL 16, Redis 7, Celery 5 + Redbeat
- **Frontend:** Jinja2 + HTMX + Tailwind + Leaflet (dashboard dark mode)
- **IA:** Anthropic Claude — Opus 4.7 (relatórios) + Haiku 4.5 (classificação)
- **Notificações:** n8n + [WAHA](https://github.com/devlikeapro/waha) (WhatsApp), SMTP (email)
- **Deploy:** Docker Compose, Caddy reverse proxy

## Arquitetura

```
┌───────────────┐  ┌──────────────────────┐  ┌──────────────────┐
│ Celery Beat   │→│ climate / news /     │→│ Postgres (events,│
│ (cron)        │  │ analysis / report    │  │  forecasts,      │
└───────────────┘  │ tasks                │  │  alerts, reports)│
                   └──────────┬───────────┘  └──────────────────┘
                              │                       ▲
                              ▼                       │
                   ┌──────────────────────┐  ┌────────┴─────────┐
                   │ Open-Meteo / ANA /   │  │ FastAPI (REST    │
                   │ Google News /        │  │ + Jinja dash)    │
                   │ Anthropic Claude     │  └────────┬─────────┘
                   └──────────────────────┘           │
                              │                       ▼
                              ▼              Basic Auth (operador)
                   ┌──────────────────────┐
                   │ Notification sweep   │
                   │ (rate limit + sev.   │
                   │  gating) → n8n/WAHA  │
                   │  ou SMTP             │
                   └──────────────────────┘
```

## Fontes de dados

| Fonte | Uso | Frequência | Status |
|---|---|---|---|
| Open-Meteo | Forecast 16d + arquivo histórico | a cada 3h | integrada |
| ANA Hidrowebservice | Pluviometria oficial (lag 2-6m) | sob demanda em relatórios | integrada |
| Google News RSS | Eventos editoriais | 3×/dia (6h, 12h, 18h) | integrada |
| Anthropic Claude | Classificação + relatórios | sob demanda | integrada |
| CEMADEN | Alertas em tempo real | — | **skip** — ver [docs/research/cemaden-2026-04.md](docs/research/cemaden-2026-04.md) |
| INMET | Pluviometria estações | — | substituído por ANA (apitempo offline em 2026) |

## Deploy no servidor bvip

Pré-requisitos: Docker + Docker Compose v2, Caddy ou nginx pra TLS, DNS apontando pra `dia.linkwise.digital` (`201.22.86.97`).

```bash
# 1. Clone no /opt
sudo git clone https://github.com/joaorafaelvaz/dia.git /opt/dia
cd /opt/dia

# 2. Configure
cp .env.example .env
# Edite .env. Mínimo: APP_SECRET_KEY, BASIC_AUTH_PASS, ANTHROPIC_API_KEY,
# POSTGRES_PASSWORD, ANA_USER/PASS (se usar relatórios com ANA),
# N8N_WEBHOOK_URL/TOKEN (se ativar notif), SMTP_* (se email).

# 3. Build + up
docker compose build
docker compose up -d

# 4. Migrations + seed (uma vez)
docker compose exec api alembic upgrade head
docker compose exec api python scripts/seed_dams.py

# 5. Reverse proxy (Caddy) — ver docs/reverse-proxy-caddy.md
sudo systemctl reload caddy

# 6. Verificar
curl -u admin:<BASIC_AUTH_PASS> https://dia.linkwise.digital/api/v1/dams | jq length  # 15
```

Depois de subir, o Celery beat dispara o primeiro ciclo em até 3h (cron `SCHEDULE_CLIMATE_FETCH=0 */3 * * *`). Pra forçar imediatamente: `make fetch-climate`.

## Comandos comuns

```bash
make help            # Lista tudo
make up              # Sobe stack
make logs            # Tail api + worker + beat
make migrate         # alembic upgrade head
make seed            # 15 barragens
make test            # pytest (offline, ~6s)
make fetch-climate   # Dispara climate sweep agora
make generate-report # Dispara briefing semanal agora
make flower          # Abre Flower (UI Celery) em :5555
make down            # Para tudo (mantém volumes)
```

## Runbook

### Ativar notificações WhatsApp

1. Suba uma instância WAHA (`docker run devlikeapro/waha`) e autentique a sessão escaneando o QR no `/dashboard` do WAHA.
2. Importe `docs/n8n-flows/dam-alerts.json` no n8n. No container do n8n, defina:
   - `DIA_WEBHOOK_TOKEN` (mesmo valor que `N8N_WEBHOOK_TOKEN` no `.env` do DIA)
   - `DIA_WAHA_BASE_URL`, `DIA_WAHA_API_KEY`, `DIA_WAHA_SESSION`
   - `DIA_WHATSAPP_TO` (formato WAHA: `5531999999999@c.us`)
3. Ative o flow no n8n e teste com payload manual:
   ```bash
   curl -X POST $N8N_WEBHOOK_URL \
     -H "Authorization: Bearer $N8N_WEBHOOK_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"alert_id":1,"message":"Teste DIA"}'
   ```
4. No `.env` do DIA, mude `NOTIFICATIONS_ENABLED=true` e reinicie o worker (`docker compose restart worker beat`). O sweep roda a cada 5min e envia alerts ativos com `severity ≥ 3`.

### Adicionar barragem nova

Edite `scripts/seed_dams.py` (lista `DAMS`) ou faça `POST /api/v1/dams` com Basic Auth. Campos mínimos: `name`, `owner_group`, `dam_type`, `municipality`, `state`, `latitude`, `longitude`, `dpa`. O próximo tick de `fetch-climate` já popula forecasts pra ela.

### Trocar modelo Claude

`.env` → `CLAUDE_MODEL_REPORTS` ou `CLAUDE_MODEL_CLASSIFY`. Reinicie worker. Os IDs válidos seguem `claude-{family}-{version}` (ex.: `claude-opus-4-7`, `claude-haiku-4-5-20251001`).

### Ler métricas de custo IA

`GET /api/v1/metrics/ai-costs` (Basic Auth) retorna agregados 24h/7d/30d com tokens in/out e USD por modelo. Card no dashboard mostra acumulado do mês.

### Ajustar agressividade de notificações

`.env`:
- `NOTIFICATION_RATE_LIMIT_HOURS=6` — janela mínima entre notif por (dam, alert_type). Subir pra 12h em estação chuvosa se gerar spam.
- `NOTIFICATION_MIN_SEVERITY_WHATSAPP=3` (Alto+) — abaixar pra 2 ativa "Moderado" também.
- `NOTIFICATION_MIN_SEVERITY_EMAIL=4` (Muito Alto+) — manter alto pra preservar caixa.

### Forçar expiração de alertas antigos

`docker compose exec api python -c "from app.tasks.climate_tasks import expire_stale_alerts; print(expire_stale_alerts())"`. As 3 regras (TTL explícito, ack >7d, forecast antigo sem TTL) também rodam de hora em hora via beat.

## Troubleshooting

| Sintoma | Causa provável | Ação |
|---|---|---|
| Beat não dispara | redbeat lock travado em Redis | `docker compose exec redis redis-cli DEL redbeat::lock`, restart beat |
| `Event loop is closed` em logs do worker | engine asyncpg amarrada ao primeiro loop | já mitigado: `task_session()` cria engine NullPool por task |
| WeasyPrint quebra ao gerar PDF | dependências GTK/Pango faltando | rebuild imagem; já incluso no `Dockerfile` (`python:3.11-slim-bookworm`) |
| Alerta criado mas WhatsApp não chega | `NOTIFICATIONS_ENABLED=false` ou n8n flow inativo | checar `.env` + Flower: task `dispatch_pending_notifications` deve rodar a cada 5min |
| ANA retorna 0 estações | endpoint OAuth falhou ou `ANA_USER/PASS` errados | `docker compose logs worker | grep ana_token` |
| Custo Claude alto inesperado | provavelmente classificação de notícias sem cache hit | conferir `/api/v1/metrics/ai-costs`, ver Redis: `KEYS dia:news:cls:*` |

## Testes

26 smoke tests offline (sem Docker, sem Redis, sem Postgres — SQLite in-memory + mocks).

```bash
make test            # via Docker
# ou local:
uv run pytest tests/ -v
```

Cobertura por arquivo:
- `test_climate_parsing.py` — Open-Meteo response → `ClimateEvent`
- `test_aggregator.py` — escala de severidade + dedup ±2d + multiplicadores
- `test_api_endpoints.py` — Basic Auth + filtros + ack
- `test_context_builder.py` — montagem de contexto pro relatório IA
- `test_notifications.py` — dispatcher (rate limit + severity gating + retry)
- `test_alert_expiration.py` — sweep de expiração (3 regras + idempotência)

## Referências

- Especificação original: `CLAUDE.md`
- Reverse proxy: [docs/reverse-proxy-caddy.md](docs/reverse-proxy-caddy.md)
- Flow n8n: [docs/n8n-flows/dam-alerts.json](docs/n8n-flows/dam-alerts.json)
- Decisão CEMADEN: [docs/research/cemaden-2026-04.md](docs/research/cemaden-2026-04.md)
