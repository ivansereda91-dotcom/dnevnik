#!/usr/bin/env bash
# Полностью удаляет Ryot: bash /opt/dnevnik/ryot-remove.sh
# Дневники (dnevnik.ivansereda.fr) и их данные не трогает.
set -uo pipefail
DOMAIN=ryot.ivansereda.fr

echo "== Останавливаю Ryot и удаляю его базу =="
if [ -f /opt/ryot/docker-compose.yml ]; then
  (cd /opt/ryot && docker compose down -v --rmi all)
fi
rm -rf /opt/ryot

echo "== Убираю ryot.ivansereda.fr из Caddy =="
cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak
python3 - <<'PY'
import re
p = "/etc/caddy/Caddyfile"
s = open(p).read()
s = re.sub(r"\n*ryot\.ivansereda\.fr \{[^}]*\}\n?", "\n", s)
open(p, "w").write(s.rstrip() + "\n")
PY
if caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
  systemctl reload caddy
else
  echo "Caddyfile не прошёл проверку, возвращаю как было"
  cp /etc/caddy/Caddyfile.bak /etc/caddy/Caddyfile
fi

echo "== Удаляю Docker (больше ничем не используется) =="
if [ -z "$(docker ps -aq 2>/dev/null)" ]; then
  systemctl disable --now docker docker.socket 2>/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq docker.io docker-compose-v2 >/dev/null
  apt-get autoremove -y -qq >/dev/null
  rm -rf /var/lib/docker /var/lib/containerd
else
  echo "Есть другие контейнеры, Docker оставляю"
fi

echo
grep -q "$DOMAIN" /etc/caddy/Caddyfile && echo "! $DOMAIN всё ещё в Caddyfile" || echo "Ryot удалён."
df -h / | tail -1
