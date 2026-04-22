#!/usr/bin/env bash
# ==============================================================================
# DIA — Deploy script para servidor bvip (Ubuntu x86_64)
# Domínio alvo: https://dia.linkwise.digital
# ==============================================================================
#
# Uso:
#   # Primeira instalação no bvip:
#   sudo mkdir -p /opt/dia && sudo chown "$USER:$USER" /opt/dia
#   cd /opt/dia
#   git clone https://github.com/joaorafaelvaz/dia.git .
#   cp .env.example .env && nano .env              # preencher secrets
#   ./scripts/deploy.sh
#
#   # Atualizações subsequentes:
#   cd /opt/dia && git pull && ./scripts/deploy.sh
#
# Este script:
#   1. Valida pré-requisitos (docker, docker compose, .env)
#   2. Faz pull das imagens base + build local
#   3. Sobe os containers
#   4. Aguarda o Postgres ficar healthy
#   5. Roda migrations
#   6. Seed (idempotente) das 15 barragens
#   7. Smoke test no endpoint /api/v1/health
#
# Reverse proxy (dia.linkwise.digital → localhost:8080) fica fora do escopo
# deste script — ver nota no final.
# ==============================================================================

set -euo pipefail

# Cores para logs
RED="\033[0;31m"; GREEN="\033[0;32m"; YELLOW="\033[1;33m"; BLUE="\033[0;34m"; NC="\033[0m"
log()  { echo -e "${BLUE}[deploy]${NC} $*"; }
ok()   { echo -e "${GREEN}[  ok  ]${NC} $*"; }
warn() { echo -e "${YELLOW}[ warn ]${NC} $*"; }
err()  { echo -e "${RED}[error]${NC} $*" >&2; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

DOMAIN="dia.linkwise.digital"
API_PORT="${API_PORT:-8080}"
HEALTH_URL="http://localhost:${API_PORT}/api/v1/health"

# ------------------------------------------------------------------------------
# 1. Pré-requisitos
# ------------------------------------------------------------------------------
log "Validando pré-requisitos..."

if ! command -v docker >/dev/null 2>&1; then
    err "docker não encontrado. Instale via: https://docs.docker.com/engine/install/ubuntu/"
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    err "'docker compose' (plugin v2) não encontrado. Instale docker-compose-plugin."
    exit 1
fi

if [ ! -f .env ]; then
    err "Arquivo .env não encontrado em $REPO_DIR"
    err "Crie a partir do exemplo:  cp .env.example .env && nano .env"
    exit 1
fi

# Verifica placeholders que precisam ser trocados em produção
if grep -q "change-me" .env; then
    warn "Há placeholders 'change-me' no .env — revise antes de usar em produção."
fi
if grep -q "^ANTHROPIC_API_KEY=sk-ant-\.\.\." .env; then
    warn "ANTHROPIC_API_KEY ainda contém o placeholder do .env.example."
fi

ok "Pré-requisitos OK"

# ------------------------------------------------------------------------------
# 2. Pull + build
# ------------------------------------------------------------------------------
log "Fazendo pull de imagens base..."
docker compose pull postgres redis || warn "pull falhou — seguindo com cache local"

log "Buildando imagens locais (api/worker/beat/flower)..."
docker compose build --pull

ok "Build concluído"

# ------------------------------------------------------------------------------
# 3. Subir containers
# ------------------------------------------------------------------------------
log "Subindo serviços em background..."
docker compose up -d

# ------------------------------------------------------------------------------
# 4. Aguardar Postgres healthy
# ------------------------------------------------------------------------------
log "Aguardando Postgres ficar healthy..."
for i in {1..30}; do
    status="$(docker inspect -f '{{.State.Health.Status}}' dia_postgres 2>/dev/null || echo "starting")"
    if [ "$status" = "healthy" ]; then
        ok "Postgres healthy"
        break
    fi
    if [ "$i" = "30" ]; then
        err "Postgres não ficou healthy em 30s. Verifique: docker compose logs postgres"
        exit 1
    fi
    sleep 1
done

# ------------------------------------------------------------------------------
# 5. Migrations
# ------------------------------------------------------------------------------
log "Rodando Alembic migrations..."
docker compose exec -T api alembic upgrade head

ok "Schema atualizado"

# ------------------------------------------------------------------------------
# 6. Seed (idempotente)
# ------------------------------------------------------------------------------
log "Seed das 15 barragens (idempotente)..."
docker compose exec -T api python -m scripts.seed_dams

# ------------------------------------------------------------------------------
# 7. Smoke test
# ------------------------------------------------------------------------------
log "Smoke test: $HEALTH_URL"
sleep 2
if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    ok "API respondendo em $HEALTH_URL"
else
    warn "Endpoint /api/v1/health não respondeu — verifique: docker compose logs api"
fi

# ------------------------------------------------------------------------------
# Resumo
# ------------------------------------------------------------------------------
cat <<EOF

${GREEN}═══════════════════════════════════════════════════════════════${NC}
  DIA deploy concluído
${GREEN}═══════════════════════════════════════════════════════════════${NC}

  Dashboard (local) : http://localhost:${API_PORT}
  Dashboard (público): https://${DOMAIN}
  Flower            : http://localhost:5555
  API docs          : http://localhost:${API_PORT}/api/docs

  Logs:  docker compose logs -f api worker beat
  Status: docker compose ps

${YELLOW}Reverse proxy pendente?${NC}
  Se dia.linkwise.digital ainda não aponta para este servidor:
    1. DNS A-record  : dia.linkwise.digital → IP público do bvip
    2. Configurar Caddy/Nginx para proxiar 443 → localhost:${API_PORT}
       (exemplo Caddy em docs/reverse-proxy-caddy.md)
    3. Confirmar certificado Let's Encrypt emitido

${YELLOW}Próximo ciclo de coleta climática:${NC}
  O Celery beat dispara fetch_all_climate_data a cada 3h.
  Para disparar agora:
    curl -u admin:\$BASIC_AUTH_PASS -X POST \\
      http://localhost:${API_PORT}/api/v1/tasks/run/fetch_all_climate_data
EOF
