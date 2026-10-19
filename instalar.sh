#!/usr/bin/env bash
# Painel de Faturas v4 - instalador de comando unico (baixa do GitHub e verifica md5)
set -e
RAIZ=/root/painel
RAW=https://raw.githubusercontent.com/lostofc77-cpu/smtp-instalador/main/painel.py
mkdir -p "$RAIZ"
systemctl stop painel-faturas 2>/dev/null || true
if [ -f "$RAIZ/painel.py" ]; then grep -oP '(?<=ACCESS_CODE = ")[^"]+' "$RAIZ/painel.py" | head -1 > /root/codigo_painel.txt || true; fi
curl -fsSL "$RAW" -o "$RAIZ/painel.py"
ESP="f0e338494d02294218364a2bfbd048e3"
AT=$(md5sum "$RAIZ/painel.py" | cut -d" " -f1)
if [ "$AT" != "$ESP" ]; then echo ">>> DOWNLOAD CORROMPIDO ($AT). Rode o comando novamente."; exit 1; fi
CODE=$(tr -d "
" < /root/codigo_painel.txt 2>/dev/null || true)
if [ -z "$CODE" ]; then CODE=$(openssl rand -hex 4); fi
sed -i "s|ALTERE-ESTE-CODIGO|$CODE|" "$RAIZ/painel.py"
chmod 600 "$RAIZ/painel.py"
printf '[Unit]
Description=Painel de envio de faturas
After=network-online.target
[Service]
ExecStart=/usr/bin/python3 /root/painel/painel.py
Restart=always
[Install]
WantedBy=multi-user.target
' > /etc/systemd/system/painel-faturas.service
systemctl daemon-reload
systemctl enable painel-faturas >/dev/null 2>&1 || true
systemctl restart painel-faturas
ufw allow 8080/tcp >/dev/null 2>&1 || true
sleep 2
echo "====================================================="
echo " PAINEL v4: $(systemctl is-active painel-faturas) | HTTP: $(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/)"
echo " CODIGO DE ACESSO: $(grep -oP '(?<=ACCESS_CODE = ")[^"]+' "$RAIZ/painel.py")"
echo " URL: http://$(curl -fsS4 --max-time 8 https://api.ipify.org 2>/dev/null || echo SEU_IP):8080"
echo "====================================================="
rm -f /root/codigo_painel.txt
