# Reverse proxy — dia.linkwise.digital

Dois cenários documentados:

- **Caddy** (mais simples, TLS automático) — se o bvip ainda não tem proxy configurado.
- **Nginx** (multi-serviço) — se o bvip já serve outros subdomínios Linkwise (n8n, suite, etc.). Ver seção **"Alternativa: Nginx (multi-serviço)"** abaixo.

Ambos terminam TLS via Let's Encrypt e proxiam 443 → `localhost:8080`.

## Pré-requisitos

1. **DNS**: criar registro A em `linkwise.digital`:
   ```
   dia    A    201.22.86.97
   ```
   (IP público do servidor bvip)
2. **Firewall**: portas 80 e 443 abertas no servidor (`sudo ufw allow 80,443/tcp`).
3. **Caddy** já instalado no host (não dentro do Docker do DIA).

## Caddyfile

Adicione ao `/etc/caddy/Caddyfile` do bvip:

```caddy
dia.linkwise.digital {
    encode zstd gzip
    reverse_proxy localhost:8080

    log {
        output file /var/log/caddy/dia.log {
            roll_size 10mb
            roll_keep 5
        }
    }
}
```

Se já há outros sites no Caddyfile, basta adicionar o bloco acima.

## Aplicar

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

O Caddy negocia o certificado Let's Encrypt automaticamente na primeira
requisição HTTPS.

## Verificação

```bash
curl -I https://dia.linkwise.digital/api/v1/health
# Espera-se: 401 Unauthorized (Basic Auth ativo) — TLS funcionando.

curl -u admin:<senha> https://dia.linkwise.digital/api/v1/health
# {"status":"ok"}
```

## Alternativa: Nginx (multi-serviço)

O `bvip` já hospeda outros serviços Linkwise (n8n, suite, vipcam, etc.) via
Nginx. A recomendação é manter **um arquivo por subdomínio** em
`/etc/nginx/sites-available/` e adicionar só o bloco do DIA ao conjunto
existente — sem tocar nos outros.

### 1. Snippet reutilizável de proxy

Criar `/etc/nginx/snippets/proxy-common.conf` (uma vez, usado por todos os
serviços):

```nginx
# /etc/nginx/snippets/proxy-common.conf
proxy_http_version 1.1;
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host  $host;

# WebSocket (n8n, Flower live updates, etc.)
proxy_set_header Upgrade    $http_upgrade;
proxy_set_header Connection $connection_upgrade;

proxy_read_timeout    300s;
proxy_connect_timeout 30s;
proxy_send_timeout    300s;

proxy_buffering off;
```

Em `/etc/nginx/nginx.conf`, dentro do bloco `http {}`, adicionar o map
(uma vez) para suportar WebSocket:

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
```

### 2. Arquivo do DIA

`/etc/nginx/sites-available/dia.linkwise.digital`:

```nginx
# HTTP → HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name dia.linkwise.digital;
    return 301 https://$host$request_uri;
}

# HTTPS
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name dia.linkwise.digital;

    ssl_certificate     /etc/letsencrypt/live/dia.linkwise.digital/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/dia.linkwise.digital/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    # Para PDFs gerados e uploads futuros
    client_max_body_size 25m;

    access_log /var/log/nginx/dia.access.log;
    error_log  /var/log/nginx/dia.error.log;

    # Flower (Celery monitoring) — opcional, sub-path dedicado
    location /flower/ {
        proxy_pass http://127.0.0.1:5555/;
        include    /etc/nginx/snippets/proxy-common.conf;
    }

    # API + dashboard
    location / {
        proxy_pass http://127.0.0.1:8080;
        include    /etc/nginx/snippets/proxy-common.conf;
    }
}
```

Ativar:

```bash
sudo ln -s /etc/nginx/sites-available/dia.linkwise.digital \
           /etc/nginx/sites-enabled/dia.linkwise.digital
sudo nginx -t
sudo systemctl reload nginx
```

### 3. Exemplos para outros serviços no mesmo host

Mesma estrutura, só muda o upstream. Referência para quem adiciona novos
subdomínios ao bvip:

```nginx
# /etc/nginx/sites-available/n8n.linkwise.digital
server {
    listen 443 ssl http2;
    server_name n8n.linkwise.digital;

    ssl_certificate     /etc/letsencrypt/live/n8n.linkwise.digital/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/n8n.linkwise.digital/privkey.pem;

    client_max_body_size 50m;

    location / {
        proxy_pass http://127.0.0.1:5678;
        include    /etc/nginx/snippets/proxy-common.conf;
    }
}
server { listen 80; server_name n8n.linkwise.digital; return 301 https://$host$request_uri; }
```

```nginx
# /etc/nginx/sites-available/suite.linkwise.digital
server {
    listen 443 ssl http2;
    server_name suite.linkwise.digital;

    ssl_certificate     /etc/letsencrypt/live/suite.linkwise.digital/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/suite.linkwise.digital/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8090;   # ajustar para a porta real
        include    /etc/nginx/snippets/proxy-common.conf;
    }
}
server { listen 80; server_name suite.linkwise.digital; return 301 https://$host$request_uri; }
```

### 4. Certificados Let's Encrypt

Um comando por subdomínio (Nginx permanece ouvindo durante o desafio):

```bash
sudo certbot --nginx -d dia.linkwise.digital
sudo certbot --nginx -d n8n.linkwise.digital
sudo certbot --nginx -d suite.linkwise.digital
```

Renovação automática já vem configurada via `systemd timer certbot.timer`.
Testar com `sudo certbot renew --dry-run`.

### 5. Tabela de portas do bvip

Para não colidir ao adicionar serviços novos, manter documentado o
mapeamento subdomínio → porta local:

| Subdomínio               | Upstream          | Serviço         |
|--------------------------|-------------------|-----------------|
| dia.linkwise.digital     | 127.0.0.1:8080    | DIA API         |
| dia.linkwise.digital/flower | 127.0.0.1:5555 | Flower (Celery) |
| n8n.linkwise.digital     | 127.0.0.1:5678    | n8n             |
| suite.linkwise.digital   | 127.0.0.1:8090    | (ajustar)       |
| vipcam.linkwise.digital  | 127.0.0.1:???     | (ajustar)       |

> Atualizar esta tabela sempre que um serviço novo entrar no bvip.
