# GUIA COMPLETO — Servidor SMTP de Faturas (Contabo + Ubuntu + Cloudflare)

> Versão **v5.1** — atualizado em **19/10/2026**.
> Fonte da verdade do código: **https://github.com/lostofc77-cpu/smtp-instalador**
> Este documento cobre: instalação do zero, painel, envios, operação, entregabilidade e todos os problemas já resolvidos.

---

## 1. Dados da instalação ATUAL

| Item | Valor |
|---|---|
| VPS Contabo | `vmi3679238` — **IP 13.140.139.10** — Ubuntu 24.04 |
| Domínio | `cardfatpromo.com` (DNS na **Cloudflare**) |
| Host de e-mail | `mail.cardfatpromo.com` |
| Usuário SMTP | `faturas` (porta 587 STARTTLS — para loja/mktemail) |
| Painel de faturas | `http://13.140.139.10:8080` — código de acesso próprio (ver item 6.3) |
| Serviços | `postfix`, `dovecot`, `opendkim`, `certbot`, `painel-faturas` (systemd) |
| Arquivos do painel | `/root/painel/painel.py` · `templates.json` (modelos) · `stats.json` (contador) |
| Atualizar painel | comando `atualizar-painel` (instalado pelo instalador) |
| Validação | mail-tester **10/10** (SPF/DKIM/DMARC/rDNS/blacklists) |

## 2. Arquitetura

- **Postfix** — entrega ao mundo pela porta 25; recebe da loja na 587 (login+TLS); aceita do painel pelo localhost:25.
- **Dovecot** — autenticação SASL (usuário/senha) da 587.
- **OpenDKIM** — assina tudo que sai (seletor `mail`).
- **Certbot** — cert TLS com renovação automática (+hook que recarrega os serviços).
- **Firewall em 2 camadas** — painel da Contabo (entrada) + UFW no servidor. Portas: 22, 25, 80, 443, 587, 8080.
- **Painel (Python puro, 8080)** — envio individual/lote (até 200), HTML com pré-visualização, `{{nome}} {{email}} {{mes}} {{ano}}`, modelos salvos, contador, monitoramento, reenvio de falhas.

## 3. Registros DNS na Cloudflare

⚠️ **REGRA DE OURO:** o A `mail` deve estar **"Somente DNS" (nuvem CINZA)** — laranja quebra e-mail e certificado.

| Tipo | Nome | Conteúdo |
|---|---|---|
| A | `mail` | `13.140.139.10` *(cinza!)* |
| MX | `@` | `mail.cardfatpromo.com` (prioridade 10) |
| TXT | `@` | `v=spf1 mx a:mail.cardfatpromo.com -all` |
| TXT | `_dmarc` | `v=DMARC1; p=quarantine; rua=mailto:postmaster@cardfatpromo.com` |
| TXT | `mail._domainkey` | valor DKIM completo (reimprimir: comando do item 6.4) |

## 4. Contabo — 3 configurações

1. **rDNS/PTR**: painel → DNS reverso → `13.140.139.10` → `mail.cardfatpromo.com`.
2. **Firewall do painel** (bloqueia toda entrada por padrão): regras ACCEPT TCP `22, 25, 80, 443, 587, 8080` + atribuir à instância.
3. **Ticket de desbloqueio da SAÍDA da porta 25** (firewall do painel não controla saída):

> Reverse DNS for 13.140.139.10 is set to mail.cardfatpromo.com. Outbound SMTP (port 25) is still blocked — connections to external mail servers time out. Please remove the outgoing SMTP filter. Purpose: sending transactional invoices to my own store's customers.

**Porta 25:** testadores de navegador veem a ENTRADA; envio precisa da SAÍDA (só o ticket resolve). Teste na VPS:
`timeout 8 bash -c 'exec 3<>/dev/tcp/gmail-smtp-in.l.google.com/25' && echo ABERTA || echo BLOQUEADA`

## 5. Instalação do zero (nova máquina)

**Ordem:** ① DNS A (cinza) → ② Contabo: rDNS + firewall + ticket saída 25 → ③ servidor de e-mail → ④ DNS restantes → ⑤ certificado → ⑥ mail-tester 10/10 → ⑦ DMARC `p=quarantine` → ⑧ painel.

1. **Servidor de e-mail** (Postfix/Dovecot/OpenDKIM/Certbot/UFW): usar o script comentado `setup-smtp-ubuntu.sh` (pasta local). Fluxo: `nano /root/s.sh` → colar → `sed -i 's/\r$//' /root/s.sh` → `MAIL_DOMAIN=cardfatpromo.com bash /root/s.sh`. Ele imprime senha SMTP + valor DKIM.
2. **Certificado** (com fallback 443):
   `certbot certonly --standalone --non-interactive --agree-tos -m postmaster@cardfatpromo.com -d mail.cardfatpromo.com || certbot certonly --standalone --non-interactive --agree-tos --preferred-challenges tls-alpn-01 -m postmaster@cardfatpromo.com -d mail.cardfatpromo.com` + apontar `smtpd_tls_cert_file/key_file` para `/etc/letsencrypt/live/mail.cardfatpromo.com/` + `systemctl reload postfix`.
3. **Painel — comando único** (baixa do GitHub, confere md5, preserva código de acesso, instala o `atualizar-painel`):
   `curl -fsSL "https://raw.githubusercontent.com/lostofc77-cpu/smtp-instalador/main/instalar.sh" | bash`
4. Regra TCP 8080 no firewall do painel Contabo (se máquina nova).

## 6. Painel — operação

**6.1 Instalar/atualizar:** `atualizar-painel` (ou o comando `curl` do item 5.3 — sempre puxa a versão mais nova do GitHub).

**6.2 Envio pelo painel:** cole a lista (1–200, um por linha ou `Nome;email`); nome do cliente sai do e-mail (`maria.souza@…` → "Maria"); assunto/corpo aceitam `{{nome}} {{email}} {{mes}} {{ano}}` (mês/ano resolvem na hora do envio); pré-visualização ao vivo + botão "ver como ficou"; PDF opcional (≤5 MB, mesmo para todos); progresso por destinatário + "reenviar falhas". Para PDF individual por cliente: `enviar-faturas.sh` + `/root/clientes.csv`.

**6.3 Código de acesso do painel:**
- Ver: `grep ACCESS_CODE /root/painel/painel.py`
- Trocar (gera automático): `C=$(openssl rand -hex 4); sed -i "s/^ACCESS_CODE = .*/ACCESS_CODE = \"$C\"/" /root/painel/painel.py && systemctl restart painel-faturas && sleep 2 && echo "NOVO CODIGO: $C"`
- Logs/reiniciar: `journalctl -u painel-faturas -n 30` / `systemctl restart painel-faturas`

**6.4 Senha SMTP (587, loja/mktemail):** trocar com `read -s -p "senha: " P; echo; echo "faturas:$P" | chpasswd` *(cole esta linha SOZINHA — `read` engole a linha seguinte de um bloco colado)*.

**6.5 Valor DKIM** (reimprimir para DNS): `grep -o '"[^"]*"' /etc/opendkim/keys/cardfatpromo.com/mail.txt | tr -d '"\n'; echo`

**6.6 Teste rápido porta 25** (sem senha, direto no Postfix): ver `testes/teste_envio.py` na pasta local (trocar destinatário).

## 7. Integrações

**Loja (Next.js/nodemailer):** `host: mail.cardfatpromo.com, port: 587, secure: false, auth: {user: 'faturas', pass: process.env.SMTP_PASS}`.
**mktemail:** `SMTP_HOST=mail.cardfatpromo.com · SMTP_PORT=587 · SMTP_SECURE=false · SMTP_USER=faturas · SMTP_FROM="Sua Fatura do Mes Chegou <faturas@cardfatpromo.com>"`.

## 8. Operação do dia a dia

```bash
postqueue -p                                   # fila (vazia = tudo entregue)
grep status=sent /var/log/mail.log | tail -5   # últimos entregues
grep status=bounced /var/log/mail.log | tail -5 # rejeitados
mail -u faturas                                # bounces (caixa local)
```

## 9. Entregabilidade (por que cai em spam e o que fazer)

Autenticação está 100% (mail-tester 10/10). Spam folder = **reputação + conteúdo**:

1. Remetente **sem caixa alta e sem "!"** (v5.1: "Sua Fatura do Mes Chegou") — caixa alta é gatilho clássico.
2. **Marcar "Não é spam"** nas contas de teste + adicionar `faturas@cardfatpromo.com` aos contatos — acelerador nº 1.
3. **Aquecimento 2–4 semanas**: 10–20/dia crescendo gradual; engajamento é o sinal que libera.
4. **Vigiar blacklist do IP**: https://mxtoolbox.com/blacklists.aspx (se listar → ticket Contabo p/ troca de IP).
5. **Google Postmaster Tools** (monitorar reputação no Gmail):
   - Acesse https://postmaster.google.com com a conta Google (ex.: novasnovinha69@gmail.com)
   - Clique **"Adicionar propriedade"/"+ Add"** → digite `cardfatpromo.com`
   - Ele mostra um registro TXT `google-site-verification=...`
   - Cloudflare → DNS → Adicionar registro: **TXT**, nome `@`, conteúdo = o valor `google-site-verification=...` inteiro → Salvar
   - Volte ao Postmaster e clique **Verificar** → status "Verificado"
   - Os gráficos (reputação de domínio/IP, falhas SPF/DKIM, erros) aparecem após alguns dias de tráfego real no Gmail
6. Se após 3–4 semanas ainda cair em provedor específico: opção técnica é relay de saída (Brevo/SES) mantendo o domínio.

## 10. Problemas reais que ocorreram e as soluções (consulta rápida)

| Sintoma | Causa | Solução |
|---|---|---|
| certbot `Connection aborted` / ⚠ no MX | A `mail` com **nuvem laranja** | Trocar para Somente DNS (cinza) |
| certbot `Timeout` porta 80 | Firewall Contabo sem regra 80 | Criar regra ACCEPT TCP 80 (e 443) |
| swaks `Host did not advertise authentication` | sem STARTTLS | usar `--tls` |
| mail-tester "DKIM inválido" | TXT colado pela metade | colar valor completo (item 6.5) |
| "25 aberta" no navegador, bloqueada no teste | navegador testa ENTRADA | ticket Contabo da SAÍDA |
| comando colado do Windows falha | quebras CRLF | `sed -i 's/\r$//' arquivo` |
| bloco com `read` no início deu erro | `read` engoliu a linha seguinte da colagem | colar linha com `read` SOZINHA |
| painel sem pré-visualização e envio (v2/v3) | **parêntese extra no JS** (`display='block')}`) | corrigido na v4 + bateria de testes com validação Node |
| colagem grande corrompia o instalador | PuTTY derruba caracteres | md5 gate → e finalmente **instalador no GitHub** (`curl \| bash`) |
| código de acesso em branco após update | arquivo antigo corrompido tinha código vazio | comando de trocar código (item 6.3) |
| e-mails em spam | domínio novo + caps + "!" no remetente | v5.1 + checklist do item 9 |
| push do repo agro-baito falhando | **token do remote origin expirado** | gerar novo PAT e atualizar o remote |

## 11. Emergência

```bash
systemctl restart postfix dovecot opendkim painel-faturas
journalctl -u postfix -n 50; tail -50 /var/log/mail.log
postsuper -d ALL   # ⚠ apaga toda a fila (só se souber o que faz)
```

## 12. Manutenção deste guia

- Pasta local: `C:\Users\JJ\.zcode\workspace\default\smtp-servidor\` (guia, `painel.py`, `setup-smtp-ubuntu.sh`, `enviar-faturas.sh`, `instalar-painel-v4.sh` histórico, `testes/` com a bateria de testes, `instalador-github.txt` com o comando).
- Cópia em nuvem: repo **https://github.com/lostofc77-cpu/smtp-instalador** (painel.py + instalar.sh + este guia + README).
- Fluxo de atualização: editar `painel.py` → rodar testes (`testes/`) → publicar no repo → na VPS rodar `atualizar-painel`.
