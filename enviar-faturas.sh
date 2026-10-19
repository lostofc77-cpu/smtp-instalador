#!/usr/bin/env bash
#===============================================================================
# enviar-faturas.sh — disparo em lote de faturas em PDF (sem senha SMTP)
#
# Envia via /usr/sbin/sendmail local (localhost é confiável no Postfix):
# nao guarda senha em lugar nenhum e sai assinado com DKIM.
#
# USO NA VPS (como root):
#   1. Suba os PDFs para /root/faturas/ (WinSCP arrasta-e-solta)
#   2. Edite a lista:  nano /root/clientes.csv
#      Formato de cada linha (separe por ponto-e-virgula):
#        Nome do cliente;email@cliente.com;/root/faturas/arquivo.pdf
#      Linhas comecando com # sao ignoradas
#   3. Rode:  bash /root/enviar-faturas.sh
#===============================================================================
FROM_NAME="CardFat Promo"
FROM_ADDR="faturas@cardfatpromo.com"
LISTA="/root/clientes.csv"
ASSUNTO="Sua fatura"          # edite se quiser assunto fixo diferente

[ -f "$LISTA" ] || { echo "ERRO: crie a lista primeiro: nano $LISTA"; exit 1; }

while IFS=';' read -r NOME EMAIL PDF; do
  [ -z "${EMAIL:-}" ] && continue
  case "$EMAIL" in \#*) continue;; esac
  if [ ! -f "$PDF" ]; then echo ">>> FALHOU (PDF nao achado): $EMAIL -> $PDF"; continue; fi
  if python3 - "$NOME" "$EMAIL" "$PDF" "$FROM_NAME" "$FROM_ADDR" "$ASSUNTO" <<'PY'
import sys, subprocess
nome, to, pdf, from_name, from_addr, subject = sys.argv[1:7]
from email.message import EmailMessage
msg = EmailMessage()
msg["From"] = f"{from_name} <{from_addr}>"
msg["To"] = to
msg["Subject"] = subject
msg.set_content(f"Ola {nome},\n\nSegue em anexo a sua fatura.\n\n"
                f"Qualquer duvida, responda este e-mail.\n\n{from_name}")
with open(pdf, "rb") as f:
    msg.add_attachment(f.read(), maintype="application", subtype="pdf",
                       filename=pdf.rsplit("/", 1)[-1])
sys.exit(subprocess.run(["/usr/sbin/sendmail", "-t", "-oi"],
                        input=msg.as_bytes()).returncode)
PY
  then echo ">>> OK: $EMAIL"; else echo ">>> FALHOU: $EMAIL"; fi
  sleep 3   # ritmo saudavel (aquecimento de dominio novo)
done < "$LISTA"
echo ">>> Concluido. Conferir fila com: postqueue -p"
