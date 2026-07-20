#!/usr/bin/env bash
#
# registry/create-user.sh
#
# Создаёт или обновляет пользователя Docker Registry в файле
# registry/auth/htpasswd (Basic Auth, bcrypt).
#
# Использование:
#   ./create-user.sh <username>
#
# Пароль запрашивается интерактивно самой утилитой htpasswd (ввод скрыт,
# требуется повтор для подтверждения). Скрипт никогда не получает,
# не хранит и не выводит пароль в консоль или лог.
#
# Предназначен для запуска на Linux (Ubuntu VPS). Перед первым запуском:
#   chmod +x registry/create-user.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTH_DIR="${SCRIPT_DIR}/auth"
HTPASSWD_FILE="${AUTH_DIR}/htpasswd"

err() {
    echo "Ошибка: $*" >&2
    exit 1
}

# --- аргументы -----------------------------------------------------------
if [[ $# -ne 1 ]]; then
    err "укажите имя пользователя одним аргументом. Использование: $0 <username>"
fi

USERNAME="$1"

if [[ -z "$USERNAME" ]]; then
    err "имя пользователя не может быть пустым"
fi

if [[ ! "$USERNAME" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    err "имя пользователя содержит недопустимые символы (разрешены буквы, цифры, '.', '_', '-')"
fi

# --- зависимости -----------------------------------------------------------
if ! command -v htpasswd >/dev/null 2>&1; then
    err "утилита 'htpasswd' не найдена. Установите: sudo apt-get update && sudo apt-get install -y apache2-utils"
fi

# --- подготовка директории -------------------------------------------------
mkdir -p "$AUTH_DIR" || err "не удалось создать директорию ${AUTH_DIR}"
chmod 700 "$AUTH_DIR"

FILE_EXISTS=false
[[ -f "$HTPASSWD_FILE" ]] && FILE_EXISTS=true

# --- защита от случайной перезаписи/удаления существующих пользователей ---
# Точное сравнение первого поля (до ":") через awk — НЕ regex-поиск.
# grep -E интерпретировал бы спецсимволы имени пользователя (., *, [ и т.д.)
# как регулярное выражение, что могло бы дать ложное совпадение с другим
# именем; awk здесь сравнивает строки буквально ($1 == u).
user_exists() {
    awk -F: -v u="$USERNAME" '$1 == u { found = 1 } END { exit (found ? 0 : 1) }' "$HTPASSWD_FILE"
}

if [[ "$FILE_EXISTS" == true ]]; then
    if user_exists; then
        echo "Пользователь '${USERNAME}' уже существует в ${HTPASSWD_FILE}."
        read -r -p "Обновить его пароль? [y/N]: " CONFIRM
        case "$CONFIRM" in
            y|Y|yes|YES) ;;
            *) echo "Отменено пользователем. Изменений не внесено."; exit 0 ;;
        esac
    else
        echo "Файл ${HTPASSWD_FILE} уже существует. Пользователь '${USERNAME}' будет добавлен к нему."
        echo "(Существующие пользователи затронуты не будут.)"
    fi

    # Резервная копия перед любым изменением существующего файла.
    BACKUP_FILE="${HTPASSWD_FILE}.bak.$(date +%Y%m%d%H%M%S)"
    cp "$HTPASSWD_FILE" "$BACKUP_FILE" || err "не удалось создать резервную копию ${BACKUP_FILE}"
    echo "Резервная копия сохранена: ${BACKUP_FILE}"
fi

# --- создание/обновление записи --------------------------------------------
# ВАЖНО: флаг -c (create) применяется ТОЛЬКО когда файла ещё нет.
# Если файл уже существует, -c перезаписал бы его целиком и удалил
# всех остальных пользователей — поэтому здесь ветвление обязательно.
#
# Пароль вводится интерактивно самой htpasswd (без флага -b), поэтому
# он не попадает в аргументы командной строки/историю/список процессов
# этого скрипта.
echo "Ввод пароля для пользователя Registry '${USERNAME}':"
if [[ "$FILE_EXISTS" == true ]]; then
    if ! htpasswd -B "$HTPASSWD_FILE" "$USERNAME"; then
        err "не удалось обновить пользователя '${USERNAME}' в ${HTPASSWD_FILE}. Резервная копия: ${BACKUP_FILE:-нет}"
    fi
else
    if ! htpasswd -c -B "$HTPASSWD_FILE" "$USERNAME"; then
        err "не удалось создать ${HTPASSWD_FILE}"
    fi
fi

# Права ограничены до владельца (600/700): htpasswd содержит bcrypt-хэши,
# читать их посторонним локальным пользователям VPS не нужно. Для
# bind-mount в Registry (docker-compose.yml: ./registry/auth:/auth:ro)
# запускайте этот скрипт от того же пользователя (или через sudo), от
# которого выполняется `docker compose up` — обычно root на VPS,
# — чтобы владелец файла совпадал с тем, кто будет его читать.
chmod 600 "$HTPASSWD_FILE"

echo
echo "Готово: пользователь '${USERNAME}' создан/обновлён в ${HTPASSWD_FILE}."
echo "Пароль нигде не сохранён этим скриптом и не выводился на экран."
