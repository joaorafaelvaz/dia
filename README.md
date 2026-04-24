# DIA — Dam Intelligence Agent

Monitoramento autônomo de barragens para a **Fractal Engenharia**. Coleta dados climáticos, detecta eventos atípicos, gera alertas preditivos e produz relatórios de prospecção com IA.

> **Status:** Scaffold inicial. Implementação em 4 fases — ver `C:\Users\rafae\.claude\plans\claude-md-agente-transient-panda.md`.

## Stack

- **Runtime:** Python 3.11, FastAPI, PostgreSQL 16, Redis 7, Celery 5 + Redbeat
- **Frontend:** Jinja2 + HTMX + TailwindCSS + Leaflet (dashboard dark mode)
- **IA:** Anthropic Claude (Opus 4.7 para relatórios, Haiku 4.5 para classificação)
- **Deploy:** Docker Compose em servidor Ubuntu x86_64 (`bvip` / `fractaleng.linkwise.digital`)

## Fases de implementação

| Fase | Escopo | Status |
|------|--------|--------|
| Scaffold | Estrutura, Docker, deps | ✅ atual |
| F1 | Núcleo: models + Open-Meteo + API + dashboard + alertas | ⏳ próximo |
| F2 | News scraping (Playwright) + classificação IA + métricas de custo | ⏳ |
| F3 | Geração de relatórios IA + export PDF | ⏳ |
| F4 | ANA Hidrowebservice + CEMADEN + notificações (WhatsApp/email) + testes + docs | ⏳ |

## Quick start (scaffold validation)

```bash
# 1. Configure env
cp .env.example .env
# edit .env: at minimum set ANTHROPIC_API_KEY, BASIC_AUTH_PASS

# 2. Build images
docker compose build

# 3. Start services
docker compose up -d

# 4. Check health
curl -u admin:<pass> http://localhost:8080/api/v1/health
# expected: {"status":"ok","phase":"scaffold"}

# 5. Stop
docker compose down
```

O endpoint `/api/v1/health` é um stub — será substituído na Fase 1.

## Estrutura de diretórios

```
.
├── app/                    # Python package principal
│   ├── api/v1/             # FastAPI routers (Fase 1)
│   ├── models/             # SQLAlchemy ORM (Fase 1)
│   ├── schemas/            # Pydantic v2 (Fase 1)
│   ├── services/
│   │   ├── climate/        # Open-Meteo / ANA Hidrowebservice / CEMADEN
│   │   ├── news/           # Scraping + classificação
│   │   ├── ai/             # Claude client + relatórios
│   │   └── notifications/  # WhatsApp (n8n) + email
│   ├── tasks/              # Celery
│   ├── web/                # Jinja2 + HTMX + Tailwind
│   └── utils/
├── alembic/                # Migrations
├── scripts/                # Seed, test_apis
├── tests/
├── docker-compose.yml
├── Dockerfile
├── Makefile
├── pyproject.toml
└── .env.example
```

## Comandos comuns

```bash
make help            # Lista tudo
make up              # Sobe serviços
make logs            # Tail em api + worker + beat
make migrate         # Alembic upgrade head (após Fase 1)
make seed            # Popula 15 barragens (após Fase 1)
make test            # pytest
make down            # Para tudo
```

## Referências

- Especificação original: `CLAUDE.md` (fornecida pelo cliente)
- Plano de execução: `C:\Users\rafae\.claude\plans\claude-md-agente-transient-panda.md`
