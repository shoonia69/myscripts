# myscripts

Набор личных скриптов для быстрой подготовки сервера.

## prep_vpn

Основной инсталлятор:

```bash
prep_vpn/install.sh
```

Он заменяет старые раздельные скрипты подготовки VM, установки Docker и установки Portainer.

### Быстрый запуск с GitHub

Запустить интерактивное меню:

```bash
curl -fsSL https://raw.githubusercontent.com/shoonia69/myscripts/refs/heads/main/prep_vpn/install.sh | sudo bash
```

Запустить все этапы сразу:

```bash
curl -fsSL https://raw.githubusercontent.com/shoonia69/myscripts/refs/heads/main/prep_vpn/install.sh | sudo bash -s -- --all
```

### Локальный запуск

```bash
cd prep_vpn
sudo ./install.sh
```

По умолчанию откроется меню:

```text
1) System update and base utilities
2) Install Docker
3) Install Portainer
a) Run all
```

Можно выбрать несколько пунктов через запятую, например `1,2`.

### Запуск отдельных этапов

Обновить систему и поставить базовые утилиты:

```bash
sudo ./install.sh --system
```

Поставить Docker:

```bash
sudo ./install.sh --docker
```

Поставить Portainer:

```bash
sudo ./install.sh --portainer
```

Запустить все:

```bash
sudo ./install.sh --all
```

### Опции

Добавить пользователя в группу `docker`:

```bash
sudo ./install.sh --docker --docker-user admin
```

Если скрипт запущен через `sudo`, пользователь по умолчанию берется из `SUDO_USER`.

Пропустить проверку `docker run hello-world`:

```bash
sudo ./install.sh --all --no-hello-world
```

### Что устанавливается

Этап `--system`:

- обновление пакетов;
- `nano`;
- `tree`;
- `tmux`;
- `bash-completion`;
- `qemu-guest-agent`;
- `curl`;
- `ca-certificates`;
- дополнительные зависимости для Docker на Debian/Ubuntu.

Этап `--docker`:

- Docker Engine;
- Docker CLI;
- containerd;
- Docker Buildx plugin;
- Docker Compose plugin;
- запуск и включение сервиса Docker.

Этап `--portainer`:

- создает `prep_vpn/portainer/docker-compose.yml`;
- запускает Portainer CE LTS;
- Portainer будет доступен на `https://SERVER_IP:9443`.

## ansible-multiselect

Отдельный Ansible-плейбук с мультивыбором действий и временным inventory:

```bash
cd ansible-multiselect
./run.sh
```
