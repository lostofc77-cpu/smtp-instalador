#!/usr/bin/env bash
# Painel de Faturas v5 - instalador/atualizador de comando unico (GitHub + md5)
set -e
RAIZ=/root/painel
RAW=https://raw.githubusercontent.com/lostofc77-cpu/smtp-instalador/main/painel.py
INSTURL=https://raw.githubusercontent.com/lostofc77-cpu/smtp-instalador/main/instalar.sh
mkdir -p "$RAIZ"
systemctl stop painel-faturas 2>/dev/null || true
if [ -f "$RAIZ/painel.py" ]; then grep -oP '(?<=ACCESS_CODE = ")[^"]+' "$RAIZ/painel.py" | head -1 > /root/codigo_painel.txt || true; fi
curl -fsSL "$RAW" -o "$RAIZ/painel.py"
ESP="dd2f413f7655ae7344782d29b495d4ae"
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
printf '#!/usr/bin/env bash
curl -fsSL "%s" | bash
' "$INSTURL" > /usr/local/bin/atualizar-painel
chmod +x /usr/local/bin/atualizar-painel
sleep 2
echo "====================================================="
echo " PAINEL v5: $(systemctl is-active painel-faturas) | HTTP: $(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/)"
echo " CODIGO DE ACESSO: $(grep -oP '(?<=ACCESS_CODE = ")[^"]+' "$RAIZ/painel.py")"
echo " Para atualizar no futuro, basta rodar: atualizar-painel"
echo "====================================================="
rm -f /root/codigo_painel.txt
