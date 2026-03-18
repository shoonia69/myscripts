#!/bin/bash

mkdir -p docker-compose/telemt-docker/telemt-config
touch docker-compose/telemt-docker/telemt-config/telemt.toml
chmod 777 docker-compose/telemt-docker/telemt-config
chmod 666 docker-compose/telemt-docker/telemt-config/telemt.toml

cat <<'EOF' > docker-compose/telemt-docker/telemt-config/telemt.toml

log_level = "normal"

[general.modes]
classic = false
secure = false
tls = true

[general.links]
show = "*"
# show = ["alice", "bob"] # Only show links for alice and bob
# show = "*"              # Show links for all users
# public_host = "proxy.example.com"  # Host (IP or domain) for tg:// links
# public_port = 443                  # Port for tg:// links (default: server.port)

# === Server Binding ===
[server]
port = 443
# proxy_protocol = false           # Enable if behind HAProxy/nginx with PROXY protocol
# metrics_port = 9090
# metrics_listen = "0.0.0.0:9090"  # Listen address for metrics (overrides metrics_port)
# metrics_whitelist = ["127.0.0.1", "::1", "0.0.0.0/0"]

[server.api]
enabled = true
listen = "0.0.0.0:9091"
whitelist = ["127.0.0.0/8"]
minimal_runtime_enabled = false
minimal_runtime_cache_ttl_ms = 1000

# Listen on multiple interfaces/IPs - IPv4
[[server.listeners]]
ip = "0.0.0.0"

# === Anti-Censorship & Masking ===
[censorship]
tls_domain = "rutube.ru"
mask = true
tls_emulation = true        # Fetch real cert lengths and emulate TLS records
tls_front_dir = "tlsfront"   # Cache directory for TLS emulation

[access.users]
# format: "username" = "32_hex_chars_secret"
hello = "a3f9c2e7b1d84f6a9c0e5b7d2f4a8c1e"
EOF

touch docker-compose/telemt-docker/docker-compose.yml
cat <<'EOF' > docker-compose/telemt-docker/docker-compose.yml
services:
  telemt:
    image: whn0thacked/telemt-docker:latest
    container_name: telemt
    restart: unless-stopped

    # ---------------------------------------------------------------
    # Root user requirement for binding privileged ports (<1024)
    # The default image runs as 'nonroot' to minimize attack vectors.
    # Uncomment the line below to run as root ONLY if you need to bind
    # to port 443 and encounter 'os error 13'.
    # ---------------------------------------------------------------
    user: "root"

    # Telemt uses RUST_LOG for verbosity (optional)
    environment:
      RUST_LOG: "info"

    # ---------------------------------------------------------------
    # API Configuration writes (Atomic Config Save)
    # The API performs atomic writes (creates a .tmp file and renames it).
    # To allow the API to save changes to the config, we MUST mount the 
    # ENTIRE directory (not just the file) and ensure it is writable.
    # We override the default command to point to the mounted file.
    # ---------------------------------------------------------------
    command: ["/etc/telemt/telemt.toml"]
    volumes:
      - ./telemt-config:/etc/telemt

    # ---------------------------------------------------------------
    # Host network mode: the container uses the host's network stack
    # directly. The "ports" section is IGNORED in this mode — Telemt
    # binds to host ports as specified in telemt.toml.
    #
    # To use Docker-managed port mapping instead, comment out
    # "network_mode: host" and uncomment the "ports" section below.
    # ---------------------------------------------------------------
    #network_mode: host

    ports:
      - "10443:443/tcp"
    #   # If you enable metrics_port=9090 in config:
    #   # - "127.0.0.1:9090:9090/tcp"

    # Hardening
    # ---------------------------------------------------------------
    # ⚠️ If you uncommented `user: "root"` above to bind to port 443,
    # you MUST comment out the two lines below, as they prevent
    # gaining the necessary privileges for binding restricted ports.
    # ---------------------------------------------------------------
    #security_opt:
    #  - no-new-privileges:true
    cap_drop:
      - ALL
    cap_add:
      - NET_BIND_SERVICE
    read_only: true
    tmpfs:
      - /tmp:rw,nosuid,nodev,noexec,size=16m

    # Resource limits (optional)
    deploy:
      resources:
        limits:
          cpus: "0.50"
          memory: 256M
        reservations:
          cpus: "0.25"
          memory: 128M

    # File descriptor limits (critical for a high-load server!)
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

cd docker-compose/telemt-docker
docker compose up -d