# Reverse proxy — Caddy (dia.linkwise.digital)

Configuração recomendada para servir o DIA em `https://dia.linkwise.digital`,
com TLS automático via Let's Encrypt.

## Pré-requisitos

1. **DNS**: criar registro A em `linkwise.digital`:
   ```
   dia    A    <IP público do servidor bvip>
   ```
2. **Firewall**: portas 80 e 443 abertas no servidor.
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

## Alternativa: Nginx

Se o ambiente já usa Nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name dia.linkwise.digital;

    ssl_certificate     /etc/letsencrypt/live/dia.linkwise.digital/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/dia.linkwise.digital/privkey.pem;

    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }
}

server {
    listen 80;
    server_name dia.linkwise.digital;
    return 301 https://$host$request_uri;
}
```

Gerar certificado com Certbot:
```bash
sudo certbot --nginx -d dia.linkwise.digital
```
