#!/usr/bin/env bash
# Ryot рядом с Дневниками: bash /opt/dnevnik/ryot.sh
set -euo pipefail
DOMAIN="${DOMAIN:-ryot.ivansereda.fr}"
DIR=/opt/ryot

apt-get update -qq
apt-get install -y -qq docker.io docker-compose-v2 openssl
systemctl enable --now docker

mkdir -p "$DIR"
cd "$DIR"
if [ ! -f .env ]; then
  printf 'PG_PASS=%s\nADMIN_TOKEN=%s\n' "$(openssl rand -hex 24)" "$(openssl rand -hex 32)" > .env
  chmod 600 .env
fi
sed -i '/^TMDB=$/d; /«/d' .env
if ! grep -q '^TMDB=.' .env; then
  echo
  read -rsp "Вставьте TMDB API Read Access Token (eyJ...) и нажмите Enter: " T; echo
  [ -n "$T" ] || { echo "Токен пустой, запустите ещё раз"; exit 1; }
  echo "TMDB=$T" >> .env
fi

cat > docker-compose.yml <<YML
services:
  ryot-db:
    image: postgres:18-alpine
    restart: unless-stopped
    volumes:
      - pgdata:/var/lib/postgresql
    environment:
      - TZ=Europe/Paris
      - POSTGRES_DB=postgres
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=\${PG_PASS}

  ryot:
    image: ignisda/ryot:v10
    restart: unless-stopped
    depends_on:
      - ryot-db
    ports:
      - "127.0.0.1:8000:8000"
    environment:
      - TZ=Europe/Paris
      - FRONTEND_URL=https://$DOMAIN
      - DATABASE_URL=postgres://postgres:\${PG_PASS}@ryot-db:5432/postgres
      - SERVER_ADMIN_ACCESS_TOKEN=\${ADMIN_TOKEN}
      - MOVIES_AND_SHOWS_TMDB_ACCESS_TOKEN=\${TMDB}
      - USERS_ALLOW_REGISTRATION=false

volumes:
  pgdata:
YML

docker compose pull -q
docker compose up -d

if ! grep -q "$DOMAIN" /etc/caddy/Caddyfile; then
  cat >> /etc/caddy/Caddyfile <<CADDY

$DOMAIN {
	encode gzip
	reverse_proxy 127.0.0.1:8000
}
CADDY
fi
systemctl reload caddy

echo "Жду запуска Ryot..."
for i in $(seq 1 60); do
  if curl -s -o /dev/null http://127.0.0.1:8000; then break; fi
  sleep 3
done
docker compose ps
echo
echo "Готово: https://$DOMAIN"
