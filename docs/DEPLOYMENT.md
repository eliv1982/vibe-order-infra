# Деплой vibe-order-infra

Этот документ — источник истины для процедур деплоя (локальная разработка,
CI, production VPS) и для чеклиста приёмки после деплоя (см. "Текущий
чеклист приёмки после деплоя" ниже — актуален для сегодняшнего
репозитория). Подробные пошаговые команды релиза (сборка/доставка образа
backend, полный порядок первого деплоя) пока остаются в README, "Порядок
деплоя" — этот файл на них ссылается, а не дублирует их. Архитектура,
границы доверия и ролевая модель БД — в [ARCHITECTURE.md](ARCHITECTURE.md);
security-инварианты, полная API allowlist-матрица и историческая (commit
`7761901`) production-приемка — в [README.md](../README.md). Операционные
события после деплоя (сбой старта, восстановление из backup, ротация
секретов и т.п.) — в [RUNBOOK.md](RUNBOOK.md).

## Три окружения

| | Где | БД | Секреты | Как считается "готово" |
|---|---|---|---|---|
| **Локальная разработка** | ноутбук разработчика | `postgres` контейнер, поднятый через `docker compose -f docker-compose.yml -f docker-compose.local.yml up -d` (см. README, "Полный локальный стек") | `.env`, сгенерированный локально, не коммитится | `pytest`/`npm test` проходят; `docker compose -f docker-compose.yml -f docker-compose.local.yml up -d` поднимает postgres+backend, `npm run dev` — frontend |
| **CI** | GitHub Actions (`.github/workflows/ci.yml`) | одноразовый `postgres:16.15-alpine` service-контейнер, уничтожается после job'а | фиктивные, захардкоженные в workflow-файле (`postgres`/`postgres`), не секреты в смысле GitHub Secrets — база одноразовая и никогда не публикуется | все три job'а (`backend-tests`, `frontend`, `containers`) зелёные |
| **Production** | Ubuntu 24.04 VPS (см. README, "Целевой сервер") | `postgres` контейнер с постоянным volume, порт наружу не публикуется | `.env` создаётся вручную на VPS, права `600`, никогда не в git | `docker compose ps` — все сервисы `healthy`/`running`, `GET /api/health` через HTTPS отвечает `200` |

Repo не предоставляет Terraform/Ansible/Kubernetes-манифестов — production
provisioning самого VPS (ОС, Docker Engine, UFW, DNS) выполняется вручную
по шагам ниже и не автоматизирован в этом репозитории.

## Предварительные требования

**Локально / CI:**

- Python 3.12 (см. `backend/pyproject.toml`, `requires-python = ">=3.12"`)
- Node.js 22 (см. `docker run ... node:22-slim` в README, "Production build
  frontend" — это же значение используется в CI)
- Docker Engine + Compose plugin (для `docker compose config`/сборки
  образов; не обязателен для одного только `pytest`/`npm test`)

**Production VPS:**

- Ubuntu 24.04, Docker Engine, Docker Compose plugin (версии — см. README,
  "Целевой сервер")
- UFW, пропускающий снаружи только 22/80/443 — Docker публикует порты в
  обход UFW (см. README, "UFW — не единственный механизм защиты"), поэтому
  реальная защита — не публиковать чувствительные порты на host вообще
  (уже так: `postgres`/`backend`/`registry` без `ports:`)
- `apache2-utils` (для `htpasswd`, см. `registry/create-user.sh`)
- Выпущенный TLS-сертификат Let's Encrypt под `/etc/letsencrypt` (см. раздел
  "TLS / reverse-proxy" ниже)

## Конфигурация и переменные окружения

`docker-compose.yml` требует **все** переменные ниже через `${VAR:?...}` —
без полного `.env` `docker compose config`/`up` откажется стартовать с
явной ошибкой (проверить: `docker compose --env-file .env.example config`).
`.env.example` — единственный источник правды по актуальному набору
переменных; таблица ниже — сводка того, кто именно каждую использует.

| Переменная | Использует | Назначение |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD` | `postgres` (init кластера), `db-roles-bootstrap`/`db-roles-finalize`, `app/db_admin/adopt_legacy.py` | Кластерный superuser-креденшл. **Backend им не пользуется** (см. ниже) — только для одноразового создания ролей/реассайна ownership. |
| `POSTGRES_DB` | `postgres`, все сервисы, читающие БД | Имя базы данных. |
| `MIGRATION_DB_USER`, `MIGRATION_DB_PASSWORD` | `db-migrate` (Alembic), `db-roles-bootstrap`/`finalize` (создают эту роль) | Роль-владелец схемы. Единственная роль, от имени которой когда-либо подключается Alembic. `CREATE`+`USAGE` на `public`, не суперпользователь. |
| `APP_DB_USER`, `APP_DB_PASSWORD` | `backend` (все обычные запросы), `db-roles-bootstrap`/`finalize` (создают эту роль) | Runtime-роль. Только `SELECT/INSERT/UPDATE/DELETE` на таблицы приложения, **без прав DDL**, без доступа к `alembic_version` (см. `app/db_admin/bootstrap_roles.py`). |
| `JWT_SECRET_KEY` | `backend` | Секрет подписи JWT. Минимум 32 символа; известные placeholder-значения (включая значение из `.env.example`) отклоняются валидацией `Settings` при старте (см. `app/core/config.py`). |
| `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES` | `backend` | Не секреты, есть безопасные значения по умолчанию. |
| `PGADMIN_DEFAULT_EMAIL`, `PGADMIN_DEFAULT_PASSWORD` | `pgadmin` (профиль `admin`) | Учётные данные веб-интерфейса pgAdmin. |

Три отдельные роли — не разные вкусы одного и того же креденшла: они
существуют, чтобы ни backend (взломанный через уязвимость в приложении),
ни миграционная роль сами по себе не располагали правами сверх
необходимых им (backend не может выполнить DDL даже теоретически - он
подключается исключительно как `APP_DB_USER`). Важно не путать это с
"`MIGRATION_DB_USER` физически не может читать/писать таблицы приложения
вне `alembic upgrade head`" — это не так: `MIGRATION_DB_USER` становится
владельцем (`OWNER`) каждой таблицы/sequence, которую создаёт миграция
(см. `_set_default_privileges_for_future_objects` в
`bootstrap_roles.py`), и как владелец технически может читать/писать их в
любой момент, а не только во время самой миграции. Гарантия, которую даёт
разделение ролей — не это, а то, что backend в обычной работе никогда не
пользуется миграционным креденшлом (и наоборот, миграционная роль никогда
не участвует в обслуживании HTTP-трафика) — `APP_DB_USER` остаётся
единственной, наименее привилегированной ролью, от имени которой
приложение фактически работает. Полное
обоснование и живой regression-тест privilege-границ — см.
`backend/app/db_admin/bootstrap_roles.py` и
`backend/tests/test_db_role_privileges.py`.

### Секреты

- Ничего из таблицы выше не хранится в git — `.env.example` содержит только
  безопасные плейсхтолдеры, реальный `.env` — в `.gitignore`.
- На production VPS `.env` создаётся вручную (`cp .env.example .env`,
  заполняется реальными значениями) и получает права `600`.
- `JWT_SECRET_KEY`: `openssl rand -hex 32` или
  `python -c "import secrets; print(secrets.token_urlsafe(64))"`.
- Пароли ролей PostgreSQL: любой генератор случайных строк, например
  `openssl rand -base64 32`.
- TLS-приватный ключ — не в этом репозитории вообще: физически на VPS под
  `/etc/letsencrypt`, монтируется в Nginx read-only (см. "TLS /
  reverse-proxy" ниже).
- Registry Basic Auth (`registry/auth/htpasswd`) — создаётся отдельно
  скриптом `registry/create-user.sh`, не через `.env` (см. README, "Docker
  Registry").

## Provisioning базы данных

Схема управляется **исключительно Alembic-миграциями**
(`backend/alembic/versions/`) — `Base.metadata.create_all()` в коде
приложения больше не используется, а старт backend (`app/main.py`'s
`lifespan`) — read-only проверка (`app/core/schema_check.py`), которая
**отказывает в старте**, если схема не соответствует ожидаемой версии, а не
пытается её починить сама. Это осознанное разделение: backend никогда не
делает DDL.

### Свежая установка (fresh install)

Предполагается, что тег `vibe-order-infra-backend:latest` уже
детерминированно присутствует локально на этом хосте: на production VPS
это гарантирует процедура README, "Первый деплой (bootstrap)" (образ
собран вне VPS и доставлен явно — через Registry при обновлении, через
`docker save`/`scp`/`docker load` при самом первом деплое; `build:` в этой
Compose-декларации на VPS не используется никогда, см. README, "Docker
Compose"). Поэтому на VPS команда всегда с `--no-build`:

```bash
docker compose up -d --no-build
```

(Локально для разработки этот флаг не нужен — без предварительно
собранного тега Compose соберет образ сам за счет `build: ./backend`,
именно поэтому этот флаг в описании "Три окружения" выше не указан для
локальной разработки.)

Локально также не поднимаются `nginx`/`registry` — для чистого клона без
production TLS-сертификата/Registry-креденшлов используется отдельный
override [docker-compose.local.yml](../docker-compose.local.yml):
`docker compose -f docker-compose.yml -f docker-compose.local.yml up -d`
(полная процедура, включая frontend, — README, "Полный локальный стек").
Цепочка postgres -> roles -> migrate -> roles -> backend ниже — общая для
production и для этого локального override: он меняет только набор
запускаемых сервисов и публикацию порта backend, не сам lifecycle.

Это поднимает всё автоматически в правильном порядке — зависимости между
сервисами объявлены через `depends_on: condition:
service_completed_successfully` / `service_healthy`, вручную запускать
шаги ниже не нужно:

```
postgres (healthy)
  -> db-roles-bootstrap   (создаёт роли MIGRATION_DB_*/APP_DB_*, ALTER DEFAULT PRIVILEGES)
  -> db-migrate           (alembic upgrade head, от имени MIGRATION_DB_USER)
  -> db-roles-finalize    (тот же bootstrap-скрипт повторно — подхватывает
                            только что созданные Alembic'ом таблицы/sequences,
                            REVOKE на alembic_version для APP_DB_USER)
  -> backend               (healthy, только когда GET /api/ready = 200)
```

Все три one-shot сервиса (`db-roles-bootstrap`/`db-migrate`/
`db-roles-finalize`) явно объявляют тот же `image: vibe-order-infra-backend:latest`,
что и `backend` (только другая `command:`/креденшлы — см.
`docker-compose.yml`); собирает этот образ только сам `backend` (единственный
из четырёх с `build:`) — остальные три лишь ссылаются на готовый тег, не
пересобирают его отдельно. Ни один из трёх не перезапускается (`restart` не
задан) и не публикует портов. `docker compose ps --all` (обычный `ps` без
`--all` по умолчанию скрывает контейнеры в статусе `Exited`, в т.ч. эти
three one-shot — они бы просто не появились в выводе) после первого
`up -d` должен показать их в статусе `Exited (0)` — это ожидаемо и означает
успех, не сбой.

### Существующая (legacy) production-база

Если БД была развёрнута **до** появления Alembic-lifecycle (нет таблицы
`alembic_version`), её нужно один раз "усыновить" в управляемый Alembic'ом
жизненный цикл, прежде чем `db-migrate` сможет применить к ней миграции.
Полная последовательность (каждый шаг обязан завершиться прежде, чем
начинать следующий):

1. подготовка (роли);
2. усыновление (adopt_legacy);
3. миграция (`alembic upgrade head`);
4. финализация ролей/прав;
5. запуск backend.

```bash
# 1. Подготовка: создаёт роли (MIGRATION_DB_USER/APP_DB_USER), если их ещё
#    нет. Безопасно запускать через обычный "run" без --no-deps - у
#    db-roles-bootstrap в docker-compose.yml нет depends_on ни от кого
#    "вниз по цепочке" (только postgres: condition: service_healthy "вверх"),
#    так что это не может случайно поднять db-migrate/db-roles-finalize.
docker compose run --rm db-roles-bootstrap

# 2. Усыновление. adopt_legacy запускается через сервис "backend", но его
#    собственный environment: в docker-compose.yml не содержит
#    POSTGRES_USER/MIGRATION_DB_* (их несёт только
#    db-roles-bootstrap/finalize) - передаём явно из .env.
#
#    --no-deps ОБЯЗАТЕЛЕН: "backend" в docker-compose.yml объявляет
#    depends_on: db-roles-finalize: condition: service_completed_successfully
#    (а тот, в свою очередь, зависит от db-migrate). Без --no-deps
#    "docker compose run backend ..." сначала запускает и ждёт весь этот
#    хвост зависимостей - включая db-migrate ("alembic upgrade head") -
#    ДО того, как adopt_legacy успевает застемпить alembic_version на
#    legacy-базе. `alembic upgrade head` на ещё не усыновленной базе
#    (нет alembic_version) начинает применять миграции с самого начала,
#    включая CREATE TABLE для таблиц, которые в legacy-базе уже есть -
#    падает на конфликте (в лучшем случае) вместо ожидаемого усыновления.
#    Проверено live (docker compose run без --no-deps действительно
#    поднимает db-roles-bootstrap/db-migrate/db-roles-finalize раньше
#    вашей команды; с --no-deps - не поднимает ничего лишнего).
set -a; source .env; set +a
docker compose run --rm --no-deps \
  -e POSTGRES_USER -e POSTGRES_PASSWORD \
  -e MIGRATION_DB_USER -e MIGRATION_DB_PASSWORD \
  backend python -m app.db_admin.adopt_legacy

# 3-5. Миграция -> финализация ролей -> backend. Обычная цепочка (см.
#    "Свежая установка" выше) теперь безопасна: alembic_version уже
#    застемплен на 0001_legacy_baseline шагом 2, поэтому db-migrate
#    применяет только 0002+ поверх него, а не пытается создать существующие
#    legacy-таблицы заново. db-roles-bootstrap из шага 1 идемпотентен -
#    повторный прогон внутри этой цепочки безвреден.
#
#    --no-build: тег vibe-order-infra-backend:latest на этом этапе уже
#    должен существовать локально (доставлен явно - см. README, "Первый
#    деплой (bootstrap)" / "Обновление / повторный деплой"), а не собран
#    здесь голым "up -d" из build: ./backend.
docker compose up -d --no-build
docker compose ps --all   # db-roles-bootstrap/db-migrate/db-roles-finalize — Exited (0)
```

`adopt_legacy` — read-only-верификация точного legacy-фингерпринта
(`admins`/`admin_settings`/`applications`/`behavior_metrics`, ровно эти
четыре таблицы, ровно эти колонки/constraints/sequences), затем
реассайн ownership таблиц на `MIGRATION_DB_USER` и `alembic stamp` на
legacy baseline-ревизию (`0001_legacy_baseline`). Идемпотентна: если
`alembic_version` уже существует, ничего не делает (см. модуль
`app/db_admin/adopt_legacy.py` и `backend/tests/test_migrations_legacy_upgrade.py`).
База, чья форма хоть немного отличается от ожидаемого legacy-фингерпринта,
**отклоняется**, а не "усыновляется по-хорошему" — не гадает.

## Процедура миграций

Новая ревизия схемы добавляется как обычный Alembic-файл в
`backend/alembic/versions/` (генерация — `alembic revision --autogenerate
-m "..."`, локально, против отдельной dev-базы; см. `backend/alembic.ini`
и `backend/alembic/env.py` про то, что миграции никогда не подключаются
runtime-креденшлом). Применение — только через `db-migrate` (`alembic
upgrade head`, роль `MIGRATION_DB_USER`), никогда вручную через
`APP_DB_USER`/backend-код.

Для production-релиза, меняющего схему — см. раздел "Обновление /
перезапуск" ниже: миграция должна быть применена **до** пересоздания
`backend`, иначе новый backend откажется стартовать (`app/core/schema_check.py`
зафейлит `/api/ready`, увидев схему из более старой ревизии, чем ожидает
код).

## Первый администратор (bootstrap)

Публичной HTTP-регистрации администратора не существует (см. README,
"First production admin"). Единственный способ создать первого
администратора — оператор, локально на сервере, **до** публичного открытия
сервиса на новой БД:

```bash
docker compose exec -it backend python -m app.cli bootstrap-admin
```

Команда интерактивна (логин/пароль, ввод пароля скрыт), безопасно
повторяема (отказывает, если администратор уже есть) и никогда не печатает
пароль/хэш ни при успехе, ни при ошибке. Восстановленной из backup БД (где
администратор уже есть) bootstrap не требуется.

## Сборка и запуск контейнеров

**backend** — единственный из четырёх backend/DB-lifecycle сервисов
(`backend`, `db-roles-bootstrap`, `db-migrate`, `db-roles-finalize`), у
которого в `docker-compose.yml` есть `build: ./backend`; все четыре явно
объявляют один и тот же `image: vibe-order-infra-backend:latest`, так что
собранный `backend`'ом образ — это ровно то, что запускают и три
one-shot lifecycle-сервиса (см. "Provisioning базы данных" выше). `build:`
используется только для локальной разработки — на production VPS этот
локальный тег никогда не собирается самим Compose, а доставляется явно
(через Registry при обновлении; через `docker save`/`scp`/`docker load`
при самом первом деплое, когда Registry еще не достижим снаружи — см.
README, "Порядок деплоя" → "Первый деплой (bootstrap)") и запускается с
`--no-build`. Production-доставка при обновлении — отдельная: образ
собирается **вне VPS** для `linux/amd64` и доставляется через private
Registry (см. README, "Порядок деплоя" — точные команды `docker buildx
build`/`docker push`/`docker pull`/`docker tag`, включая шаг, которым
запушенный/выкачанный release image ретегируется на этот же локальный тег
`vibe-order-infra-backend:latest`). Этот репозиторий не выполняет `docker
compose build` на самом VPS ни при первом деплое, ни при обновлении.

**frontend** — не контейнеризован отдельно: `frontend/dist` собирается вне
Nginx-контейнера (`npm run build` на build-машине или временным
`node:22-slim` контейнером) и монтируется в `nginx` как read-only bind
mount. Сам образ `nginx:alpine` Node/npm не содержит (см. README,
"Production build frontend").

Запуск: всегда с `--no-build` на production VPS — `docker compose up -d
--no-build` для свежей установки (см. "Provisioning базы данных" →
"Свежая установка" выше) или `docker compose up -d --no-build --no-deps
<service>` для точечного обновления одного сервиса (см. ниже). Голый
`docker compose up -d` без `--no-build` на VPS не используется ни в одном
из этих случаев — при отсутствующем локально теге он собрал бы образ прямо
на VPS через `build: ./backend` вместо использования доставленного release
image.

## Health / readiness

- `GET /api/health` — чистая liveness-проверка (процесс жив), без
  обращения к БД. Публикуется наружу через Nginx.
- `GET /api/ready` — readiness: БД доступна, runtime-роль рабочая,
  подключенная схема совпадает с ожидаемой текущей (не проверяет
  `alembic_version` — reflection-based read-only guard, см.
  `app/core/schema_check.py`). **Не публикуется** наружу Nginx-ом —
  только Docker `HEALTHCHECK` (`backend/healthcheck.py`,
  `backend/Dockerfile`) и локальные операторы изнутри `app-net`.
- Docker-уровень: `docker compose ps` — все долгоживущие сервисы
  `healthy`/`running`, `RestartCount=0`. one-shot DB-lifecycle сервисы —
  `Exited (0)`, это ожидаемое конечное состояние, не ошибка (их собственный
  `HEALTHCHECK` осознанно отключён, см. `docker-compose.yml`'s комментарий у
  `db-roles-bootstrap`) — **но чтобы их увидеть, нужен `docker compose ps
  --all`**: обычный `ps` по умолчанию скрывает остановленные контейнеры
  (включая эти три), так что без `--all` они просто отсутствуют в выводе,
  а не показываются как `Exited (0)`.

Минимальный технический health-check (быстрая проверка, не полная
приёмка):

```bash
docker compose ps --all
curl -fsS https://<домен>/api/health
docker compose exec backend python healthcheck.py && echo "backend ready"
```

Полная процедура приёмки для текущего репозитория — раздел "Текущий
чеклист приёмки после деплоя" ниже. Исторический чеклист в README
("Historical production smoke checklist", commit `7761901`) для
сегодняшнего деплоя не источник истины — он ссылается на
`POST /api/auth/register`, которого в текущем коде больше нет.

## TLS / reverse-proxy

TLS **не выпускается и не автоматизируется этим репозиторием** — сертификат
Let's Encrypt выпускается на VPS отдельно (`certbot`, вне docker-compose
стека) и монтируется в Nginx read-only напрямую с хоста
(`/etc/letsencrypt:/etc/letsencrypt:ro`, см. `docker-compose.yml`). Полная
процедура выпуска (ACME HTTP-01 через `nginx/acme-challenge`) и
обоснование, почему автопродление сертификата пока не автоматизировано —
см. README, разделы "Nginx"/"Почему так"/"Дальше по плану". Reverse-proxy
(Nginx) — единственный сервис, публикующий порты на все интерфейсы; вся
API allowlist-логика, security-заголовки (HSTS/CSP/X-Frame-Options) и
rate-limiting уже настроены в `nginx/conf.d/` и подробно задокументированы
в README — этот файл их не дублирует.

## Обновление / перезапуск (production)

Полная процедура с обоснованием каждого шага — README, "Порядок деплоя".
Критичное дополнение, которого раньше не было явно зафиксировано: **любое
обновление backend обязано применить миграции до пересоздания самого
backend**, иначе новый образ откажется проходить readiness против ещё не
мигрированной схемы (fail-closed by design, не баг):

```bash
# 1. Собрать/запушить/выкачать новый образ backend, как описано в README.
# 2. Backup PostgreSQL, если релиз меняет схему (см. README и RUNBOOK.md).
# 3. Переключить локальный тег на новый образ (docker tag ...), как в README.

# 4. Применить миграции ПЕРЕД пересозданием backend. --no-build не даёт
#    Compose пересобрать образ на VPS; полная цепочка bootstrap -> migrate
#    -> finalize запускается автоматически, т.к. db-roles-finalize зависит
#    от db-migrate, который зависит от db-roles-bootstrap (depends_on:
#    service_completed_successfully). Безопасно и дёшево запускать это на
#    КАЖДЫЙ релиз, даже если он не меняет схему: bootstrap_roles.py
#    идемпотентен, а "alembic upgrade head" на уже актуальной схеме — no-op.
docker compose up -d --no-build db-roles-finalize
docker compose ps --all   # db-roles-bootstrap / db-migrate / db-roles-finalize — Exited (0)

# 5. Только теперь пересоздать backend на новом образе.
docker compose up -d --no-build --no-deps backend

# 6. Smoke-проверка (см. "Текущий чеклист приёмки после деплоя" ниже —
#    НЕ исторический чеклист в README), docker stats --no-stream (см.
#    README, "Resource protection").
```

Frontend/Nginx-обновления — без изменений относительно README (шаги
"Production build frontend"/"Порядок деплоя" остаются точными и
исчерпывающими).

`docker compose down` не используется как часть обновления — см. README.

## Текущий чеклист приёмки после деплоя (current-state smoke checklist)

Этот чеклист — источник истины для приёмки любого текущего/будущего
деплоя (production и, где применимо, локального стека) и проверяет
поведение **текущего** репозитория: публичной HTTP-регистрации
администратора не существует, `POST /api/auth/register` отсутствует как
маршрут. Он **не совпадает** с историческим "Historical production smoke
checklist" в README, зафиксированным на commit `7761901` (тот чеклист
включает успешный `POST /api/auth/register` — маршрут, которого в текущем
коде больше нет) — тот чеклист сохранён в README только как историческое
свидетельство приёмки на тот момент, не как процедура для сегодняшнего
деплоя.

Подставьте `<домен>` = `vibe.elivcloud.org` для production; для локального
стека (см. README, "Полный локальный стек") замените
`https://<домен>` на `http://127.0.0.1:8000` и пропустите шаги, специфичные
для Nginx/Registry (там их нет — override гасит эти сервисы).

1. **Lifecycle one-shots завершены успешно:**
   ```bash
   docker compose ps --all
   # db-roles-bootstrap / db-migrate / db-roles-finalize — Exited (0)
   ```
2. **Backend healthy / readiness:**
   ```bash
   docker compose ps backend        # healthy, RestartCount=0
   docker compose exec backend python healthcheck.py && echo "backend ready"
   ```
3. **Публичный health endpoint:**
   ```bash
   curl -fsS https://<домен>/api/health   # 200
   ```
4. **Текущая модель bootstrap администратора (без публичной регистрации):**
   ```bash
   curl -fsS https://<домен>/api/auth/check
   # {"admin_exists": false} на новой БД - поля "registration_allowed" в
   # ответе нет и быть не должно (см. README, "First production admin")
   ```
   Если `admin_exists: false` — создать первого администратора ДО
   публичного открытия сервиса на этой БД (см. "Первый администратор
   (bootstrap)" выше):
   ```bash
   docker compose exec -it backend python -m app.cli bootstrap-admin
   ```
   `POST /api/auth/register` для этого использовать нельзя — маршрута нет
   в коде вообще (`404`), это не альтернативный путь.
5. **Login текущим auth-эндпоинтом:**
   ```bash
   curl -fsS -X POST https://<домен>/api/auth/login \
     -H 'Content-Type: application/json' \
     -d '{"username": "<имя>", "password": "<пароль>"}'
   # 200, тело содержит JWT
   ```
6. **Representative protected admin request с JWT:**
   ```bash
   TOKEN=<значение из шага 5>
   curl -fsS https://<домен>/api/auth/me -H "Authorization: Bearer $TOKEN"   # 200
   curl -o /dev/null -s -w '%{http_code}\n' https://<домен>/api/auth/me     # без токена - 401
   ```
7. **Representative public application/service endpoint:**
   ```bash
   curl -fsS https://<домен>/api/admin-settings/active   # 200, публичный, без токена
   ```
8. **Frontend / SPA availability:**
   ```bash
   curl -o /dev/null -s -w '%{http_code}\n' https://<домен>/          # 200
   curl -o /dev/null -s -w '%{http_code}\n' https://<домен>/admin     # 200, прямой refresh, не 404
   ```
9. **Nginx / публичный периметр** (только production, где Nginx поднят):
   ```bash
   curl -o /dev/null -s -w '%{http_code}\n' https://<домен>/docs         # 404
   curl -o /dev/null -s -w '%{http_code}\n' https://<домен>/openapi.json # 404
   curl -o /dev/null -s -w '%{http_code}\n' https://<домен>/redoc        # 404
   curl -o /dev/null -s -w '%{http_code}\n' https://registry-vibe.elivcloud.org/v2/
   # 401 с Basic auth challenge
   ```
10. **Итоговый статус контейнеров:**
    ```bash
    docker compose ps --all
    # все долгоживущие сервисы - healthy/running, RestartCount=0;
    # три one-shot lifecycle-сервиса - Exited (0)
    ```

Дополнительные protected-маршруты для расширенной проверки шага 6 (CRUD
услуг, список/приоритизация заявок, аналитика) — README, "API и публичный
security allowlist", раздел "Public/protected route matrix". Операционные
проблемы после деплоя (сбой старта, недоступная БД, неудачная миграция,
откат) — [RUNBOOK.md](RUNBOOK.md).
