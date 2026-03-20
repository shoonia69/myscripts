#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="${SCRIPT_DIR}/docker-compose/telemt-docker"
CFG_DIR="${BASE_DIR}/telemt-config"
CFG_FILE="${CFG_DIR}/telemt.toml"
COMPOSE_FILE="${BASE_DIR}/docker-compose.yml"

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1" >&2; exit 1; }; }

need_cmd docker
docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 not available (docker compose)" >&2; exit 1; }

mkdir -p "$CFG_DIR"

# Генерация секрета при первом запуске (32 hex chars)
if [[ ! -f "$CFG_FILE" ]]; then
  SECRET="$(openssl rand -hex 16)"

  cat >"$CFG_FILE" <<EOF
log_level = "normal"

[general.modes]
classic = false
secure = false
tls = true

[general.links]
show = "*"

[server]
port = 443

[server.api]
enabled = true
listen = "127.0.0.1:9091"
whitelist = ["127.0.0.0/8"]
minimal_runtime_enabled = false
minimal_runtime_cache_ttl_ms = 1000

[[server.listeners]]
ip = "0.0.0.0"

[censorship]
tls_domain = "rutube.ru"
mask = true
tls_emulation = true
tls_front_dir = "tlsfront"

[access.users]
hello = "${SECRET}"
EOF
fi

# Права (без world-writable)
chmod 777 "$CFG_DIR"
chmod 666 "$CFG_FILE"

# Compose файл создаём только если его нет (чтобы не затирать правки)
if [[ ! -f "$COMPOSE_FILE" ]]; then
  cat >"$COMPOSE_FILE" <<'EOF'
services:
  telemt:
    image: whn0thacked/telemt-docker:latest
    container_name: telemt
    restart: unless-stopped

    # Лучше попробовать без root, используя NET_BIND_SERVICE
    # user: "root"

    environment:
      RUST_LOG: "info"

    command: ["/etc/telemt/telemt.toml"]
    volumes:
      - ./telemt-config:/etc/telemt

    network_mode: host

    cap_drop:
      - ALL
    cap_add:
      - NET_BIND_SERVICE

    read_only: true
    tmpfs:
      - /tmp:rw,nosuid,nodev,noexec,size=16m

    ulimits:
      nofile:
        soft: 65536
        hard: 65536

    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
EOF
fi

cd "$BASE_DIR"
docker compose config >/dev/null
docker compose up -d