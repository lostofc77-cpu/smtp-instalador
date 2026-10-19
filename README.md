# Instalador do Painel de Faturas

Servidor SMTP proprio (Postfix + OpenDKIM + Cloudflare DNS) com painel web de envio
de faturas (Python puro, porta 8080). Documentacao completa em [GUIA-COMPLETO-SERVIDOR-SMTP.md](GUIA-COMPLETO-SERVIDOR-SMTP.md).

## Instalar / atualizar na VPS (Ubuntu, root)

    curl -fsSL "https://raw.githubusercontent.com/lostofc77-cpu/smtp-instalador/main/instalar.sh" | bash

Depois disso, atualizacoes futuras: comando `atualizar-painel`.

## Arquivos

- `instalar.sh` — instalador/atualizador de comando unico (verifica md5)
- `painel.py` — painel completo (v5.1): envio pela porta 25, HTML com pre-visualizacao,
  lote ate 200 destinatarios, modelos salvos, contador, monitoramento
- `GUIA-COMPLETO-SERVIDOR-SMTP.md` — documentacao de tudo (DNS, Contabo, operacao, entregabilidade)
- `enviar-faturas.sh` — disparo em lote com PDF individual por cliente (via /root/clientes.csv)

## Verificacao de integridade

md5 do painel.py v5.1: `4d2bef084dc0f4da0ed15d947206e10d` (o instalador checa sozinho).
