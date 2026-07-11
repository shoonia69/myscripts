#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v ansible-playbook >/dev/null 2>&1; then
  echo "ansible-playbook не найден. Установите Ansible на управляющей машине."
  exit 1
fi

read -r -p "IP конечных хостов через пробел или запятую: " TARGET_HOSTS_INPUT

if [[ -z "$TARGET_HOSTS_INPUT" ]]; then
  echo "Список хостов не может быть пустым."
  exit 1
fi

read -r -p "SSH-пользователь [root]: " SSH_USER
SSH_USER="${SSH_USER:-root}"
SSH_KEY="${HOME}/.ssh/id_ed25519"
ASK_BECOME_PASS=false

if [[ "$SSH_USER" != "root" ]]; then
  read -r -p "Для sudo/become нужен пароль? [y/N]: " BECOME_PASS_CHOICE
  case "${BECOME_PASS_CHOICE,,}" in
    y|yes|д|да) ASK_BECOME_PASS=true ;;
  esac
fi

normalize_hosts() {
  printf '%s' "$1" | tr ',' ' '
}

read -r -a TARGET_HOSTS <<<"$(normalize_hosts "$TARGET_HOSTS_INPUT")"

if [[ "${#TARGET_HOSTS[@]}" -eq 0 ]]; then
  echo "Не найдено ни одного хоста."
  exit 1
fi

if [[ ! -f "${SSH_KEY}.pub" ]]; then
  echo "SSH-ключ не найден, создаю ${SSH_KEY}."
  mkdir -p "${HOME}/.ssh"
  chmod 700 "${HOME}/.ssh"
  ssh-keygen -t ed25519 -N "" -f "$SSH_KEY" -q
fi

for target_host in "${TARGET_HOSTS[@]}"; do
  if ! ssh \
    -i "$SSH_KEY" \
    -o BatchMode=yes \
    -o ConnectTimeout=5 \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "${SSH_USER}@${target_host}" true >/dev/null 2>&1; then
    if ! command -v ssh-copy-id >/dev/null 2>&1; then
      echo "ssh-copy-id не найден. Установите openssh-clients/openssh-client на управляющей машине."
      exit 1
    fi

    echo "Ключа на ${target_host} еще нет, запускаю ssh-copy-id для ${SSH_USER}@${target_host}."
    echo "Если сервер требует пароль пользователя ${SSH_USER}, введите его один раз."
    ssh-copy-id \
      -i "${SSH_KEY}.pub" \
      -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null \
      "${SSH_USER}@${target_host}"
  fi
done

cat <<'MENU'

Выберите действия через запятую, например: 1,2,5
  1) Обновление системы
  2) Установить tree
  3) Установить tmux
  4) Установить bash-completion
  5) Установить Docker
  6) Добавить alias в .bashrc
  a) Выполнить все
MENU

read -r -p "Ваш выбор: " CHOICE

do_update=false
install_tree=false
install_tmux=false
install_bash_completion=false
install_docker=false
configure_bash_aliases=false

normalize_choice() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]'
}

IFS=',' read -r -a ITEMS <<<"$(normalize_choice "$CHOICE")"

for item in "${ITEMS[@]}"; do
  case "$item" in
    1) do_update=true ;;
    2) install_tree=true ;;
    3) install_tmux=true ;;
    4) install_bash_completion=true ;;
    5) install_docker=true ;;
    6) configure_bash_aliases=true ;;
    a|all)
      do_update=true
      install_tree=true
      install_tmux=true
      install_bash_completion=true
      install_docker=true
      configure_bash_aliases=true
      ;;
    "")
      ;;
    *)
      echo "Неизвестный пункт: $item"
      exit 1
      ;;
  esac
done

if [[ "$do_update" == false \
  && "$install_tree" == false \
  && "$install_tmux" == false \
  && "$install_bash_completion" == false \
  && "$install_docker" == false \
  && "$configure_bash_aliases" == false ]]; then
  echo "Ничего не выбрано."
  exit 1
fi

INVENTORY_FILE="$(mktemp)"
trap 'rm -f "$INVENTORY_FILE"' EXIT

{
  echo "[target]"
  printf '%s\n' "${TARGET_HOSTS[@]}"
} >"$INVENTORY_FILE"

export ANSIBLE_HOST_KEY_CHECKING=False

PLAYBOOK_ARGS=(
  -i "$INVENTORY_FILE"
  -u "$SSH_USER"
  --private-key "$SSH_KEY"
  --ssh-common-args="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
  playbook.yml
  -e "do_update=$do_update"
  -e "install_tree=$install_tree"
  -e "install_tmux=$install_tmux"
  -e "install_bash_completion=$install_bash_completion"
  -e "install_docker=$install_docker"
  -e "configure_bash_aliases=$configure_bash_aliases"
  -e "bash_alias_user=$SSH_USER"
)

if [[ "$ASK_BECOME_PASS" == true ]]; then
  PLAYBOOK_ARGS=(--ask-become-pass "${PLAYBOOK_ARGS[@]}")
fi

ansible-playbook "${PLAYBOOK_ARGS[@]}"
