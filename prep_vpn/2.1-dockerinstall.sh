#!/bin/bash
set -euo pipefail # 🔒 Стратегическая настройка: ошибка = exit, undefined var = exit, pipefail = exit

echo "🐳 Универсальный улучшенный установщик Docker"

# 🔍 Проверка прав суперпользователя
if [[ "$EUID" -ne 0 ]]; then
  echo "❌ Пожалуйста, запустите скрипт от имени root (sudo)"
  exit 1
fi

# 🎯 Определяем пакетный менеджер и дистрибутив
# Используем os-release, но проверяем наличие пакетных менеджеров для надежности
source /etc/os-release
ID="${ID:-unknown}"
VERSION_ID="${VERSION_ID:-unknown}"
PRETTY_NAME="$PRETTY_NAME"

# 📦 Определение пакетного менеджера
get_package_manager() {
    local pkg_manager=""
    if command -v dnf &>/dev/null; then pkg_manager="dnf"; fi
    elif command -v yum &>/dev/null; then pkg_manager="yum"; fi
    elif command -v apt-get &>/dev/null; then pkg_manager="apt"; fi
    elif command -v apk &>/dev/null; then pkg_manager="apk"; fi
    
    if [[ -z "$pkg_manager" ]]; then
        echo "❌ Неизвестная или отсутствующая система пакетов"
        exit 1
    fi
    echo "$pkg_manager"
}

PKG_MANAGER=$(get_package_manager)
echo "🛠 Пакетный менеджер: $PKG_MANAGER (Дистрибутив: $PRETTY_NAME)"

# 🛡 Проверка: Уже установлен Docker?
check_docker_installed() {
    case "$PKG_MANAGER" in
        apt) dpkg -l docker-ce | grep -q docker-ce ;;
        dnf|yum) rpm -qa docker-ce &>/dev/null ;;
        apk) apk list docker &>/dev/null ;;
    esac
}

# 🚀 Главная функция установки
install_docker() {
    # Обновление зависимостей
    case "$PKG_MANAGER" in
        apt)
            apt-get update
            apt-get install -y \
                ca-certificates curl gnupg lsb-release
            install -m 0755 -d /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/${ID:-debian}/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID:-debian} $(lsb_release -cs) stable" | \
                tee /etc/apt/sources.list.d/docker.list > /dev/null
            ;;
        dnf)
            dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
            # dnf install -y dnf-utils ... (уже есть в base для новых CentOS/RHEL)
            ;;
        yum)
            # Для CentOS 7 и EPEL
            dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
            ;;
        apk)
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] ..."
            apk add --update docker-cli
            ;;
    esac

    # Установка самого Docker
    echo "🚛 Установка компонентов Docker..."
    case "$PKG_MANAGER" in
        apt)
            apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;
        dnf|yum)
            dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;
        apk)
            apk add --update docker-ce-cli docker-cli
            ;;
    esac
    
    echo "✅ Docker успешно установлен!"
}

# 🔁 Выполняем установку только если нужно
if ! check_docker_installed; then
    echo "🔧 Установка Docker..."
    install_docker
else
    echo "💚 Docker уже установлен. Переходим к настройке."
fi

# ⚙️ Запуск и автозапуск
echo "🚀 Управление службами..."
systemctl daemon-reload
systemctl start docker
systemctl enable docker

# 👥 Работа с пользователем (безопасный способ)
CURRENT_USER=$(whoami)
if [[ "$(groups "$CURRENT_USER")" =~ docker ]]; then
    echo "✅ Пользователь $CURRENT_USER уже в группе docker"
else
    groupadd docker 2>/dev/null || true
    usermod -aG docker "$CURRENT_USER"
    echo "👤 Добавлен в группу docker. Перезагрузите терминал."
fi

# 🔍 Финальная проверка
echo "🧪 Проверка версии и Hello World..."
docker --version
docker run --rm hello-world

echo "✨ Готово! Docker работает на сервере: $PRETTY_NAME"
