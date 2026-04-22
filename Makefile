.PHONY: help up down restart build logs ps migrate seed test shell psql redis flower dashboard fetch-climate generate-report clean

# ==============================================================================
# DIA — Dam Intelligence Agent — Makefile
# ==============================================================================

help:
	@echo "DIA — Common commands"
	@echo ""
	@echo "  make up                  Start all services in background"
	@echo "  make down                Stop all services"
	@echo "  make restart             Restart all services"
	@echo "  make build               Rebuild images"
	@echo "  make logs                Tail logs (api + worker + beat)"
	@echo "  make ps                  List running containers"
	@echo ""
	@echo "  make migrate             Run alembic upgrade head"
	@echo "  make migration MSG=\"msg\" Generate new alembic revision (autogenerate)"
	@echo "  make seed                Seed the database with the 15 monitored dams"
	@echo "  make test                Run pytest suite"
	@echo ""
	@echo "  make shell               Open python REPL inside api container"
	@echo "  make psql                Open psql shell on the postgres container"
	@echo "  make redis               Open redis-cli on the redis container"
	@echo ""
	@echo "  make dashboard           Open dashboard in browser (localhost:8080)"
	@echo "  make flower              Open Celery Flower in browser (localhost:5555)"
	@echo ""
	@echo "  make fetch-climate       Trigger climate fetch task now"
	@echo "  make generate-report     Trigger weekly briefing generation now"
	@echo ""
	@echo "  make clean               Remove containers + volumes (DANGEROUS)"

# --- Lifecycle ---

up:
	docker compose up -d

down:
	docker compose down

restart:
	docker compose restart

build:
	docker compose build

logs:
	docker compose logs -f api worker beat

ps:
	docker compose ps

# --- Database ---

migrate:
	docker compose run --rm api alembic upgrade head

migration:
	@if [ -z "$(MSG)" ]; then echo "Usage: make migration MSG=\"your message\""; exit 1; fi
	docker compose run --rm api alembic revision --autogenerate -m "$(MSG)"

seed:
	docker compose run --rm api python scripts/seed_dams.py

test:
	docker compose run --rm api pytest tests/ -v

# --- Shells ---

shell:
	docker compose exec api python

psql:
	docker compose exec postgres psql -U dia -d dia_db

redis:
	docker compose exec redis redis-cli

# --- URLs (Windows: uses `start`; Linux: `xdg-open`; Mac: `open`) ---

dashboard:
	@echo "Dashboard: http://localhost:8080"

flower:
	@echo "Flower:    http://localhost:5555"

# --- Task triggers ---

fetch-climate:
	docker compose exec api celery -A app.tasks.celery_app call app.tasks.climate_tasks.fetch_all_climate_data

generate-report:
	docker compose exec api celery -A app.tasks.celery_app call app.tasks.report_tasks.generate_weekly_briefing

# --- Cleanup (destructive) ---

clean:
	docker compose down -v
