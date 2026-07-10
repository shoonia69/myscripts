#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PORTAINER_DIR="${SCRIPT_DIR}/portainer"
PORTAINER_COMPOSE="${PORTAINER_DIR}/docker-compose.yml"
DOCKER_USER="${SUDO_USER:-}"
RUN_SYSTEM=false
RUN_DOCKER=false
RUN_PORTAINER=false
RUN_HELLO_WORLD=true

log() {
  printf '[prep-vpn] %s\n' "$*"
}

die() {
  printf '[prep-vpn] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  ./install.sh                 Interactive menu
  ./install.sh --all           Run system prep, Docker install, Portainer install
  ./install.sh --system        Update OS and install base utilities
  ./install.sh --docker        Install and enable Docker
  ./install.sh --portainer     Install/start Portainer with Docker Compose

Options:
  --docker-user USER           Add USER to docker group after install
  --no-hello-world             Skip docker hello-world test
  -h, --help                   Show this help

Examples:
  sudo ./install.sh --all --docker-user admin
  sudo ./install.sh --docker --portainer
EOF
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "run as root or with sudo"
  fi
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing command: $1"
}

detect_os() {
  [[ -r /etc/os-release ]] || die "/etc/os-release not found"
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_ID="${ID:-unknown}"
  OS_VERSION_ID="${VERSION_ID:-unknown}"
  OS_PRETTY_NAME="${PRETTY_NAME:-unknown Linux}"

  if command -v apt-get >/dev/null 2>&1; then
    PKG_MANAGER="apt"
  elif command -v dnf >/dev/null 2>&1; then
    PKG_MANAGER="dnf"
  elif command -v yum >/dev/null 2>&1; then
    PKG_MANAGER="yum"
  elif command -v apk >/dev/null 2>&1; then
    PKG_MANAGER="apk"
  else
    die "supported package manager not found: apt, dnf, yum or apk"
  fi

  log "detected ${OS_PRETTY_NAME}; package manager: ${PKG_MANAGER}"
}

parse_args() {
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      --all)
        RUN_SYSTEM=true
        RUN_DOCKER=true
        RUN_PORTAINER=true
        ;;
      --system)
        RUN_SYSTEM=true
        ;;
      --docker)
        RUN_DOCKER=true
        ;;
      --portainer)
        RUN_PORTAINER=true
        ;;
      --docker-user)
        [[ "${2:-}" ]] || die "--docker-user requires a value"
        DOCKER_USER="$2"
        shift
        ;;
      --no-hello-world)
        RUN_HELLO_WORLD=false
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "unknown argument: $1"
        ;;
    esac
    shift
  done
}

interactive_menu() {
  cat <<'EOF'

Select actions, comma-separated:
  1) System update and base utilities
  2) Install Docker
  3) Install Portainer
  a) Run all
EOF

  read -r -p "Choice [a]: " choice
  choice="${choice:-a}"
  choice="$(printf '%s' "$choice" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"

  IFS=',' read -r -a items <<<"$choice"
  for item in "${items[@]}"; do
    case "$item" in
      1) RUN_SYSTEM=true ;;
      2) RUN_DOCKER=true ;;
      3) RUN_PORTAINER=true ;;
      a|all)
        RUN_SYSTEM=true
        RUN_DOCKER=true
        RUN_PORTAINER=true
        ;;
      "") ;;
      *) die "unknown menu item: $item" ;;
    esac
  done
}

pkg_update() {
  case "$PKG_MANAGER" in
    apt)
      apt-get update
      ;;
    dnf)
      dnf makecache -y
      ;;
    yum)
      yum makecache -y
      ;;
    apk)
      apk update
      ;;
  esac
}

pkg_upgrade() {
  case "$PKG_MANAGER" in
    apt)
      DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y
      ;;
    dnf)
      dnf upgrade -y
      ;;
    yum)
      yum update -y
      ;;
    apk)
      apk upgrade
      ;;
  esac
}

pkg_install() {
  case "$PKG_MANAGER" in
    apt)
      DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
      ;;
    dnf)
      dnf install -y "$@"
      ;;
    yum)
      yum install -y "$@"
      ;;
    apk)
      apk add --no-cache "$@"
      ;;
  esac
}

install_system_tools() {
  log "updating system and installing base utilities"
  pkg_update
  pkg_upgrade

  case "$PKG_MANAGER" in
    apt)
      pkg_install nano tree tmux bash-completion qemu-guest-agent curl ca-certificates gnupg lsb-release
      ;;
    dnf|yum)
      pkg_install nano tree tmux bash-completion qemu-guest-agent curl ca-certificates
      ;;
    apk)
      pkg_install nano tree tmux bash-completion qemu-guest-agent curl ca-certificates
      ;;
  esac

  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files qemu-guest-agent.service >/dev/null 2>&1; then
    systemctl enable --now qemu-guest-agent.service || true
  fi
}

docker_installed() {
  command -v docker >/dev/null 2>&1
}

install_docker_debian() {
  pkg_update
  pkg_install ca-certificates curl gnupg lsb-release
  install -m 0755 -d /etc/apt/keyrings

  local keyring="/etc/apt/keyrings/docker.gpg"
  local repo_file="/etc/apt/sources.list.d/docker.list"
  curl -fsSL "https://download.docker.com/linux/${OS_ID}/gpg" | gpg --dearmor -o "${keyring}.tmp"
  install -m 0644 "${keyring}.tmp" "$keyring"
  rm -f "${keyring}.tmp"

  local arch
  arch="$(dpkg --print-architecture)"
  local codename
  codename="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-}")"
  if [[ -z "$codename" ]]; then
    codename="$(lsb_release -cs)"
  fi

  printf 'deb [arch=%s signed-by=%s] https://download.docker.com/linux/%s %s stable\n' \
    "$arch" "$keyring" "$OS_ID" "$codename" >"$repo_file"

  pkg_update
  pkg_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

install_docker_rhel() {
  if [[ "$PKG_MANAGER" == "dnf" ]]; then
    pkg_install dnf-plugins-core curl ca-certificates
    local repo_os="centos"
    [[ "$OS_ID" == "fedora" ]] && repo_os="fedora"
    dnf config-manager --add-repo "https://download.docker.com/linux/${repo_os}/docker-ce.repo"
    dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  else
    pkg_install yum-utils curl ca-certificates
    yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
}

install_docker_alpine() {
  pkg_update
  pkg_install docker docker-cli docker-cli-compose
  rc-update add docker boot || true
  service docker start || true
}

install_docker() {
  if docker_installed; then
    log "Docker is already installed: $(docker --version)"
  else
    log "installing Docker"
    case "$PKG_MANAGER" in
      apt)
        case "$OS_ID" in
          debian|ubuntu) install_docker_debian ;;
          *) die "Docker apt repo is not configured for OS_ID=${OS_ID}" ;;
        esac
        ;;
      dnf|yum)
        install_docker_rhel
        ;;
      apk)
        install_docker_alpine
        ;;
    esac
  fi

  if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
    systemctl enable --now docker
  elif command -v service >/dev/null 2>&1; then
    service docker start || true
  fi

  if [[ -n "$DOCKER_USER" && "$DOCKER_USER" != "root" ]]; then
    if id "$DOCKER_USER" >/dev/null 2>&1; then
      getent group docker >/dev/null 2>&1 || groupadd docker
      usermod -aG docker "$DOCKER_USER"
      log "added ${DOCKER_USER} to docker group; re-login is required"
    else
      log "skip docker group: user ${DOCKER_USER} does not exist"
    fi
  fi

  if [[ "$RUN_HELLO_WORLD" == true ]]; then
    docker --version
    docker run --rm hello-world
  fi
}

install_portainer() {
  docker_installed || install_docker
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is not available"

  log "installing Portainer in ${PORTAINER_DIR}"
  mkdir -p "$PORTAINER_DIR"

  if [[ ! -f "$PORTAINER_COMPOSE" ]]; then
    cat >"$PORTAINER_COMPOSE" <<'EOF'
services:
  portainer:
    image: portainer/portainer-ce:lts
    container_name: portainer
    restart: unless-stopped
    ports:
      - "9443:9443"
      - "8000:8000"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - portainer_data:/data

volumes:
  portainer_data:
EOF
  fi

  docker compose -f "$PORTAINER_COMPOSE" config >/dev/null
  docker compose -f "$PORTAINER_COMPOSE" up -d
  log "Portainer: https://SERVER_IP:9443"
}

main() {
  require_root
  parse_args "$@"
  detect_os

  if [[ "$RUN_SYSTEM" == false && "$RUN_DOCKER" == false && "$RUN_PORTAINER" == false ]]; then
    interactive_menu
  fi

  [[ "$RUN_SYSTEM" == true ]] && install_system_tools
  [[ "$RUN_DOCKER" == true ]] && install_docker
  [[ "$RUN_PORTAINER" == true ]] && install_portainer

  log "done"
}

main "$@"
