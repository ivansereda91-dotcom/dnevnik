#!/usr/bin/env bash
# Установка «Дневников»: bash /opt/dnevnik/setup.sh
set -euo pipefail
DOMAIN="${DOMAIN:-dnevnik.ivansereda.fr}"
APP=/opt/dnevnik
DATA=/var/lib/dnevnik
PORT=8090

apt-get update -qq
apt-get install -y -qq python3-venv caddy

mkdir -p "$DATA"
python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"

if [ ! -f "$APP/.env" ]; then
  echo
  read -rsp "Придумайте пароль для входа в Дневники: " PW; echo
  read -rsp "Повторите пароль: " PW2; echo
  [ "$PW" = "$PW2" ] || { echo "Пароли не совпали, запустите ещё раз"; exit 1; }
  HASH=$(PW="$PW" python3 -c 'import hashlib,os;s=os.urandom(16);print(s.hex()+"$"+hashlib.scrypt(os.environ["PW"].encode(),salt=s,n=2**14,r=8,p=1).hex())')
  SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
  printf 'PASSWORD_HASH=%s\nSECRET=%s\nDB_PATH=%s/dnevnik.db\n' "$HASH" "$SECRET" "$DATA" > "$APP/.env"
  chmod 600 "$APP/.env"
fi

cat > /etc/systemd/system/dnevnik.service <<UNIT
[Unit]
Description=Dnevniki (kino + books)
After=network.target

[Service]
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
ExecStart=$APP/venv/bin/uvicorn main:app --host 127.0.0.1 --port $PORT --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/caddy/Caddyfile <<CADDY
$DOMAIN {
	encode gzip
	reverse_proxy 127.0.0.1:$PORT
}
CADDY

if command -v ufw >/dev/null; then ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; fi
systemctl daemon-reload
systemctl enable --now dnevnik
systemctl restart dnevnik caddy
sleep 2
systemctl is-active dnevnik caddy
echo
echo "Готово: https://$DOMAIN"
