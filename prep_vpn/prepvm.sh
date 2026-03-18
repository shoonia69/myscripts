#!/bin/bash

dnf update -y
dnf install nano tree tmux bash-completion qemu-guest-agent -y

set -e

echo "🐳 Универсальный установщик Docker для Linux"

# Проверка root
if [[ "$EUID" -ne 0 ]]; then
  echo "❌ Пожалуйста, запустите скрипт от имени root (или с sudo)"
  exit 1
fi

# Определяем дистрибутив
. /etc/os-release

DISTRO=$ID
VERSION=$VERSION_ID

echo "📦 Обнаружена система: $PRETTY_NAME"

install_docker_debian() {
  apt-get update
  apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release

  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/$ID/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg

  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/$ID \
    $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

install_docker_rhel() {
  dnf install -y dnf-utils device-mapper-persistent-data lvm2 curl

  dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

  dnf install -y docker-ce docker-ce-cli containerd.io
}

install_docker_fedora() {
  dnf install -y dnf-plugins-core curl

  dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo

  dnf install -y docker-ce docker-ce-cli containerd.io
}

case "$DISTRO" in
  ubuntu|debian)
    install_docker_debian
    ;;
  centos|rhel|almalinux|rocky)
    install_docker_rhel
    ;;
  fedora)
    install_docker_fedora
    ;;
  *)
    echo "❌ Этот скрипт пока не поддерживает дистрибутив: $DISTRO"
    exit 1
    ;;
esac

# Запуск и автозапуск Docker
echo "🚀 Запуск Docker..."
systemctl start docker
systemctl enable docker

# Добавление пользователя в группу docker
if ! groups $SUDO_USER | grep -q '\bdocker\b'; then
  usermod -aG docker $SUDO_USER
  echo "👤 Пользователь $SUDO_USER добавлен в группу docker"
  echo "🔁 Перезапустите терминал или выполните: newgrp docker"
fi

# Проверка
echo "🔍 Проверка установки Docker..."
docker --version && docker run --rm hello-world

echo "✅ Docker успешно установлен и работает!"


