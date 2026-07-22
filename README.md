# vibe-order-infra

Учебный full-stack проект приема и обработки клиентских заявок для премиального
автомобильного детейлинга (AUREL Detailing). Включает клиентский frontend,
backend на FastAPI, PostgreSQL, сбор агрегированных behavior metrics, Nginx
как reverse proxy и static server, HTTPS, Docker Compose, приватный Docker
Registry, Watchtower и pgAdmin.

Проект учебный, но архитектура и security-подход сделаны в production-like
стиле: минимальный набор публичных портов, allowlist на уровне Nginx,
разделение Docker-сетей, лимиты ресурсов, версии образов зафиксированы,
секреты не хранятся в git. Локальный репозиторий остается источником истины
для конфигурации: секреты (`.env`, `registry/auth/htpasswd`, TLS-ключи) на
VPS создаются/монтируются отдельно и в git не попадают.

Публичный сайт: **https://vibe.elivcloud.org**

## Целевой сервер

- Ubuntu 24.04, Docker Engine 29.6.2, Docker Compose plugin v5.3.1
- Публичный домен: `vibe.elivcloud.org`
- Registry: `registry-vibe.elivcloud.org`
- UFW пропускает снаружи только 22, 80, 443

**UFW — не единственный механизм защиты.** Опубликованные Docker-порты
(`ports:` в compose) обходят UFW напрямую: Docker сам прописывает правила в
iptables (цепочка `DOCKER`), которые в стандартной конфигурации применяются
РАНЬШЕ пользовательских правил UFW. Поэтому "UFW разрешает только 22/80/443"
само по себе не защищает от сервиса, случайно опубликованного на
`0.0.0.0:5432` — такой порт был бы доступен снаружи, несмотря на UFW.
Единственный надежный способ — вообще не публиковать чувствительные порты на
host (backend, PostgreSQL, Registry сегодня так и сделаны, см. ниже), а не
полагаться на файрвол как на единственный рубеж. SSH (22) остается
единственным административным входом на VPS помимо HTTPS; доступ к
внутренним сервисам (pgAdmin) устроен через SSH-туннель, а не через
дополнительный публичный порт (см. раздел "pgAdmin" ниже).

## Статус проекта

- **Инфраструктура**: `nginx:1.30.4-alpine`, `postgres:16.14-alpine`,
  `nickfedor/watchtower:1.19.0` и собственный образ `backend` — запущены и в
  статусе `healthy`/`running`. `registry:3.1.1` запущен и работает (у
  Registry по дизайну нет Docker-healthcheck, см. "Почему так" ниже).
  `pgAdmin` запускается только через профиль `admin`, по требованию.
- **HTTPS**: Let's Encrypt сертификат на один SAN на оба домена
  (`vibe.elivcloud.org`, `registry-vibe.elivcloud.org`); `https://vibe.elivcloud.org`
  отвечает `200`; HTTP редиректит на HTTPS, кроме ACME challenge и `/healthz`.
- **Registry**: пользователь создан через `registry/create-user.sh`, полный
  push/pull smoke-test пройден (подробности — в разделе "Почему так").
- **PostgreSQL / pgAdmin**: 5432 наружу не публикуется; pgAdmin проверен
  через SSH-туннель и остановлен после проверки.
- **Backend + frontend**: реализованы, задеплоены, ручная end-to-end
  приемка пройдена (см. раздел "Ручная end-to-end приемка" ниже).

Не сделано осознанно (см. "Security notes / ограничения" ниже): HSTS,
автоматизация продления сертификата, Alembic-миграции, серверная auth для
административного интерфейса, Watchtower opt-in для backend.

## Архитектура

```
                              Интернет
                                 │  80/443 (публичен только Nginx)
                                 ▼
                          ┌─────────────┐
                          │    Nginx    │
                          └──────┬──────┘
              ┌───────────────────┼────────────────────┐
              │                   │                    │
     статика (frontend/dist)      │ allowlist /api/*    │ proxy-net
              │                   ▼                    ▼
              │            ┌─────────────┐      ┌──────────┐
              │            │   backend   │      │ Registry │
              │            │   :8000     │      └──────────┘
              │            └──────┬──────┘   (порт 5000 не публикуется)
              │                   │ app-net
              │                   ▼
              │            ┌─────────────┐        ┌─────────┐
              │            │ PostgreSQL  │◄──────►│ pgAdmin │
              │            │   :5432     │        └────┬────┘
              │            └─────────────┘             │
              │        (порт не публикуется)   127.0.0.1:5050
              │                                (профиль admin)
              └────────────────────────────────────────┼───────
                                                         ▼
                                            SSH-туннель с локальной машины

Watchtower: следит за образами всех сервисов (доступ к docker.sock), но
label com.centurylinklabs.watchtower.enable=false у ВСЕХ сервисов, включая
backend — автообновление сейчас никого не затрагивает.
```

Ключевые инварианты:

- **backend:8000** не публикуется на host — доступен только другим
  контейнерам по внутреннему DNS-имени `backend` через `app-net` и
  `proxy-net`.
- **PostgreSQL:5432** не публикуется на host — доступен только внутри
  `app-net`.
- **Registry:5000** не публикуется на host — доступен Nginx внутри
  `proxy-net` по DNS-имени `registry`.
- Единственный сервис, публикующий порты на все интерфейсы (`0.0.0.0`), —
  **Nginx** (80/443).
- **pgAdmin** доступен только через `127.0.0.1:5050` — то есть недоступен
  снаружи VPS в принципе (не вопрос файрвола — Docker физически не слушает
  внешний интерфейс); с локальной машины — только через SSH-туннель.

Две Docker-сети разделяют внешний и внутренний контуры:

- **proxy-net** — Nginx + Registry + backend. Backend подключен сюда
  только для того, чтобы Nginx мог проксировать к нему запросы; сам он
  порт наружу не публикует.
- **app-net** — PostgreSQL + backend + pgAdmin. Не имеет точки входа
  снаружи, кроме опционального loopback-порта pgAdmin.

## Структура проекта

```
vibe-order-infra/
├── docker-compose.yml
├── .env.example
├── .gitignore
├── README.md
├── backend/
│   ├── app/
│   │   ├── core/            # config (pydantic-settings), database (engine/session/Base), exceptions
│   │   ├── models/          # SQLAlchemy ORM: Application, BehaviorMetric, AdminSetting
│   │   ├── schemas/         # Pydantic Create/Update/Read + бизнес-валидация
│   │   ├── crud/            # доступ к БД, без HTTP-специфики
│   │   ├── routes/          # HTTP-обработчики (/api/applications, /api/behavior-metrics, /api/admin-settings)
│   │   └── main.py          # FastAPI app, lifespan (create_all), сборка /api-роутера
│   ├── tests/                # pytest: unit (schemas, db safety guard) + integration (реальный PostgreSQL)
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── api/              # fetch-клиент к /api, типы запросов/ответов
│   │   ├── metrics/          # агрегированные behavior metrics (без сторонних сервисов)
│   │   ├── pages/            # home (клиентская страница), admin (код есть, но не публикуется), notFound
│   │   ├── utils/             # форматирование бюджета, экранирование HTML
│   │   ├── router.ts          # минимальный pathname-роутер без внешней библиотеки
│   │   ├── main.ts
│   │   └── style.css
│   ├── package.json
│   └── vite.config.ts
│   # frontend/dist собирается локально командой `npm run build`;
│   # в git не хранится (см. frontend/.gitignore) — см. "Production build frontend" ниже.
├── nginx/
│   ├── nginx.conf
│   ├── conf.d/
│   │   ├── vibe.elivcloud.org.conf
│   │   └── registry-vibe.elivcloud.org.conf
│   ├── acme-challenge/        # ACME HTTP-01 webroot (в репозитории пусто)
│   └── certs/                 # неиспользуемый плейсхолдер — реальные сертификаты монтируются из /etc/letsencrypt на хосте
└── registry/
    ├── create-user.sh         # создание/обновление пользователя Registry
    └── auth/                  # htpasswd (создается скриптом на VPS, не в git)
```

## Backend

Стек: **FastAPI**, **SQLAlchemy 2** (стиль `Mapped`/`mapped_column`),
**Pydantic** v2 (`pydantic-settings` для конфигурации), **PostgreSQL**,
драйвер **psycopg** (`postgresql+psycopg`).

Три сущности:

- **Application** — клиентская заявка: контактные данные, сведения о
  бизнесе, детали запроса (выбранная услуга, бюджет, срок), предпочитаемый
  способ и время связи.
- **BehaviorMetric** — one-to-one с Application
  (`application_id UNIQUE REFERENCES applications(id) ON DELETE CASCADE`).
  Хранит агрегированные метрики поведения на странице: `time_on_page`,
  `clicked_buttons`, `cursor_hover_data`, `return_count`. Никаких сторонних
  analytics-сервисов не используется; содержимое полей формы и точные
  координаты курсора мыши не собираются — только агрегаты (счетчики кликов
  по именованным кнопкам, суммарное время/количество наведений по
  именованным секциям, счетчик повторных визитов).
- **AdminSetting** — услуги, которые можно заказать: название, диапазон
  бюджета (`budget_min`/`budget_max`), описание, флаг `is_active`.
  Используется frontend (`GET /api/admin-settings/active`) для динамического
  построения списка услуг на клиентской странице.

Слои: `models/` (SQLAlchemy ORM) → `schemas/` (Pydantic Create/Update/Read,
включая валидацию диапазона бюджета и запрет явного `null` для required-полей
при PATCH) → `crud/` (доступ к БД, без HTTP-специфики, доменные исключения
вместо `HTTPException`) → `routes/` (HTTP-обработчики, переводят
`ConflictError`/`DomainValidationError` в коды 409/422) → `core/`
(конфигурация, engine/session, доменные исключения).

Таблицы на текущем учебном этапе создаются напрямую через
`Base.metadata.create_all()` при старте приложения (`lifespan` в
`app/main.py`). Alembic-миграции пока не используются.

## Frontend

Стек: **Vite**, **TypeScript**, vanilla DOM (без тяжелого UI-framework),
шрифт Golos Text через `@fontsource/golos-text`.

Клиентский flow:

1. `GET /api/admin-settings/active` — список активных услуг.
2. Пользователь выбирает услугу.
3. Выбирает бюджет ползунком внутри диапазона услуги (`budget_min`..`budget_max`).
4. Видит summary "Ваша заявка" с выбранной услугой и бюджетом.
5. Заполняет форму (контактные данные, о бизнесе, детали запроса, способ связи).
6. `POST /api/applications`.
7. После успешного создания Application — `POST /api/behavior-metrics` с
   агрегированными метриками сессии.
8. Ошибка отправки behavior metrics не отменяет уже успешно созданную
   Application (best-effort, только `console.warn`).

Особенности:

- Все запросы — same-origin, относительный путь `/api/...`; хардкода
  IP/домена в коде нет, поэтому frontend работает под любым доменом без
  правок кода.
- CORS для этого flow не требуется — frontend и backend отдаются с одного
  origin через Nginx.
- Пути API — без завершающего слэша (`/api/applications`, а не
  `/api/applications/`), иначе FastAPI сделал бы 307-редирект на путь без
  слэша.
- Верстка адаптивная.
- Form-card (форма с полями и кнопкой отправки) скрыта, пока пользователь не
  выбрал услугу — вместо нее показывается summary с приглашением выбрать
  услугу. После выбора форма появляется с мягким fade-in без сложной
  анимации (учитывает `prefers-reduced-motion`). При сбросе выбранной услуги
  (например, ее деактивировали между загрузкой страницы и отправкой) форма
  снова скрывается.

Код учебной admin-страницы (`frontend/src/pages/admin.ts`) присутствует в
frontend-бандле и отвечает клиентскому роуту `/admin`, но на текущем
deployment этот путь заблокирован на уровне Nginx
(`location ^~ /admin { return 404; }`) — до реализации полноценной
серверной аутентификации/авторизации административный интерфейс намеренно
не публикуется.

## API и публичный security allowlist

Снаружи через Nginx (`vibe.elivcloud.org`, порт 443) разрешены строго 4
exact-match пути, каждый — только со своим HTTP-методом (`limit_except`
блокирует остальные методы на этом же пути):

| Метод | Путь |
|---|---|
| GET  | `/api/health` |
| GET  | `/api/admin-settings/active` |
| POST | `/api/applications` |
| POST | `/api/behavior-metrics` |

Все остальное под `/api/` возвращает `404` и не доходит до backend, в том
числе:

- административные CRUD-эндпоинты (`POST`/`PATCH`/`DELETE /api/admin-settings/*`,
  `GET /api/admin-settings` без `/active`);
- чтение списка/отдельных Applications (`GET /api/applications`,
  `GET /api/applications/{id}`);
- чтение BehaviorMetrics (`GET /api/behavior-metrics*`);
- любые `PATCH`/`DELETE` над клиентскими данными.

Также заблокированы Nginx-ом целиком, отдельно от SPA fallback: `/admin`,
`/docs`, `/openapi.json`, `/redoc`.

Сам backend (`routes/`) реализует полный CRUD для всех трех сущностей без
собственной auth — это открытый учебный API, публичная безопасность
обеспечивается исключительно allowlist-ом на уровне Nginx, а не самим
backend.

Swagger/OpenAPI (`/docs`, `/openapi.json`, `/redoc`) был временно доступен
на этапе ручной приемки (см. раздел "Ручная end-to-end приемка"), чтобы
вручную создать тестовые услуги и проверить активный список. После приемки
доступ к Swagger закрыт правилами Nginx.

Административный интерфейс (frontend `/admin` и backend admin-settings CRUD)
намеренно не защищен полноценной authentication/authorization — до
реализации этого механизма он не публикуется наружу ни в каком виде.

## Nginx

- **HTTP (80)**: отдает ACME challenge (`/.well-known/acme-challenge/`) и
  `/healthz` для внутреннего Docker healthcheck; весь остальной трафик
  редиректит на HTTPS (`301`).
- **HTTPS (443)**: сертификат Let's Encrypt (один SAN на
  `vibe.elivcloud.org` и `registry-vibe.elivcloud.org`), `ssl_protocols
  TLSv1.2 TLSv1.3` заданы один раз глобально в `nginx.conf`.
- **Статика**: собранный frontend (`frontend/dist`) раздается напрямую как
  webroot (`root /usr/share/nginx/html`).
- **SPA fallback** (`try_files $uri $uri/ /index.html`) применяется только к
  клиентским frontend-маршрутам — технические пути `/api/*`, `/admin`,
  `/docs`, `/openapi.json`, `/redoc` заблокированы отдельными
  `location`-блоками с более высоким приоритетом (exact-match и `^~`
  префиксы всегда обгоняют `location /`), поэтому в SPA fallback они не
  попадают.
- **`/assets/`** (хэшированные Vite JS/CSS/шрифты) — честный `404` при
  отсутствии файла (`try_files $uri =404`), а не подмена на `index.html` с
  200.
- **API allowlist** реализован exact-match location'ами (см. раздел выше) с
  `limit_except` по методу.
- Backend проксируется по внутреннему Docker DNS-имени сервиса —
  `proxy_pass http://backend:8000;` (без переменной/резолвера, имя
  разрешается при старте/reload Nginx внутри `proxy-net`; глобального
  `resolver` в `nginx.conf` нет, поэтому Nginx требует, чтобы `backend` уже
  существовал в момент старта — это выражено через `depends_on: - backend`
  в `docker-compose.yml`).
- `server_tokens off;`, `X-Content-Type-Options: nosniff` и
  `Referrer-Policy` — во всех активных server-блоках (подробнее — в разделе
  "Почему так").

## Docker Compose

Сервисы: `postgres`, `backend`, `nginx`, `registry`, `watchtower`, `pgadmin`
(профиль `admin`).

**backend**: `build: ./backend`; сети — `app-net` (доступ к `postgres`) и
`proxy-net` (доступность для Nginx); `depends_on: postgres: condition:
service_healthy`; порт 8000 НЕ публикуется на host (нет `ports:`); лимит
памяти 192M (reservation 128M, 0.50 cpu); `labels:
com.centurylinklabs.watchtower.enable: "false"` — как и у остальных
сервисов на данном этапе (backend только что реализован и еще не прошел
достаточный период стабильной работы, чтобы доверить его пересоздание
автообновлению).

**nginx**: read-only bind mount `./frontend/dist:/usr/share/nginx/html:ro`
(собирается вне контейнера, см. "Production build frontend" ниже), плюс
`nginx.conf`/`conf.d`/`acme-challenge`/сертификаты Let's Encrypt — тоже
read-only; `depends_on: - registry, - backend` (простая форма без
`condition`, так как ни у registry, ни у backend нет healthcheck).

**postgres**: без публикации порта на host; healthcheck через `pg_isready`;
наибольшая доля лимита памяти среди всех сервисов (см. "Resource
protection").

**registry**: без публикации порта на host; Basic Auth через
`REGISTRY_AUTH=htpasswd` и файл `registry/auth/htpasswd` (см. "Почему так").

**watchtower**: label-based opt-in (`WATCHTOWER_LABEL_ENABLE=true`), но
label выставлен в `"false"` у всех сервисов — автообновление сейчас никого
не затрагивает (см. "Почему так").

**pgadmin**: профиль `admin`, порт только `127.0.0.1:5050` (см. раздел
"Почему так").

Все шесть сервисов используют `logging: driver: json-file` с `max-size:
"10m"`, `max-file: "3"` — подробнее в разделе "Resource protection".

## Обязательные переменные окружения (.env)

`.env` **обязателен** для запуска — без него `docker compose up`/`config`
откажется стартовать. Критичные переменные (учетные данные PostgreSQL,
учетные данные pgAdmin) объявлены в `docker-compose.yml` в форме
`${VAR:?VAR must be set}`: если переменная не задана, Compose падает с
понятной ошибкой вместо того, чтобы тихо подставить пустую строку.

Обязательны:

- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` — используются
  контейнером `postgres` и, с теми же значениями, контейнером `backend`
  (подключается к `postgres:5432` по этим же учетным данным; отдельных
  backend-специфичных переменных не требуется).
- `PGADMIN_DEFAULT_EMAIL`, `PGADMIN_DEFAULT_PASSWORD` (нужны, только если
  запускается профиль `admin`, но объявлены обязательными и там).

Необязательна (есть безопасное значение по умолчанию):

- `WATCHTOWER_POLL_INTERVAL` (по умолчанию 86400с = раз в сутки).

Registry свои учетные данные из `.env` не берет — см. "Почему так".

Проверить конфигурацию без запуска контейнеров:

```bash
docker compose --env-file .env.example config
docker compose --env-file .env.example --profile admin config
```

## Локальная разработка и тесты

### Backend

```bash
cd backend
pip install -e ".[test]"
```

Integration-тесты (`tests/test_api.py`) требуют отдельную реальную
PostgreSQL test-базу — модели используют JSONB, который SQLite не
эмулирует достоверно. Без `TEST_DATABASE_URL` эти тесты пропускаются с
явной причиной (`pytest.mark.skipif`), а не падают и не используют
production `DATABASE_URL`/`POSTGRES_*` как fallback — такого fallback в
коде нет вообще (см. `tests/db_safety_guard.py`).

Перед любым DDL (`create_all`/`drop_all`) имя базы данных из
`TEST_DATABASE_URL` проверяется отдельной guard-функцией: оно обязано
содержать маркер `test`/`testing` (например, `vibe_orders_test`) и не
должно совпадать с настоящим `POSTGRES_DB` — иначе тесты падают с
`UnsafeTestDatabaseError`, не выполнив ни одной DDL-операции.
Production-like база данных ни при каких условиях не должна использоваться
для integration-тестов.

```bash
export TEST_DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:5432/vibe_orders_test
pytest
```

(Реальные учетные данные здесь не публикуются — подставляются локально.)

Итог прогона против отдельной реальной PostgreSQL test-базы: **44 passed**.

### Frontend

```bash
cd frontend
npm ci
npm test
npm run build
npm audit
```

Итог: **62 tests passed**, `npm run build` — успешно, `npm audit` — **0
vulnerabilities**. Покрытие тестами (coverage) не измерялось — количество
тестов не эквивалентно проценту покрытия кода.

## Production build frontend

`frontend/dist` собирается командой:

```bash
npm run build
```

На VPS Node.js/npm глобально не устанавливаются — сборка выполняется через
временный официальный Node Docker-контейнер, например:

```bash
docker run --rm -v "$PWD/frontend:/app" -w /app node:22-slim sh -c "npm ci && npm run build"
```

(Точная команда может отличаться деталями — важен принцип: сборка Node-
инструментами происходит вне постоянно работающих контейнеров, сам образ
`nginx:alpine` Node/npm не содержит.) Nginx раздает уже собранный
`frontend/dist` через read-only bind mount
(`./frontend/dist:/usr/share/nginx/html:ro`, см. "Docker Compose" выше).

## Порядок деплоя

### Первый деплой (bootstrap)

Этот порядок уже пройден на текущем VPS и остается здесь как процедура для
повторного/дополнительного bootstrap. Registry **не считается готовым к
запуску** без файла `registry/auth/htpasswd` — до его появления контейнер
стартует, но любой запрос к Registry будет отклонен на этапе аутентификации
(это ожидаемо и правильно, а не баг):

1. **Создать `.env`** на VPS (`cp .env.example .env`, заполнить реальными
   значениями — НЕ теми, что в примере).
2. **Установить `apache2-utils`**, если `htpasswd` еще не установлен:
   ```bash
   command -v htpasswd || sudo apt-get update && sudo apt-get install -y apache2-utils
   ```
3. **Запустить `registry/create-user.sh <username>`** (потребует `chmod +x`
   при первом запуске) — пароль вводится интерактивно и скрыто, скрипт его
   нигде не сохраняет.
4. **Убедиться, что файл создан**: `ls -l registry/auth/htpasswd` (права
   должны быть `600`).
5. **Только теперь** `docker compose up -d` (без профиля `admin`).

Выпуск TLS-сертификатов и включение HTTPS — отдельный шаг после этого
порядка (certbot на VPS, затем перезапуск Nginx); на текущем VPS уже
выполнен.

### Обновление / повторный деплой (текущий flow, с backend + frontend)

1. `git pull`/`git fetch` — обновить репозиторий на VPS.
2. `docker compose config --quiet` — убедиться, что `.env` полон и
   конфигурация валидна, до запуска чего-либо.
3. Собрать backend-образ: `docker compose build backend`.
4. Поднять/пересоздать backend: `docker compose up -d backend` (Compose
   дождется `postgres` healthy благодаря `depends_on`).
5. Собрать frontend вне хоста — временным официальным Node-контейнером
   (см. "Production build frontend" выше) — `frontend/dist` обновляется на
   хосте.
6. Пересоздать/перезапустить `nginx` только если менялся сам bind mount
   (новый `frontend/dist`) или конфигурация (`nginx.conf`/`conf.d/*`) —
   иначе можно оставить как есть, так как `frontend/dist` монтируется как
   bind mount, а не встраивается в образ.
7. `nginx -t` — проверить синтаксис конфигурации перед reload/restart, если
   она менялась.
8. Smoke tests: убедиться, что allowlisted `/api/*` пути отвечают, а
   заблокированные пути (`/admin`, `/docs`, `GET /api/applications` и т.п.)
   получают 403/404.
9. `docker compose ps` — все сервисы в статусе `running`/`healthy`.
10. `docker stats --no-stream` — сверить фактическое потребление
    памяти/CPU с лимитами (особенно важно теперь, когда backend добавлен в
    набор постоянно работающих сервисов — см. "Resource protection").

`docker compose down` не используется как часть стандартного flow
обновления — он остановил бы все сервисы разом вместо контролируемого
поочередного обновления.

## Ручная end-to-end приемка

Функциональный сценарий, пройденный вручную end-to-end на VPS:

- Через Swagger (на момент тестового этапа, до закрытия `/docs`) создано 3
  услуги в `admin_settings`.
- `GET /api/admin-settings/active` вернул все 3 созданные услуги.
- Записи подтверждены напрямую в PostgreSQL.
- Frontend получил список услуг динамически (без хардкода) и отобразил их в
  разделе "Услуги".
- На клиентской странице выбрана услуга "Керамическое покрытие
  премиум-класса".
- Выбран бюджет 250 000 ₽ (ползунок внутри диапазона услуги).
- Форма заполнена и успешно отправлена (`POST /api/applications`).
- Frontend показал сообщение: "Заявка отправлена! Мы свяжемся с вами в
  ближайшее время."
- Application появилась в PostgreSQL (`applications`).
- Связанная BehaviorMetric появилась в `behavior_metrics` с тем же
  `application_id`.
- Behavior metrics содержат агрегированные `time_on_page`,
  `clicked_buttons`, `cursor_hover_data`, `return_count` — без содержимого
  полей формы и без точных координат курсора.

Security smoke-tests (выполнены снаружи, через публичный домен):

- `GET /api/applications` (список заявок) — заблокирован.
- `GET /api/applications/{id}` (отдельная заявка) — заблокирован.
- `GET /api/behavior-metrics*` — заблокирован.
- Административный API (`POST`/`PATCH`/`DELETE /api/admin-settings/*`) —
  заблокирован.
- `/admin` — заблокирован (404 от Nginx).
- `/docs` — заблокирован (404 от Nginx).
- `/openapi.json` — заблокирован (404 от Nginx).
- Backend (`:8000`) и PostgreSQL (`:5432`) не публикуют портов на host —
  недоступны снаружи Docker-сети в принципе, а не только по правилам
  Nginx.

## Скриншоты / результаты

Файлы скриншотов в репозитории отсутствуют — здесь только перечислены
сценарии, подтвержденные скриншотами при сдаче:

- Swagger: `POST /api/admin-settings` (создание услуги).
- Swagger: `GET /api/admin-settings/active` (список активных услуг).
- PostgreSQL: содержимое таблицы `admin_settings`.
- Frontend: главная страница (home).
- Frontend: выбор услуги и бюджета.
- Frontend: успешная отправка заявки.
- PostgreSQL: содержимое таблицы `applications`.
- PostgreSQL: содержимое таблицы `behavior_metrics`.

## Почему так

**PostgreSQL без внешнего порта.** 5432 не публикуется ни на `0.0.0.0`, ни
на `127.0.0.1`: субд не нужна нигде, кроме `app-net` (backend и pgAdmin
обращаются к ней по DNS-имени `postgres`).

**Backend без внешнего порта.** По тем же причинам порт 8000 не
публикуется. Единственный путь снаружи — через Nginx по узкому allowlist
(см. "API и публичный security allowlist" выше); напрямую к backend
обратиться нельзя даже в обход allowlist, потому что порт физически не
слушает внешний интерфейс.

**Registry без прямого доступа.** Порт 5000 не публикуется. Доступ —
только через Nginx по HTTPS (`registry-vibe.elivcloud.org`), с Basic Auth
на стороне самого Registry (`REGISTRY_AUTH=htpasswd`, файл
`registry/auth/htpasswd`, создается через `registry/create-user.sh` — см.
"Порядок деплоя" выше). Nginx намеренно НЕ проксирует запросы к Registry по
обычному HTTP (порт 80 только редиректит на HTTPS) — иначе логин/пароль
передавались бы в открытом виде.

**Registry — подтверждено полным push/pull smoke-test'ом.** После выпуска
сертификата и создания пользователя через `create-user.sh` проверен весь
путь целиком: `docker login registry-vibe.elivcloud.org` успешен; запрос
`/v2/` без креденшлов возвращает `401` с Basic auth challenge (ожидаемо —
значит и TLS, и auth реально в деле, а не просто запущены); тестовый образ
`hello-world` запушен в
`registry-vibe.elivcloud.org/test/hello-world:latest`, локальный тег
удален, образ успешно вытянут обратно — то есть цикл push → pull через
приватный Registry за HTTPS с auth работает end-to-end.

**Registry v3.1.1 — совместимость с текущей конфигурацией проверена** перед
апгрейдом с `registry:2.x` по официальной документации CNCF Distribution:
конвенция переменных `REGISTRY_<SECTION>_<KEY>` (`REGISTRY_AUTH`,
`REGISTRY_AUTH_HTPASSWD_REALM`, `REGISTRY_AUTH_HTPASSWD_PATH`) и требование
bcrypt-хэшей для htpasswd не изменились; путь тома данных по умолчанию
(`/var/lib/registry`) не изменился. Путь конфига по умолчанию сменился на
`/etc/distribution/config.yml` (был `/etc/docker/registry/config.yml` в
v2), но на нас это не влияет — кастомный `config.yml` не монтируется,
используются только env var overrides. Отдельно: CLI-утилита `htpasswd`
убрана из самого образа `registry` — не проблема, `registry/create-user.sh`
использует `htpasswd` с хоста VPS (`apache2-utils`), а не изнутри
контейнера.

**pgAdmin — только localhost/SSH-tunnel, только по требованию.** Порт
публикуется как `127.0.0.1:5050:80`, то есть недоступен снаружи VPS в
принципе (не вопрос файрвола — Docker физически не слушает внешний
интерфейс). Доступ с локальной машины — через SSH-туннель, например:

```bash
ssh -N -L 15050:127.0.0.1:5050 vibe-vps
# затем открыть http://127.0.0.1:15050 в браузере на локальной машине
```

(Локальный порт туннеля не обязан совпадать с портом на VPS; `vibe-vps` —
алиас хоста из `~/.ssh/config`.)

Проверено на реальном деплое: через такой туннель в pgAdmin успешно
настроено подключение к PostgreSQL (`host: postgres`, `port: 5432`,
`database: vibe_orders` — `postgres` резолвится по имени сервиса Docker,
только изнутри `app-net`), база `vibe_orders` видна в интерфейсе. После
проверки pgAdmin остановлен, чтобы не расходовать RAM впустую.

pgAdmin также вынесен в Compose-профиль `admin` и НЕ поднимается командой
`docker compose up` без указания профиля — чтобы не потреблять RAM
постоянно на VPS с ограничением ~1GB:

```bash
docker compose --profile admin up -d pgadmin
docker compose --profile admin stop pgadmin   # когда не нужен
```

**Watchtower присутствует, настроен, но пока никого не обновляет.**
`WATCHTOWER_LABEL_ENABLE=true` переключает Watchtower в режим "обновляю
только контейнеры с явным label
`com.centurylinklabs.watchtower.enable=true`". На данном этапе этот label
выставлен в `"false"` у **всех** сервисов, включая появившийся backend. Это
осознанное production-like решение, а не недосмотр:

- PostgreSQL — обновление СУБД в фоне может уронить данные заявок или тихо
  сломать совместимость данных с новой мажорной версией.
- Registry и Nginx — единственные, кто напрямую держит порты наружу; их
  обновление предпочтительно делать контролируемо, синхронно с проверкой,
  что сервис поднялся корректно.
- pgAdmin — админ-панель с доступом к БД; обновлять без присмотра не нужно.
- backend — реализован недавно, еще не прошел достаточный период
  стабильной работы на VPS. Как stateless-сервис он — разумный кандидат на
  точечный opt-in в будущем (проще безопасно пересоздавать автоматически,
  чем сервисы с состоянием или привилегированным сетевым положением), но
  это отдельный шаг после стабилизации, не сделанный на данном этапе.

**Watchtower-образ: `nickfedor/watchtower`, а не `containrrr/watchtower`.**
Оригинальный `containrrr/watchtower` — заархивирован (read-only с 17
декабря 2025), релизы, багфиксы и security-патчи прекращены. Дополнительно
он несовместим с современным Docker Engine 29: падает на старте с ошибкой
`client version 1.25 is too old. Minimum supported API version is 1.44`.
`nickfedor/watchtower` — активно поддерживаемый форк того же проекта,
обновляющий внутренние зависимости под текущий Docker API. Перед
переключением образа проверена (по официальной документации форка,
watchtower.nickfedor.com) совместимость всех используемых здесь переменных
окружения — `WATCHTOWER_LABEL_ENABLE`, `WATCHTOWER_CLEANUP`,
`WATCHTOWER_POLL_INTERVAL` — задокументированы в форке с теми же именами,
типами и значениями по умолчанию, что и в оригинале.

**Риск docker.sock.** Watchtower должен уметь пересоздавать контейнеры,
поэтому ему смонтирован `/var/run/docker.sock`. Это дает контейнеру
фактически root-доступ к хосту: через Docker API можно запустить
произвольный привилегированный контейнер с бинд-маунтом `/`. Флаг `:ro` на
монтировании ограничивает только возможность подменить/удалить сам файл
сокета — он **не** ограничивает вызовы Docker API через этот сокет. Это
осознанный компромисс, типичный для Watchtower, и в этом учебном проекте
он сохраняется специально — задание явно требует Watchtower в составе
инфраструктуры. В проде эту роль обычно возлагают на более узкоправный
socket-proxy (например, `tecnativa/docker-socket-proxy`) — здесь такой
прокси сознательно не добавлен, чтобы не усложнять каркас.

**HTTPS.** Настроен и подтвержден на реальном деплое. Let's Encrypt выпустил
один сертификат на оба домена (`vibe.elivcloud.org` +
`registry-vibe.elivcloud.org` как SAN); сертификат физически лежит на VPS
под `/etc/letsencrypt/live/vibe.elivcloud.org/` и монтируется в Nginx
**read-only** напрямую оттуда (`/etc/letsencrypt:/etc/letsencrypt:ro` в
`docker-compose.yml`) — в репозиторий не копируется и не хранится.
`nginx/conf.d/*.conf` для обоих доменов: порт 80 отдает ACME challenge и
`/healthz`, весь остальной HTTP редиректит на HTTPS (`301`); порт 443 —
активен, `ssl_protocols TLSv1.2 TLSv1.3` заданы один раз глобально в
`nginx.conf`. Registry на 443 проксируется с registry-specific настройками
(`proxy_http_version 1.1`, `proxy_request_buffering off`, увеличенные
таймауты, заголовок `Docker-Distribution-Api-Version`, корректные
`X-Forwarded-*`). `nginx/certs/` в репозитории — неиспользуемый пустой
плейсхолдер (реальные сертификаты — из `/etc/letsencrypt` на хосте). HSTS
сознательно **не включен** — его стоит добавлять отдельным шагом после
более длительного периода стабильной работы HTTPS.

**Nginx hardening.** `server_tokens off;` (не светить версию Nginx),
`X-Content-Type-Options: nosniff` и `Referrer-Policy` добавлены во все
активные server-блоки. `add_header` внутри `location {}` сбрасывает
наследование `add_header` из родительского `server {}` (особенность
Nginx) — поэтому в registry-конфиге эти заголовки продублированы внутри
`location /v2/`, а не полагаются на наследование с уровня `server`.

## Resource protection (~1GB RAM VPS)

Лимиты заданы через `deploy.resources.limits` — это часть Compose
Specification и применяется обычным `docker compose up` (swarm не нужен;
проверено рендерингом через `docker compose config`). Заданы для всех
шести сервисов: postgres, backend, nginx, registry, pgadmin, watchtower.

| Сервис     | memory limit | memory reservation | cpus |
|------------|-------------:|--------------------:|-----:|
| postgres   | 384M | 192M | 1.00 |
| pgadmin    | 256M | 128M | 0.50 |
| backend    | 192M | 128M | 0.50 |
| registry   | 192M |  64M | 0.50 |
| nginx      |  96M |  32M | 0.50 |
| watchtower | 128M |  32M | 0.25 |

PostgreSQL намеренно получает наибольшую долю (лимит не занижен
агрессивно) — слишком туго ограниченная СУБД гарантированно упадет по OOM
под нагрузкой, что хуже, чем не ограничивать ее вовсе.

Сумма лимитов сервисов, работающих по умолчанию (без профиля `admin`):
postgres + backend + registry + nginx + watchtower =
384+192+192+96+128 = **992M**. Появление backend заметно сузило запас по
сравнению с состоянием до backend (было 800M/~200M запаса на ОС и Docker
daemon) — теперь запас по потолку лимитов минимален. Лимиты — это потолок
(cgroup limit), а не одновременное резервирование: сумма *reservation*
для того же набора сервисов заметно ниже (192+128+64+32+32 = 448M), и
контейнер обычно потребляет меньше своего лимита. Тем не менее, после
любого редеплоя backend стоит явно сверять фактическое потребление через
`docker stats --no-stream` (см. "Порядок деплоя" выше), а включение
профиля `admin` (+256M pgadmin, суммарный потолок — 1248M) на VPS с ~1GB
RAM разумно только на короткое время проверки, не постоянно.

**Log rotation.** Все шесть сервисов используют `logging: driver:
json-file` с `max-size: "10m"`, `max-file: "3"` — до ~30MB логов на
контейнер, дальше старые файлы ротируются. Без этого логи Docker могут
неограниченно расти и забить диск VPS. (Для Nginx это работает благодаря
тому, что образ `nginx:alpine` по умолчанию симлинкует
`/var/log/nginx/access.log`/`error.log` на `/dev/stdout`/`stderr` — наш
`nginx.conf` эти пути не переопределяет.)

## Версии образов

Образы инфраструктуры зафиксированы на конкретных версиях (без `:latest`):

| Сервис | Образ |
|---|---|
| postgres | `postgres:16.14-alpine` |
| pgadmin | `dpage/pgadmin4:9.16` |
| registry | `registry:3.1.1` |
| nginx | `nginx:1.30.4-alpine` |
| watchtower | `nickfedor/watchtower:1.19.0` |
| backend | собственная сборка на базе `python:3.12-slim` (`build: ./backend`, не тянется из реестра) |

Совместимость Watchtower-форка и Registry v3.1.1 с текущей конфигурацией
проверена по официальной документации (см. раздел "Почему так") перед
указанием версий в `docker-compose.yml`.

## Security notes / ограничения

Текущее состояние:

- Admin-роут (frontend `/admin`) и admin CRUD (backend
  `/api/admin-settings/*` кроме `/active`) существуют в коде, но не
  публикуются наружу — заблокированы правилами Nginx.
- Полноценная authentication/authorization для административного
  интерфейса — следующий этап, пока не реализована.
- Swagger/OpenAPI/Redoc закрыты Nginx после ручной приемки; backend сам их
  не защищает.
- Автоматические миграции БД через Alembic пока не внедрены;
  `Base.metadata.create_all()` — осознанное решение для текущего учебного
  этапа, не рассчитанное на эволюцию схемы в проде.
- Behavior-аналитика — первого рода (без сторонних сервисов), локальна и
  агрегирована; точные координаты курсора и содержимое полей формы не
  собираются.
- Секреты (`.env`, `registry/auth/htpasswd`, TLS-ключи) не хранятся в git —
  создаются/монтируются на VPS отдельно.
- HSTS не включен, автопродление сертификата Let's Encrypt не
  автоматизировано, Watchtower не обновляет автоматически ни один сервис
  (opt-in выключен везде, включая backend).

Проект в целом — учебный: инфраструктура и подход к security сделаны
production-like, но проект не претендует на полную production-readiness
(нет auth на admin, нет миграций схемы, нет автообновления сертификата).

## Отличия от буквальной формулировки задания

Учебное задание допускает `pgAdmin` через `127.0.0.1:5050:80` — это и
реализовано буквально. Дополнительно (сверх минимума) сделано:

- pgAdmin вынесен в отдельный Compose-профиль, а не просто "порт на
  localhost", чтобы он не грузил RAM, когда не используется.
- Basic Auth для Registry реализован на стороне самого Registry
  (встроенная поддержка `htpasswd`/bcrypt), а не через `auth_basic` в
  Nginx — меньше движущихся частей, один источник правды для учетных
  данных.
- В `create-user.sh` проверка существующего пользователя — точное
  сравнение поля через `awk`, а не `grep -E` (спецсимволы в имени
  пользователя, например `.`, иначе интерпретировались бы как regex);
  добавлены резервное копирование `htpasswd` перед правкой и явное
  подтверждение при обновлении существующего пользователя.
- Watchtower по умолчанию не обновляет ни один сервис, включая backend
  (все label = `false`) — более консервативно, чем буквально требовал
  первый проход задания.
- Осознанно НЕ включен `internal: true` на Docker-сетях (хотя это
  дополнительно ужесточило бы изоляцию `app-net`/`proxy-net`), так как это
  взаимодействует с публикацией портов не всегда очевидным образом.
  Реальный деплой прошел без этой настройки, поэтому ее влияние на
  публикацию портов (в частности, `127.0.0.1:5050` у pgAdmin) так и не
  проверялось на практике. Возможное улучшение на будущее, требующее
  отдельной проверки перед включением.

## Дальше по плану

Bootstrap, HTTPS, Registry-smoke-test, backend и frontend уже реализованы и
прошли приемку (см. "Ручная end-to-end приемка"). Из содержательного
остается:

1. Реализовать серверную authentication/authorization для административного
   интерфейса и только после этого открыть `/admin` и admin-CRUD наружу.
2. Внедрить Alembic-миграции вместо `Base.metadata.create_all()` — нужно
   для безопасной эволюции схемы БД в будущем.
3. Точечный opt-in Watchtower-label для backend (stateless, безопаснее
   автообновлять), после периода стабильной работы на VPS — не трогая
   остальные сервисы.
4. Автоматизировать продление сертификата Let's Encrypt (cron/systemd timer
   с `certbot renew`) — в рамках текущего деплоя настраивался только
   первичный выпуск.
5. HSTS — включить отдельным шагом после более длительного периода
   стабильной работы HTTPS.
