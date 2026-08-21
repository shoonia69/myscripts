# 3x-ui Self-Service Portal

Небольшой сайт на FastAPI: пользователь вводит имя, приложение создаёт клиента в 3x-ui и показывает персональную ссылку на подписку.

## Возможности

- одна форма без регистрации;
- современный API `/panel/api/clients/add` и старый `/panel/api/inbounds/addClient`;
- API Token (рекомендуется) либо логин/пароль панели;
- один или несколько inbound;
- лимит трафика, срок действия, лимит IP и flow через переменные окружения;
- стабильная идемпотентность: повтор того же нормализованного имени возвращает ту же учётную запись;
- CSRF-защита, honeypot, базовый rate limit и security headers;
- запуск непривилегированным пользователем в read-only контейнере.

> Важно: поскольку форма запрашивает только имя, два человека с абсолютно одинаковым именем получат одну учётную запись. Если это неприемлемо, в форму следует добавить второй уникальный признак (например, email или код приглашения).

## Быстрый запуск

```bash
cp .env.example .env
openssl rand -hex 32
```

Вставьте полученную строку в `APP_SECRET`, затем заполните в `.env`:

- `XUI_PANEL_URL` — URL панели, включая её secret web base path;
- `XUI_API_TOKEN` — **Settings → Security → API Token**;
- `XUI_INBOUND_IDS` — `all` для всех текущих и будущих inbound либо ID через запятую;
- `XUI_SUBSCRIPTION_BASE_URL` — публичная основа подписки.

Чтобы правильно определить `XUI_SUBSCRIPTION_BASE_URL`, скопируйте из 3x-ui уже работающую ссылку подписки и удалите из неё последний `subId`. Например:

```text
https://sub.example.com/sub/abcdef123456
                              └─ удалить это
XUI_SUBSCRIPTION_BASE_URL=https://sub.example.com/sub/
```

Запуск:

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8080/healthz
```

Сайт будет доступен на `http://SERVER_IP:8080`.

## Подключение старой панели

Если в панели нет API Token, удалите или закомментируйте `XUI_API_TOKEN` и задайте:

```dotenv
XUI_USERNAME=admin
XUI_PASSWORD=your-password
XUI_API_MODE=auto
```

Режим `auto` проверяет наличие нового API клиентов и автоматически переключается на legacy API. При необходимости режим можно зафиксировать как `modern` или `legacy`.

## Переменные окружения

| Переменная | Обязательна | Назначение |
|---|---:|---|
| `XUI_PANEL_URL` | да | Полный URL панели с base path |
| `XUI_API_TOKEN` | рекомендуется | Bearer-токен панели |
| `XUI_USERNAME`, `XUI_PASSWORD` | если нет token | Сессионная авторизация |
| `XUI_INBOUND_IDS` | да | `all` или ID, например `1,3,5` |
| `XUI_SUBSCRIPTION_BASE_URL` | да | Публичная основа URL подписки |
| `APP_SECRET` | да | Случайная строка минимум 32 символа |
| `XUI_API_MODE` | нет | `auto` (по умолчанию), `modern`, `legacy` |
| `XUI_VERIFY_TLS` | нет | Проверка TLS панели, по умолчанию `true` |
| `XUI_REQUEST_TIMEOUT` | нет | Таймаут запросов к панели в секундах |
| `CLIENT_TOTAL_GB` | нет | Лимит трафика в GiB; `0` = без лимита |
| `CLIENT_EXPIRY_DAYS` | нет | Срок действия в днях; `0` = бессрочно |
| `CLIENT_LIMIT_IP` | нет | Одновременные IP; `0` = без лимита |
| `CLIENT_FLOW` | нет | Например `xtls-rprx-vision`, если inbound это поддерживает |
| `RATE_LIMIT_PER_HOUR` | нет | Число заявок с одного IP в час |
| `SITE_TITLE` | нет | Заголовок сайта |

**Не меняйте `APP_SECRET` после ввода в эксплуатацию.** Из имени и этого секрета детерминированно формируются email-идентификатор, UUID, пароль и `subId`. Изменение секрета создаст для прежних имён новые записи.

## Развёртывание для другой панели

Код и образ менять не требуется. Создайте отдельную копию `.env` с URL, токеном, inbound ID и URL подписки другой панели. Для полной изоляции используйте отдельный `APP_SECRET`.

```bash
ENV_FILE=.env.second docker compose --project-name portal-second up -d --build
```

Если оба экземпляра находятся на одном сервере, измените host-порт в отдельном compose-файле, например на `8081:8080`.

## Reverse proxy и HTTPS

Для публичного сайта поставьте перед портом 8080 Caddy, Nginx или Traefik и включите HTTPS. Не публикуйте саму админ-панель 3x-ui через этот контейнер. Контейнеру нужен только исходящий доступ к `XUI_PANEL_URL`.

Rate limit хранится в памяти процесса. Для большого публичного сервиса добавьте внешний rate limit на reverse proxy. По умолчанию приложение не доверяет произвольному `X-Forwarded-For`; настройте trusted proxy на уровне Uvicorn/инфраструктуры, если нужен реальный IP за прокси.

## Локальная разработка и тесты

```bash
uv sync --extra test
uv run --extra test pytest -q
```

Запуск без Docker:

```bash
cp .env.example .env
# экспортируйте переменные из .env удобным для вашей оболочки способом
uv run uvicorn app.asgi:app --host 127.0.0.1 --port 8080
```

## Диагностика

```bash
docker compose logs -f portal
```

- `Панель 3x-ui отклонила авторизацию` — проверьте API Token или логин/пароль.
- `Inbound N не найден` — проверьте `XUI_INBOUND_IDS` и права токена.
- Клиент создаётся, но подписка не открывается — проверьте именно публичный `XUI_SUBSCRIPTION_BASE_URL` и настройки subscription server в 3x-ui.
- Для self-signed TLS лучше установить доверенный сертификат. `XUI_VERIFY_TLS=false` используйте только во внутренней защищённой сети.
