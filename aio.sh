#!/usr/bin/env bash
set -Eeuo pipefail

# Меню-раннер для набора пунктов.
# Требования:
# - можно указать несколько пунктов за раз: "1 2 3", "1,2,3", "1-3", "4.1 2 4.2"
# - выполняются строго в порядке, как указал пользователь
# - легко дополнять новыми пунктами: добавьте action_* и зарегистрируйте в ACTION_ORDER/DESC/URL

# ---------- Настройки пунктов (расширяемо) ----------
declare -a ACTION_ORDER=(
  "1"
  "2"
  "3"
  "4.1"
  "4.2"
)

declare -A DESC=(
  ["1"]="Подготовка ВМ: апдейт, установка утилит"
  ["2"]="Установка Docker"
  ["3"]="Установка Portainer (по желанию)"
  ["4.1"]="Установка MTProxy на порт 443 (host mode, порт 443 должен быть свободен)"
  ["4.2"]="Установка MTProxy на порт 10443 (переадресация в контейнер)"
)

declare -A URL=(
  ["1"]="https://raw.githubusercontent.com/shoonia69/myscripts/refs/heads/main/prep_vpn/1-prepvm.sh"
  ["2"]="https://raw.githubusercontent.com/shoonia69/myscripts/refs/heads/main/prep_vpn/2-dockerinstall.sh"
  ["3"]="https://raw.githubusercontent.com/shoonia69/myscripts/refs/heads/main/prep_vpn/3-portainerinstall.sh"
  ["4.1"]="https://raw.githubusercontent.com/shoonia69/myscripts/refs/heads/main/prep_vpn/4-1-mtproxy443.sh"
  ["4.2"]="https://raw.githubusercontent.com/shoonia69/myscripts/refs/heads/main/prep_vpn/4-2-mtproxy10443.sh"
)

# ---------- Общие функции ----------
need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Не найдена команда: $1" >&2; exit 1; }; }

run_remote_script() {
  local id="$1"
  local url="${URL[$id]}"
  [[ -n "${url:-}" ]] || { echo "Нет URL для пункта: $id" >&2; return 1; }

  echo "==> [$id] ${DESC[$id]}"
  echo "    $url"
  curl -fsSL "$url" | bash
}

# ---------- Actions (легко добавлять новые) ----------
action_1()   { run_remote_script "1"; }
action_2()   { run_remote_script "2"; }
action_3()   { run_remote_script "3"; }
action_4_1() { run_remote_script "4.1"; }
action_4_2() { run_remote_script "4.2"; }

dispatch() {
  local id="$1"
  case "$id" in
    "1")   action_1 ;;
    "2")   action_2 ;;
    "3")   action_3 ;;
    "4.1") action_4_1 ;;
    "4.2") action_4_2 ;;
    *) echo "Неизвестный пункт: '$id'" >&2; return 1 ;;
  esac
}

print_menu() {
  echo "Доступные пункты:"
  for id in "${ACTION_ORDER[@]}"; do
    printf "  %-4s %s\n" "$id" "${DESC[$id]}"
  done
  cat <<'EOF'

Выбор:
- несколько пунктов:   1 2 3
- через запятую:       1,2,3
- диапазон:            1-3
- смешанный вариант:   1 4.2 2,3

Команды:
  all   - выполнить все по порядку
  menu  - показать меню
  q     - выход
EOF
}

# Разбор ввода: токены, запятые, диапазоны типа 1-3 (только для целых пунктов)
expand_selection() {
  local input="$1"

  # нормализуем разделители
  input="${input//,/ }"
  input="${input//;/ }"

  local -a out=()
  local token a b i

  for token in $input; do
    [[ -z "$token" ]] && continue

    if [[ "$token" == "all" ]]; then
      out+=("${ACTION_ORDER[@]}")
      continue
    fi

    # диапазон 1-3 (только для целых, не для 4.1-4.2)
    if [[ "$token" =~ ^([0-9]+)-([0-9]+)$ ]]; then
      a="${BASH_REMATCH[1]}"
      b="${BASH_REMATCH[2]}"
      if (( a <= b )); then
        for ((i=a; i<=b; i++)); do out+=("$i"); done
      else
        for ((i=a; i>=b; i--)); do out+=("$i"); done
      fi
      continue
    fi

    # обычный токен (1, 4.1, 4.2, ...)
    out+=("$token")
  done

  printf '%s\n' "${out[@]}"
}

validate_ids() {
  local id
  for id in "$@"; do
    [[ -n "${DESC[$id]:-}" ]] || { echo "Некорректный пункт: '$id'" >&2; return 1; }
  done
}

main() {
  need_cmd curl
  need_cmd bash

  print_menu

  while true; do
    echo
    read -r -p "Введите пункты для выполнения (menu/all/q): " line || exit 0
    line="$(echo "$line" | xargs || true)"  # trim

    case "${line,,}" in
      "" ) continue ;;
      q|quit|exit) exit 0 ;;
      menu|m) print_menu; continue ;;
    esac

    mapfile -t selection < <(expand_selection "$line")
    [[ "${#selection[@]}" -gt 0 ]] || continue

    validate_ids "${selection[@]}"

    echo
    echo "Будет выполнено (в указанном порядке): ${selection[*]}"
    read -r -p "Продолжить? [y/N]: " yn
    case "${yn,,}" in
      y|yes) ;;
      *) echo "Отмена."; continue ;;
    esac

    echo
    for id in "${selection[@]}"; do
      dispatch "$id"
      echo
    done

    echo "Готово."
  done
}

main "$@"