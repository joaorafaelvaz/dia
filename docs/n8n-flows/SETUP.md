# Setup do flow `dam-alerts.json` no n8n

Importar o JSON é só metade do trabalho — n8n não exporta credentials junto
com o flow (intencional, são per-instância). Você precisa criar 2 credentials
manualmente e vincular aos nós antes de ativar.

## 1. Pré-requisitos

- n8n rodando (qualquer versão recente)
- WAHA rodando + sessão WhatsApp autenticada (QR escaneado)
- 2 secrets em mãos:
  - **Bearer token** que o DIA vai mandar no header `Authorization` (deve bater
    com `N8N_WEBHOOK_TOKEN` no `.env` do DIA)
  - **API key** do WAHA (deve bater com `WHATSAPP_API_KEY` ou equivalente
    no `.env` do container WAHA)

## 2. Criar a credential "DIA Webhook Bearer"

n8n web UI → **Credentials** → **+ Add credential** → busca **"Header Auth"**.

| Campo | Valor |
|---|---|
| Credential Name | `DIA Webhook Bearer` |
| Name | `Authorization` |
| Value | `Bearer SEU_TOKEN_AQUI` (o mesmo do `N8N_WEBHOOK_TOKEN`) |

Salvar.

## 3. Criar a credential "WAHA API Key"

Mesmo caminho — outra credential Header Auth.

| Campo | Valor |
|---|---|
| Credential Name | `WAHA API Key` |
| Name | `X-Api-Key` |
| Value | `SUA_API_KEY_DO_WAHA` |

Salvar.

## 4. Importar o flow

n8n web UI → **Workflows** → **+ Import from File** → seleciona
`docs/n8n-flows/dam-alerts.json`.

O flow vai aparecer com 4 nós: `Webhook (DIA)` → `Format WAHA payload` →
`Send via WAHA` → `Respond 200 to DIA`.

## 5. Vincular as credentials aos nós

Os IDs no JSON são placeholders (`REPLACE_WITH_..._CREDENTIAL_ID`) — n8n vai
mostrar warning. Abrir cada nó e selecionar a credential correta no dropdown:

- **Webhook (DIA)** → seção *Authentication* → escolher `DIA Webhook Bearer`
- **Send via WAHA** → seção *Authentication* → escolher `WAHA API Key`

Salvar o flow.

## 6. Ajustar URL do WAHA (se diferente)

O nó `Send via WAHA` tem URL hardcoded `https://waha.linkwise.digital/api/sendText`.
Se sua instância WAHA está em outro endereço, abre o nó e edita o campo URL.

## 7. Ativar o flow

Toggle no canto superior direito → **Active**.

## 8. Testar

No DIA:

```bash
# Pega webhook URL de produção
curl -X POST https://n8n.linkwise.digital/webhook/dam-alerts \
  -H 'Authorization: Bearer SEU_TOKEN_AQUI' \
  -H 'Content-Type: application/json' \
  -d '{
    "alert_id": -1,
    "severity": 3,
    "whatsapp_to": "5531999999999@c.us",
    "title": "Teste manual",
    "message": "Se chegou no seu WhatsApp, está funcionando."
  }'
```

Resposta esperada: `{"status":"sent","alert_id":-1,"waha_response":{...}}`.

Sem o header Authorization (ou com token errado): `{"message":"Authorization data is wrong!"}` HTTP 403 — n8n rejeita antes do flow rodar.

## 9. Validação ponta-a-ponta pelo DIA

Em `https://dia.linkwise.digital/test-harness`, aba **Mensagem direta**,
escolhe "Apenas WhatsApp" e submit. Deve chegar no número configurado em
`DIA_WHATSAPP_TO` no `.env` do DIA.

## Troubleshooting

| Sintoma | Causa | Ação |
|---|---|---|
| `access to env vars denied` no n8n | Flow lê `$env.X` mas n8n bloqueia | Esse flow não usa `$env` — checa que importou a versão correta |
| 403 do n8n no DIA | Token diferente entre `.env` DIA e credential n8n | Confere os 2 valores; recria credential com valor exato |
| WAHA retorna 401 | API key errada | Recria credential `WAHA API Key`, confere secret no `.env` do WAHA |
| WAHA retorna 422 / 500 | `chatId` mal formatado | Verifica `DIA_WHATSAPP_TO` no `.env` do DIA — formato `5531999999999@c.us` |
| Mensagem chega vazia | `body.message` ausente | Cliente API/curl tá mandando `text` em vez de `message`? Ver schema do payload |
