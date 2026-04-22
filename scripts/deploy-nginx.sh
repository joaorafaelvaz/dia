#!/usr/bin/env bash
# ==============================================================================
# DIA — Deploy Nginx multi-serviço no servidor bvip (Ubuntu)
# ==============================================================================
#
# O que este script faz (idempotente — seguro re-executar):
#   1. Instala nginx + certbot (se ainda não instalados)
#   2. Cria o snippet compartilhado /etc/nginx/snippets/proxy-common.conf
#   3. Garante o `map $http_upgrade $connection_upgrade` no nginx.conf (WebSocket)
#   4. Para cada serviço em SERVICES[]:
#        a. Gera /etc/nginx/sites-available/<dominio>
#        b. Ativa link em sites-enabled/
#        c. Solicita certificado Let's Encrypt via certbot --nginx (se faltar)
#   5. Valida config e recarrega nginx
#
# Pré-requisitos:
#   - DNS A-record de cada domínio já apontado para o IP público do bvip
#   - Portas 80 e 443 liberadas no firewall (ufw allow 'Nginx Full')
#   - Os serviços upstream (DIA, n8n, etc.) devem estar ouvindo em 127.0.0.1:<porta>
#
# Uso:
#   sudo ./scripts/deploy-nginx.sh
#
#   # Para adicionar um domínio novo: edite a lista SERVICES abaixo e re-rode.
# ==============================================================================

set -euo pipefail

# ------------------------------------------------------------------------------
# Configuração — lista de serviços a proxiar
# Formato: "dominio|porta_upstream|tamanho_max_body|extra_locations(opcional)"
# ------------------------------------------------------------------------------
SERVICES=(
    "dia.linkwise.digital|8080|25m|flower=5555"
    # Adicione mais serviços aqui. Exemplos:
    # "suite.linkwise.digital|8090|10m|"
    # "vipcam.linkwise.digital|8081|100m|"
)

# Email usado pelo Let's Encrypt (avisos de renovação expirando)
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-vaz.rafael@gmail.com}"

# ------------------------------------------------------------------------------
# Cores para logs
# ------------------------------------------------------------------------------
RED="\033[0;31m"; GREEN="\033[0;32m"; YELLOW="\033[1;33m"; BLUE="\033[0;34m"; NC="\033[0m"
log()  { echo -e "${BLUE}[nginx-deploy]${NC} $*"; }
ok()   { echo -e "${GREEN}[    ok    ]${NC} $*"; }
warn() { echo -e "${YELLOW}[   warn   ]${NC} $*"; }
err()  { echo -e "${RED}[  error   ]${NC} $*" >&2; }

# ------------------------------------------------------------------------------
# 0. Pré-requisitos
# ------------------------------------------------------------------------------
if [ "$EUID" -ne 0 ]; then
    err "Rode como root (sudo ./scripts/deploy-nginx.sh)"
    exit 1
fi

log "Atualizando apt cache..."
apt-get update -qq

for pkg in nginx certbot python3-certbot-nginx; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
        log "Instalando $pkg..."
        apt-get install -y -qq "$pkg"
    fi
done
ok "nginx + certbot presentes"

# Firewall (sem falhar se ufw não estiver instalado/ativo)
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    ufw allow 'Nginx Full' >/dev/null 2>&1 || true
    ok "ufw: Nginx Full liberado"
fi

# ------------------------------------------------------------------------------
# 1. Snippet compartilhado de proxy
# ------------------------------------------------------------------------------
SNIPPET="/etc/nginx/snippets/proxy-common.conf"
log "Escrevendo $SNIPPET"
mkdir -p /etc/nginx/snippets
cat > "$SNIPPET" <<'EOF'
# /etc/nginx/snippets/proxy-common.conf
# Gerado por scripts/deploy-nginx.sh — não editar manualmente
proxy_http_version 1.1;
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host  $host;
proxy_set_header Authorization     $http_authorization;

# WebSocket (n8n, Flower live updates, etc.)
proxy_set_header Upgrade    $http_upgrade;
proxy_set_header Connection $connection_upgrade;

proxy_read_timeout    300s;
proxy_connect_timeout 30s;
proxy_send_timeout    300s;

proxy_buffering off;
EOF
ok "snippet atualizado"

# ------------------------------------------------------------------------------
# 2. map $http_upgrade — requisito do snippet acima
# ------------------------------------------------------------------------------
MAP_FILE="/etc/nginx/conf.d/00-websocket-map.conf"
if [ ! -f "$MAP_FILE" ]; then
    log "Criando $MAP_FILE"
    cat > "$MAP_FILE" <<'EOF'
# Map para suportar upgrade WebSocket nos proxies
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
EOF
    ok "map WebSocket criado"
fi

# ------------------------------------------------------------------------------
# 3. Gerar vhosts
# ------------------------------------------------------------------------------
render_vhost() {
    local domain="$1"
    local port="$2"
    local maxbody="$3"
    local extras="$4"          # "flower=5555,grafana=3000" ou vazio
    local conf="/etc/nginx/sites-available/${domain}"
    local cert_path="/etc/letsencrypt/live/${domain}/fullchain.pem"
    local has_cert=false
    [ -f "$cert_path" ] && has_cert=true

    log "Escrevendo $conf"

    {
        echo "# Gerado por scripts/deploy-nginx.sh — ${domain} → 127.0.0.1:${port}"
        echo ""
        echo "server {"
        echo "    listen 80;"
        echo "    listen [::]:80;"
        echo "    server_name ${domain};"
        echo ""
        echo "    location /.well-known/acme-challenge/ { root /var/www/html; }"

        if $has_cert; then
            echo "    location / { return 301 https://\$host\$request_uri; }"
        else
            # Sem cert ainda — serve direto em HTTP; certbot adicionará o bloco 443
            _write_proxy_locations "$port" "$maxbody" "$extras"
        fi

        echo "}"

        if $has_cert; then
            echo ""
            echo "server {"
            echo "    listen 443 ssl http2;"
            echo "    listen [::]:443 ssl http2;"
            echo "    server_name ${domain};"
            echo ""
            echo "    ssl_certificate     /etc/letsencrypt/live/${domain}/fullchain.pem;"
            echo "    ssl_certificate_key /etc/letsencrypt/live/${domain}/privkey.pem;"
            echo "    include             /etc/letsencrypt/options-ssl-nginx.conf;"
            echo "    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;"
            echo ""
            _write_proxy_locations "$port" "$maxbody" "$extras"
            echo "}"
        fi
    } > "$conf"

    ln -sf "$conf" "/etc/nginx/sites-enabled/${domain}"
}

_write_proxy_locations() {
    local port="$1"
    local maxbody="$2"
    local extras="$3"

    echo ""
    echo "    client_max_body_size ${maxbody};"
    echo ""
    echo "    access_log /var/log/nginx/\$server_name.access.log;"
    echo "    error_log  /var/log/nginx/\$server_name.error.log;"

    if [ -n "$extras" ]; then
        IFS=',' read -ra EXT <<< "$extras"
        for pair in "${EXT[@]}"; do
            local sub="${pair%%=*}"
            local subport="${pair##*=}"
            echo ""
            echo "    location /${sub}/ {"
            echo "        proxy_pass http://127.0.0.1:${subport}/;"
            echo "        include    /etc/nginx/snippets/proxy-common.conf;"
            echo "    }"
        done
    fi

    echo ""
    echo "    location / {"
    echo "        proxy_pass http://127.0.0.1:${port};"
    echo "        include    /etc/nginx/snippets/proxy-common.conf;"
    echo "    }"
}

DOMAINS=()
for entry in "${SERVICES[@]}"; do
    IFS='|' read -r DOMAIN PORT MAXBODY EXTRAS <<< "$entry"
    DOMAIN="${DOMAIN// /}"
    PORT="${PORT// /}"
    MAXBODY="${MAXBODY:-10m}"
    if [ -z "$DOMAIN" ] || [ -z "$PORT" ]; then
        warn "Entrada SERVICES inválida: '$entry' — ignorada"
        continue
    fi
    render_vhost "$DOMAIN" "$PORT" "$MAXBODY" "$EXTRAS"
    DOMAINS+=("$DOMAIN")
done

# ------------------------------------------------------------------------------
# 4. Primeiro teste de config (sem TLS ainda)
# ------------------------------------------------------------------------------
log "Validando nginx (sem TLS)..."
nginx -t
systemctl reload nginx
ok "nginx recarregado"

# ------------------------------------------------------------------------------
# 5. Certbot por domínio (idempotente — só pede se cert não existir)
# ------------------------------------------------------------------------------
for domain in "${DOMAINS[@]}"; do
    cert_path="/etc/letsencrypt/live/${domain}/fullchain.pem"
    if [ -f "$cert_path" ]; then
        ok "cert já existe para ${domain} (não re-solicitando)"
        continue
    fi

    log "Solicitando certificado Let's Encrypt para ${domain}..."
    if certbot --nginx \
        --non-interactive \
        --agree-tos \
        --redirect \
        --email "$LETSENCRYPT_EMAIL" \
        -d "$domain"; then
        ok "cert emitido para ${domain}"
    else
        warn "certbot falhou para ${domain} — verifique DNS/firewall e rode manualmente:"
        warn "    sudo certbot --nginx -d ${domain}"
    fi
done

# ------------------------------------------------------------------------------
# 6. Validação final
# ------------------------------------------------------------------------------
log "Validação final nginx -t"
nginx -t
systemctl reload nginx

# Renovação automática já vem via systemd timer certbot.timer
if systemctl list-timers | grep -q certbot; then
    ok "certbot.timer ativo (renovação automática)"
else
    warn "certbot.timer não detectado — rode: sudo systemctl enable --now certbot.timer"
fi

# ------------------------------------------------------------------------------
# Resumo
# ------------------------------------------------------------------------------
cat <<EOF

${GREEN}═══════════════════════════════════════════════════════════════${NC}
  Nginx multi-serviço deploy concluído
${GREEN}═══════════════════════════════════════════════════════════════${NC}

  Serviços configurados:
EOF
for entry in "${SERVICES[@]}"; do
    IFS='|' read -r DOMAIN PORT _ _ <<< "$entry"
    echo "    https://${DOMAIN}  →  127.0.0.1:${PORT}"
done

cat <<EOF

  Verificar:
    curl -I https://dia.linkwise.digital/api/v1/health   # espera 401 (Basic Auth)
    sudo tail -f /var/log/nginx/dia.linkwise.digital.access.log

  Renovação manual (teste):
    sudo certbot renew --dry-run

  Adicionar serviço novo:
    1. Edite a lista SERVICES no topo deste script
    2. Re-rode: sudo ./scripts/deploy-nginx.sh
EOF
