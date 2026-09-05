# vibe-order-infra

Full-stack система приёма, обработки и приоритизации клиентских заявок для
премиального автомобильного детейлинга AUREL Detailing: публичная форма
заявки, административная панель с JWT-аутентификацией, explainable-
приоритизация заявок и поведенческая аналитика — на FastAPI/PostgreSQL/Vite,
за Nginx, в Docker Compose с приватным Docker Registry.

Публичный сайт: **https://vibe.elivcloud.org** (см. "Статус проекта" ниже —
там честно разведено, что именно исторически проверено на этом VPS и что
верно только для текущего состояния репозитория).

## Возможности

- **Публичная заявка** — клиент выбирает услугу и бюджет (ползунок в
  диапазоне услуги), заполняет контактные данные и автомобильную анкету,
  отправляет заявку без регистрации.
- **Администрирование** — JWT-аутентификация без публичной HTTP-регистрации:
  первый администратор создаётся только оператором через CLI bootstrap;
  полный CRUD услуг.
- **Обработка заявок** — список/поиск/фильтр по приоритету, detail-карточка
  заявки с explainable scoring (приоритет Высокий/Средний/Стандартный,
  человекочитаемые причины, рекомендуемое действие и команда).
- **Поведенческая аналитика** — агрегированные метрики поведения на странице
  (без сторонних сервисов), статистика за 24 часа/7 дней/30 дней с
  detail-разбивкой по конкретной заявке.

## Ключевые инженерные решения

- **Трёхролевая модель PostgreSQL** — кластерный admin / migration-owner
  (Alembic) / runtime-роль без прав DDL: backend физически не может
  выполнить DDL, даже если приложение будет скомпрометировано.
- **Alembic-управляемый жизненный цикл схемы** —
  `postgres → db-roles-bootstrap → db-migrate → db-roles-finalize → backend`,
  fail-closed readiness-проверка вместо `Base.metadata.create_all()`: backend
  отказывается стартовать при несовпадении схемы, а не пытается её починить.
- **Усыновление legacy-схемы** — одноразовая проверка точного фингерпринта
  существующих таблиц перед первой миграцией; база, форма которой хоть
  немного отличается от ожидаемой, отклоняется, а не "усыновляется
  по-хорошему".
- **Контролируемый bootstrap администратора** — публичной HTTP-регистрации
  не существует; Argon2id-хэширование и Postgres advisory lock против гонки
  при создании первого администратора.
- **Nginx allowlist + security headers** — segment-safe allowlist по
  `/api/`-сегментам (Nginx не проверяет JWT и не различает методы — это
  делает только backend), HSTS, CSP, закрытые `/docs`/`/redoc`/`/openapi.json`.
- **Hardened containers** — `no-new-privileges`, `cap_drop: ALL` с точечным
  `cap_add`, `read_only` + tmpfs где применимо, digest-пиннинг образов.
- **Immutable production-image flow** — релиз собирается вне VPS, доставляется
  через приватный Registry, `--no-build` запрещает сборку образа на VPS.
- **Backup/restore с верификацией** — restore в заведомо чистую пересозданную
  БД, а не поверх текущей (исключает тихий drift); round-trip
  `pg_dump`/`pg_restore` проверен тестом и в CI.
- **CI на реальной PostgreSQL** — интеграционные тесты (включая Alembic
  fresh-install/legacy-upgrade/drift) идут против настоящего
  `postgres`-контейнера, не мока.

## Стек

| Слой | Технологии |
|---|---|
| Frontend | Vite, TypeScript, vanilla DOM (без UI-framework) |
| Backend | FastAPI, SQLAlchemy 2, Pydantic v2, Argon2id, JWT |
| База данных | PostgreSQL 16.15, три изолированные роли доступа |
| Миграции | Alembic (4 ревизии), fail-closed readiness-проверка |
| Reverse proxy | Nginx (API allowlist, TLS, HSTS, CSP) |
| Контейнеризация | Docker Compose, приватный Docker Registry |
| CI | GitHub Actions — backend/frontend/containers |

## Архитектура (обзор)

Система состоит из публичного SPA, административного SPA-роута, Nginx
(единственная точка входа наружу), FastAPI backend и PostgreSQL, плюс
одноразовые lifecycle-сервисы миграции схемы, приватный Docker Registry —
обычный сервис production-стека по умолчанию, без Compose-профиля, — и
pgAdmin — единственный по-настоящему опциональный сервис (Compose-профиль
`admin`). Полная схема с диаграммой, границами доверия, ролевой
моделью БД, жизненным циклом схемы и топологией local/production —
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); построчная ASCII-схема сети и
структура проекта также приведены ниже, в разделах "Архитектура" и
"Структура проекта".

## Качество и верификация

Прогон от 2026-09-05 на техническом baseline `28932b1` — последнем коммите
финального технического аудита перед этим документационным pass'ом
(полные команды и контекст — разделы "Локальная разработка и тесты"/"CI"
ниже):

- backend: **650 passed, 0 skipped** (реальная PostgreSQL, полный
  prerequisite-набор); без PostgreSQL — 287 passed, 363 skipped.
- frontend: **469 passed**.
- TypeScript (`tsc --noEmit`) — pass; production build (`npm run build`) — pass.
- `npm audit` (все severity) — 0 vulnerabilities на момент проверки;
  фактический CI-gate — `--audit-level=moderate` (см. "CI").
- Docker build backend — pass; `docker compose config` (default и `admin`
  профиль) — pass.

Эти числа — результат конкретного прогона на конкретном состоянии кода, не
гарантия на будущее; актуальное значение — самостоятельный прогон командами
из разделов выше.

## Быстрый старт

Минимальный путь для обзора; полная процедура (включая обязательную замену
`JWT_SECRET_KEY`) — раздел "Полный локальный стек" ниже и
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

```bash
cp .env.example .env   # затем сгенерировать реальный JWT_SECRET_KEY, см. ниже
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d
cd frontend && npm ci && npm run dev
```

Frontend — `http://localhost:5173`; backend напрямую — `http://127.0.0.1:8000`.
Первый администратор:
`docker compose -f docker-compose.yml -f docker-compose.local.yml exec -it backend python -m app.cli bootstrap-admin`.

## Security и эксплуатация — кратко

JWT-аутентификация выполняется backend'ом (Nginx — только allowlist по
сегментам, не авторизация); три раздельные роли PostgreSQL — кластерный
admin (суперпользователь, только для bootstrap/lifecycle, backend им не
пользуется) и два непривилегированных — migration-owner и runtime (см.
"Ключевые инженерные решения" выше и
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), "Ролевая модель базы
данных"); hardened-контейнеры; секреты не хранятся в git; HSTS и CSP
включены; backup
обязателен перед любым релизом, меняющим схему БД. Полное описание — раздел
"Security notes / ограничения" ниже, границы доверия — в
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), операционные процедуры — в
[docs/RUNBOOK.md](docs/RUNBOOK.md).

## О проекте

Проект начинался как учебный full-stack инфраструктурный проект — приём и
обработку клиентских заявок для премиального автомобильного детейлинга
(AUREL Detailing) требовалось реализовать в production-like стиле:
минимальный набор публичных портов, разделение Docker-сетей, security-подход,
приближенный к реальному деплою.

Далее репозиторий самостоятельно продвинулся заметно дальше исходного
задания: добавлены Alembic-управляемый жизненный цикл схемы с трёхролевой
моделью прав, административная панель с JWT-аутентификацией и
explainable-приоритизацией заявок, поведенческая аналитика, container
hardening, immutable-образы, CI на реальной PostgreSQL и полный набор
операционных runbook-процедур (backup/restore, ротация секретов, recovery
администратора). Текущее состояние репозитория отражает именно этот более
поздний этап работы, зафиксированный техническим baseline `28932b1` (см.
"Качество и верификация" выше). Историческая ручная production-приемка на VPS
зафиксирована на более раннем коммите `7547704` (см. "Статус проекта" ниже)
— из содержимого репозитория не следует, что все изменения после него уже
задеплоены на этот VPS.

## Документация

| Документ | Что внутри |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Границы доверия, ролевая модель БД, жизненный цикл схемы, топология local/production, ключевые инженерные решения |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Процедуры деплоя: локально/CI/production, provisioning БД, порядок обновления, текущий (актуальный) чеклист приёмки после деплоя |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | Операционные инциденты: backend не стартует, migration failure, restore, ротация секретов |
| ["CI"](#ci) (ниже в этом файле) | GitHub Actions: backend-tests / frontend / containers |

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

Этот раздел намеренно разделяет два разных факта, которые легко перепутать:
что реально проверено вручную на живом production VPS, и что просто
существует в текущем репозитории.

**Последняя задокументированная ручная production-приемка на VPS** —
commit `7547704` (см. "Финальная production-приемка" ниже). Все пункты
подраздела "Последняя VPS-приемка" ниже описывают состояние именно на этот
коммит, а не текущее состояние репозитория.

**Текущее состояние репозитория** продвинулось дальше на несколько стадий
hardening (database lifecycle, публичный API, runtime/supply chain,
CI/deployment operations — см. "Дальше по плану" ниже) без отдельной
повторной ручной VPS-приемки, задокументированной в этом README. Эти
стадии зафиксированы техническим baseline `28932b1` (`fix: finalize
repository review readiness`, коммит поверх `95299dc`) — портфолио-
документация поверх него коммитится отдельно и не меняет код, тесты или
инфраструктуру. Из содержимого репозитория не следует, какие именно из
этих изменений — ни зафиксированные на `95299dc`, ни добавленные коммитом
`28932b1` — фактически задеплоены на текущий VPS, поэтому подраздел
"Текущее репозиторное состояние" ниже описывает только то, что верно для
самого репозитория, без утверждений про VPS. Автоматических тестов это
ограничение не касается: их результаты, приведенные в этом README,
получены прогоном на техническом baseline `28932b1` (2026-09-05, см.
"Локальная разработка и тесты"), независимо от статуса VPS-приемки.

### Последняя VPS-приемка (commit `7547704`)

- **Инфраструктура**: `postgres:16.14-alpine` и собственный образ
  `backend` — запущены на production VPS, `RestartCount=0` у всех
  сервисов. `registry` запущен и работает (у Registry по дизайну нет
  Docker-healthcheck, см. "Почему так" ниже). `pgAdmin` запускается
  только через профиль `admin`, по требованию. Watchtower на этот момент
  ещё присутствовал в инфраструктуре в label-based opt-in режиме (ни один
  сервис не был включен в автообновление, все label стояли в `"false"`) —
  удалён позже, в Stage 3 (см. "Текущее репозиторное состояние" ниже).
- **HTTPS**: Let's Encrypt сертификат на один SAN на оба домена
  (`vibe.elivcloud.org`, `registry-vibe.elivcloud.org`); `https://vibe.elivcloud.org`
  отвечает `200`; HTTP редиректит на HTTPS, кроме ACME challenge и
  `/healthz`. HSTS на этот момент ещё не был включен — конфигурация Nginx
  сознательно его не задавала (добавлен позже, в Stage 4, см. "Текущее
  репозиторное состояние" ниже).
- **Registry**: пользователь создан через `registry/create-user.sh`, полный
  push/pull smoke-test пройден (подробности — в разделе "Почему так");
  `GET /v2/` без credentials возвращает `401` с Basic auth challenge.
- **PostgreSQL / pgAdmin**: 5432 наружу не публикуется; pgAdmin проверен
  через SSH-туннель и остановлен после проверки.
- **Backend + frontend, включая административную панель, JWT-аутентификацию,
  приоритизацию заявок и поведенческую аналитику**: реализованы и
  **задеплоены на production VPS** по состоянию на commit `7547704`.
  Полная ручная production-приемка пройдена (см. "Ручная end-to-end
  приемка" ниже): регистрация первого администратора, повторные
  login/logout, CRUD услуг, публичная форма заявки, behavior metrics,
  explainable scoring с приоритетами Высокий/Средний/Стандартный,
  поведенческая аналитика за 24 часа/7 дней/30 дней (включая состояния
  "метрики есть"/"метрики отсутствуют"), закрытые технические endpoints
  (`/docs`, `/redoc`, `/openapi.json`, неизвестные `/api/*`).
- Первый администратор существует — `GET /api/auth/check` возвращал
  `admin_exists: true` (поле `registration_allowed` на тот момент в ответе
  ещё присутствовало — убрано позже вместе с удалением публичной
  HTTP-регистрации, см. "First production admin" ниже).

### Текущее репозиторное состояние (технический baseline `28932b1`)

Ничего из перечисленного ниже не следует читать как "задеплоено на VPS" —
только как состояние репозитория, зафиксированное техническим baseline
`28932b1` (2026-09-05; портфолио-документация поверх него коммитится
отдельно и не меняет ничего из перечисленного). Родительский коммит —
`95299dc`; сам по себе `95299dc` перечисленные ниже изменения еще не
содержит — они добавлены финальным техническим коммитом `28932b1`.

- **Инфраструктура**: `nginx:1.30.4-alpine`, `postgres:16.15-alpine`,
  `registry:3.1.1` и собственный образ `backend` (точные версии и digest —
  см. "Версии образов" ниже). `backend` начиная со Stage 3 имеет Docker
  healthcheck (см. "Backend healthcheck и readiness"). Watchtower удалён
  из инфраструктуры в Stage 3 (см. "Почему так") — обновления образов
  теперь только явные, вручную.
- **HSTS** включен (Stage 4) в текущей конфигурации Nginx — см. "Security
  notes / ограничения" ниже за точной конфигурацией заголовка. На VPS-
  приемке `7547704` (см. выше) HSTS ещё не был включен.
- Backend + frontend покрыты тестами локально: backend suite на реальной
  PostgreSQL — **650 passed, 0 skipped**, frontend suite — **469 passed**
  (технический baseline `28932b1`, 2026-09-05 — см. "Локальная разработка и
  тесты"/"CI"). Эти числа не описывают состояние на момент VPS-приемки
  `7547704`, а также не обязательно совпадают с тем, что дал бы прогон
  непосредственно на родительском коммите `95299dc`.

Не сделано осознанно (см. "Security notes / ограничения" ниже):
автоматизация продления сертификата Let's Encrypt.

---

**Подробный технический разбор и историческая приемка** — дальше идёт
построчное описание архитектуры, backend/frontend, Nginx, Docker Compose,
процедур деплоя и историческая ручная VPS-приемка. Для быстрого обзора
достаточно уже прочитанного выше.

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

(Watchtower удалён из инфраструктуры в Stage 3 — обновления образов теперь
только явные, ручные: выбор конкретного immutable release, `docker pull`
этого тега, явный `docker tag` на локальный alias
`vibe-order-infra-backend:latest`, затем `docker compose up -d --no-build`
— см. "Порядок деплоя" → "Обновление / повторный деплой" ниже; не голый
`docker compose pull && docker compose up -d`, который полагался бы на
mutable remote `latest` и не запрещал бы build-fallback.)
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
├── docker-compose.local.yml # override для локальной разработки — см. "Полный локальный стек" ниже
├── .env.example
├── .gitignore
├── README.md
├── .github/workflows/ci.yml # GitHub Actions CI — см. раздел "CI" ниже
├── docs/
│   ├── DEPLOYMENT.md        # процедура деплоя (dev/CI/production)
│   └── RUNBOOK.md           # операционный runbook
├── backend/
│   ├── app/
│   │   ├── core/            # config (pydantic-settings), database (engine/session/Base),
│   │   │                    #   schema_check (fail-closed readiness guard), exceptions
│   │   ├── models/          # SQLAlchemy ORM: Admin, Application, BehaviorMetric, AdminSetting, ...
│   │   ├── schemas/         # Pydantic Create/Update/Read + бизнес-валидация
│   │   ├── crud/            # доступ к БД, без HTTP-специфики
│   │   ├── routes/          # HTTP-обработчики (/api/applications, /api/behavior-metrics,
│   │   │                    #   /api/admin-settings, /api/auth, /api/analytics)
│   │   ├── services/        # application scoring, behavior analytics
│   │   ├── db_admin/        # bootstrap_roles.py, adopt_legacy.py — Stage 2 DB lifecycle
│   │   ├── cli.py           # оператор CLI (bootstrap-admin)
│   │   └── main.py          # FastAPI app, lifespan (schema readiness guard), сборка /api-роутера
│   ├── alembic/              # Alembic-миграции (versions/, env.py)
│   ├── alembic.ini
│   ├── tests/                # pytest: unit + integration (реальный PostgreSQL)
│   ├── healthcheck.py         # Docker HEALTHCHECK (GET /api/ready изнутри контейнера)
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── api/              # fetch-клиент к /api, типы запросов/ответов
│   │   ├── metrics/          # агрегированные behavior metrics (без сторонних сервисов)
│   │   ├── pages/            # home (клиентская страница), admin (панель, /admin, JWT), notFound
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

Четыре сущности:

- **Admin** — администратор панели: `username` (уникальный), Argon2id-хэш
  пароля (`password_hash`), флаг `is_active`. Единственный источник правды
  о том, кто admin — используется JWT-аутентификацией (см. "Frontend" и
  "API и публичный security allowlist" ниже).
- **Application** — клиентская заявка: контактные данные, автомобильная
  анкета (использование и класс автомобиля, количество автомобилей, кто
  обращается, марка/модель/год и состояние автомобиля), детали запроса
  (формат обслуживания, тип обращения, желаемый срок записи, выбранная
  услуга, бюджет), предпочитаемый способ и время связи.
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

Схема управляется Alembic-миграциями (`backend/alembic/versions/`, четыре
ревизии на текущий момент) — `Base.metadata.create_all()` больше не
используется. Старт приложения (`lifespan` в `app/main.py`) — read-only
проверка (`app/core/schema_check.py`), которая отказывает в старте, если
подключенная схема не совпадает с ожидаемой текущей ревизией, а не
пытается сама что-то создать/поправить. Подробная процедура (роли,
one-shot DB-lifecycle сервисы, свежая установка и усыновление legacy-базы)
— [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), раздел "Provisioning базы
данных".

## Frontend

Стек: **Vite**, **TypeScript**, vanilla DOM (без тяжелого UI-framework),
шрифт Golos Text через `@fontsource/golos-text`.

Клиентский flow:

1. `GET /api/admin-settings/active` — список активных услуг.
2. Пользователь выбирает услугу.
3. Выбирает бюджет ползунком внутри диапазона услуги (`budget_min`..`budget_max`).
4. Видит summary "Ваша заявка" с выбранной услугой и бюджетом.
5. Заполняет форму: контактные данные; автомобильная анкета (использование
   и класс автомобиля, количество автомобилей, кто обращается, марка/
   модель/год и состояние автомобиля); детали запроса (формат
   обслуживания, тип обращения, желаемый срок записи); способ связи.
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

Административная страница (`frontend/src/pages/admin.ts`) отвечает
клиентскому роуту `/admin` и публикуется Nginx как обычный SPA-путь
(`location = /admin` / `location = /admin/`, см. "Nginx" ниже) — сам Nginx
никакой авторизации не делает, ей полностью занимается backend через JWT.

Публичной HTTP-регистрации администратора **не существует** (Stage 1A
remediation, "unsafe first-admin bootstrap"): на пустой БД никакой
publicly-доступный запрос не может создать первого администратора. Первый
администратор создается только оператором — локально на сервере, ДО
публичного открытия сервиса на новой БД:

```bash
docker compose exec -it backend python -m app.cli bootstrap-admin
```

см. "First production admin" ниже за полной процедурой и объяснением, почему
уже существующему/восстановленному из backup администратору bootstrap не
требуется.

- `GET /api/auth/check` — есть ли уже созданный администратор
  (`{"admin_exists": true|false}` — поля `registration_allowed` в ответе
  больше нет);
- пока администратора нет — страница показывает обычную форму входа с
  поясняющим текстом ("Администратор ещё не настроен…"), а не форму
  регистрации; попытки входа корректно отклоняются (`401`), пока bootstrap
  не выполнен;
- иначе — форма входа (`POST /api/auth/login`), токен хранится в
  `sessionStorage` (не `localStorage`, чтобы забытая открытой вкладка не
  держала сессию бессрочно) и прикладывается как `Authorization: Bearer` к
  защищенным запросам; `GET /api/auth/me` подтверждает валидность сессии
  при каждой загрузке страницы.

После входа административная панель состоит из трех вкладок:

- **Услуги** — CRUD активных/неактивных услуг (`/api/admin-settings`);
- **Заявки** — список/поиск/фильтр заявок по приоритету
  (`/api/applications`, `/api/applications/prioritized`) и detail modal
  (карточка) заявки. Каждая заявка получает **explainable scoring** —
  приоритет **Высокий** / **Средний** / **Стандартный** с человекочитаемым
  списком причин (`reasons`), рекомендуемым действием
  (`recommended_action`), рекомендуемой командой (`recommended_team`) и
  флагом необходимости личного менеджера (`requires_personal_manager`). В
  карточке заявки также отображаются агрегированные behavior metrics —
  отдельно показано состояние "метрики есть" и состояние "метрики
  отсутствуют" (например, если отправка `POST /api/behavior-metrics`
  не удалась — best-effort, см. "Frontend" flow выше);
- **Статистика** — поведенческая аналитика (`/api/analytics/*`) за три
  периода: **24 часа**, **7 дней**, **30 дней** — общие показатели по
  заявкам, а также та же detail-аналитика с разделением "метрики
  есть"/"метрики отсутствуют" для конкретной заявки.

## API и публичный security allowlist

Снаружи через Nginx (`vibe.elivcloud.org`, порт 443) разрешено проксирование
только конкретных `/api/`-сегментов, каждый — **segment-safe**: либо точный
путь без вложенных подпутей (`location =`), либо префикс с обязательным
завершающим `/` перед вложенным путём (`location ^~ /api/xxx/`). Простой
широкий префикс без слэша (`^~ /api/auth`, `^~ /api/applications` и т.п.)
здесь намеренно НЕ используется — он совпал бы не только с настоящими
вложенными путями, но и с любым другим путём, который просто начинается с
той же строки (`/api/authentic`, `/api/applications-archive` и т.д. — см.
"Nginx" ниже про полный список location'ов и обоснование). Nginx здесь
работает только как allowlist по сегменту — он НЕ проверяет JWT и НЕ
различает методы: какие конкретные пути внутри сегмента существуют, какой
HTTP-метод на них допустим и какие из них public/protected, решает
исключительно backend (FastAPI route-декларации + `Depends(get_current_admin)`
для защищенных). Всё остальное под `/api/` возвращает `404` прямо в Nginx и
не доходит до backend.

### Public/protected route matrix

**Public** (без токена):

| Метод | Путь |
|---|---|
| GET  | `/api/health` |
| GET  | `/api/auth/check` |
| POST | `/api/auth/login` |
| GET  | `/api/admin-settings/active` |
| POST | `/api/applications` |
| POST | `/api/behavior-metrics` |

**Protected** (требуют `Authorization: Bearer <JWT>`, backend отвечает `401`
без валидного токена). Collection- и item-маршруты разведены по отдельным
строкам — не каждый метод в таблице применим к каждому пути:

| Ресурс | Метод | Путь |
|---|---|---|
| Auth | GET | `/api/auth/me` — текущий администратор |
| Admin settings, collection | POST | `/api/admin-settings` — создать услугу |
| Admin settings, collection | GET | `/api/admin-settings` — полный список (не только active) |
| Admin settings, item | GET | `/api/admin-settings/{id}` |
| Admin settings, item | PATCH | `/api/admin-settings/{id}` |
| Admin settings, item | DELETE | `/api/admin-settings/{id}` |
| Applications, collection | GET | `/api/applications` — список заявок |
| Applications, prioritized | GET | `/api/applications/prioritized` |
| Applications, item | GET | `/api/applications/{id}` |
| Applications, item | PATCH | `/api/applications/{id}` |
| Applications, item | DELETE | `/api/applications/{id}` |
| Behavior metrics, collection | GET | `/api/behavior-metrics` — список |
| Behavior metrics, item | GET | `/api/behavior-metrics/{id}` |
| Behavior metrics, item | PATCH | `/api/behavior-metrics/{id}` |
| Behavior metrics, item | DELETE | `/api/behavior-metrics/{id}` |
| Analytics, overview | GET | `/api/analytics/overview` |
| Analytics, application detail | GET | `/api/analytics/applications/{application_id}` |

(`POST /api/applications` и `POST /api/behavior-metrics` — создание — не
входят сюда: это public-маршруты, см. таблицу выше. Public/protected
границы не менялись — это только уточнение отображения уже существующей
матрицы.)

Также заблокированы Nginx-ом целиком, отдельно от SPA fallback: `/docs`,
`/openapi.json`, `/redoc` и `/redoc/` (exact `/redoc` не покрывает `/redoc/`
— это разные URI для Nginx, отдельный exact-location закрывает и его) —
API не должен светить схему эндпоинтов наружу, даже после включения auth. `/api/openapi.json` тоже не проксируется — падает в общий `/api/` →
`404`, т.к. не совпадает ни с одним разрешённым сегментом. Внутри FastAPI
OpenAPI не отключен (нужен для локальных тестов/разработки) — ограничение
только на production-периметре Nginx.

Сам backend (`routes/`) реализует полный CRUD для всех сущностей и
собственную JWT-аутентификацию (Argon2id-хэширование паролей, единственный
источник правды о том, кто admin) — Nginx не дублирует эту матрицу через
`limit_except`/`auth_request`, чтобы не было двух независимых мест, решающих
одно и то же.

`/admin` и `/admin/` (frontend SPA, exact-match) публикуются как обычный
статический путь — сама страница делает клиентский auth-gate через те же
`/api/auth/*` эндпоинты (см. "Frontend" выше). Более глубокие подпути вида
`/admin/whatever` не заблокированы отдельно — они попадают в общий SPA
fallback (см. "Nginx" ниже) и получают тот же `200` с `index.html`, что и
любой другой нераспознанный frontend-путь.

## Nginx

- **HTTP (80)**: отдает ACME challenge (`/.well-known/acme-challenge/`) и
  `/healthz` для внутреннего Docker healthcheck; весь остальной трафик
  редиректит на HTTPS (`301`).
- **HTTPS (443)**: сертификат Let's Encrypt (один SAN на
  `vibe.elivcloud.org` и `registry-vibe.elivcloud.org`), `ssl_protocols
  TLSv1.2 TLSv1.3` заданы один раз глобально в `nginx.conf`.
- **Статика**: собранный frontend (`frontend/dist`) раздается напрямую как
  webroot (`root /usr/share/nginx/html`).
- **`/admin` SPA-роут**: точные `location = /admin` и `location = /admin/`
  отдают тот же `index.html`, что и `/` (`try_files /index.html =404;`) —
  это гарантирует, что прямой refresh на `/admin`/`/admin/` работает явным
  правилом, а не как побочный эффект общего SPA fallback. Более глубокие
  подпути (`/admin/whatever`) НЕ покрыты этим exact-match и намеренно НЕ
  блокируются отдельно — они проваливаются в общий SPA fallback ниже и
  получают тот же `200` с `index.html`, что и любой другой нераспознанный
  frontend-путь; клиентский `router.ts` резолвит их в `'not-found'` уже на
  стороне браузера. Query string не мешает совпадению (Nginx матчит
  `location` по URI без query part).
- **SPA fallback** (`try_files $uri $uri/ /index.html`) применяется ко всем
  остальным нераспознанным frontend-путям (включая `/admin/whatever`) —
  технические пути `/api/*`, `/docs`, `/openapi.json`, `/redoc`, `/redoc/`
  заблокированы отдельными `location`-блоками с более высоким приоритетом
  (`=`/`^~` префиксы всегда обгоняют `location /`), поэтому в SPA fallback
  они не попадают.
- **`/assets/`** (хэшированные Vite JS/CSS/шрифты) — честный `404` при
  отсутствии файла (`try_files $uri =404`), а не подмена на `index.html` с
  200.
- **API allowlist** реализован segment-safe: для каждого допустимого
  эндпоинта — либо `location =` (пути без вложенных подпутей: `/api/health`,
  коллекции `/api/admin-settings`/`/api/applications`/`/api/behavior-metrics`),
  либо `location ^~ /api/xxx/` с обязательным завершающим `/` перед вложенным
  путём (`/api/auth/`, `/api/admin-settings/`, `/api/applications/`,
  `/api/behavior-metrics/`, `/api/analytics/`). Обязательный `/` в
  префиксных location'ах — принципиальный момент: без него `^~ /api/auth`
  пропустил бы также `/api/authentic`, `^~ /api/applications` — также
  `/api/applications-archive`, и т.д. (см. "API и публичный security
  allowlist" выше про полную route matrix). Без `limit_except` — метод/
  public-protected различает сам backend, а не Nginx. Всё прочее под
  `/api/` — `404` через `location ^~ /api/ { return 404; }`, включая
  exact-пути без реального backend-эндпоинта на них (`/api/auth`,
  `/api/analytics`) и любые pseudo-похожие сегменты
  (`/api/healthcheck`, `/api/admin-settings-old` и т.п.).
- Backend проксируется по внутреннему Docker DNS-имени сервиса —
  `proxy_pass http://backend:8000;` (без переменной/резолвера, имя
  разрешается при старте/reload Nginx внутри `proxy-net`; глобального
  `resolver` в `nginx.conf` нет, поэтому Nginx требует, чтобы `backend` уже
  существовал в момент старта — это выражено через `depends_on: - backend`
  в `docker-compose.yml`).
- **Body size**: `client_max_body_size` НЕ задан глобально в `nginx.conf`
  (глобальный `0` убран как production-blocker — реальные payload'ы этого
  сайта на порядки меньше 1 МБ). Основной сайт (`vibe.elivcloud.org`,
  443-блок) задает собственный `client_max_body_size 1m;`, покрывающий форму
  заявки, behavior-metrics, auth и административный API. Docker Registry
  сохраняет свой unlimited `client_max_body_size 0;` (без ограничения —
  крупные слои образов), заданный на уровне HTTPS `server{}` (443) целиком —
  изолированно в отдельном vhost-файле `registry-vibe.elivcloud.org.conf` —
  он не наследуется основным сайтом, т.к. это отдельный `server{}` в
  отдельном файле, и основной сайт не наследует Registry limit.
- `server_tokens off;`, `X-Content-Type-Options: nosniff`, `X-Frame-Options:
  DENY` (защита от clickjacking — актуальна с публикацией `/admin`) и
  `Referrer-Policy` — во всех активных server-блоках `vibe.elivcloud.org.conf`
  (подробнее — в разделе "Почему так").

## Docker Compose

Сервисы: `postgres`, `backend`, `nginx`, `registry`, `pgadmin` (профиль
`admin`).

**backend**: Compose-декларация — `build: ./backend` (build context
только для локальной разработки — `docker compose up`/`build` на
ноутбуке разработчика). На production VPS этот `build:` фактически не
используется никогда — ни при первом деплое, ни при последующих
обновлениях: production-доставка всегда идет как явный `--no-build`
поверх уже присутствующего локально тега `vibe-order-infra-backend:latest`
(см. "Порядок деплоя" ниже — единая модель для первого деплоя, обновления
и rollback: образ детерминированно оказывается на VPS под immutable-
идентификатором, ретегируется на этот локальный тег, и только потом
`docker compose up -d --no-build` создает/пересоздает контейнер). При
первом деплое, когда ни Registry, ни сам backend на VPS еще не
существуют, образ доставляется не через `docker pull` (Registry еще
физически недостижим снаружи — см. "Первый деплой (bootstrap)"), а как
versioned artifact с контрольной суммой, тем же принципом, что и
`frontend/dist` (см. "Production build frontend"); при последующих
обновлениях/откате — через `docker pull`/`docker tag` из уже работающего
Registry (`registry-vibe.elivcloud.org`); сети — `app-net` (доступ к
`postgres`) и `proxy-net` (доступность для Nginx); порт 8000 НЕ
публикуется на host (нет `ports:`);
лимит памяти 192M (reservation 128M, 0.50 cpu). Stage 3: `healthcheck`
через `backend/healthcheck.py` (stdlib `urllib`, обращается к
`GET /api/ready` на `127.0.0.1:8000` изнутри контейнера) — см. "Backend
healthcheck и readiness" ниже.

**nginx**: read-only bind mount `./frontend/dist:/usr/share/nginx/html:ro`
(собирается вне контейнера, см. "Production build frontend" ниже), плюс
`nginx.conf`/`conf.d`/`acme-challenge`/сертификаты Let's Encrypt — тоже
read-only; `depends_on: registry: condition: service_started, backend:
condition: service_healthy` (Stage 3: у backend теперь есть реальный
healthcheck, поэтому Nginx стартует только после того, как backend
реально готов принимать трафик — у registry healthcheck по-прежнему
намеренно нет, см. ниже).

**postgres**: без публикации порта на host; healthcheck через `pg_isready`;
наибольшая доля лимита памяти среди всех сервисов (см. "Resource
protection").

**registry**: без публикации порта на host; Basic Auth через
`REGISTRY_AUTH=htpasswd` и файл `registry/auth/htpasswd` (см. "Почему так").

**pgadmin**: профиль `admin`, порт только `127.0.0.1:5050` (см. раздел
"Почему так").

Все пять сервисов используют `logging: driver: json-file` с `max-size:
"10m"`, `max-file: "3"` — подробнее в разделе "Resource protection".

## Обязательные переменные окружения (.env)

`.env` **обязателен** для запуска — без него `docker compose up`/`config`
откажется стартовать. Критичные переменные (учетные данные PostgreSQL,
учетные данные pgAdmin) объявлены в `docker-compose.yml` в форме
`${VAR:?VAR must be set}`: если переменная не задана, Compose падает с
понятной ошибкой вместо того, чтобы тихо подставить пустую строку.

Обязательны:

- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` — кластерный
  bootstrap/admin-креденшл. Начиная со Stage 2 backend **им не
  пользуется** — только контейнер `postgres` (первичная инициализация
  кластера) и одноразовые сервисы `db-roles-bootstrap`/`db-roles-finalize`
  (`app/db_admin/bootstrap_roles.py`), а также ручной
  `app/db_admin/adopt_legacy.py` при усыновлении legacy-базы.
- `MIGRATION_DB_USER`, `MIGRATION_DB_PASSWORD` — миграционная/владеющая
  роль. Единственная роль, от имени которой когда-либо подключается
  Alembic (сервис `db-migrate`, `backend/alembic/env.py`) — `CREATE`/
  `USAGE` на схему `public`, не суперпользователь.
- `APP_DB_USER`, `APP_DB_PASSWORD` — runtime-роль. Единственная роль, от
  имени которой backend подключается к БД в обычной работе
  (`app/core/config.py`). Без прав DDL и без доступа к `alembic_version`
  (см. `app/db_admin/bootstrap_roles.py` и
  `backend/tests/test_db_role_privileges.py`).
  Три роли выше — не взаимозаменяемые вкусы одного и того же креденшла:
  они разделены намеренно, чтобы ни backend, ни сама Alembic-миграция не
  располагали правами сверх необходимых им — подробнее и с точным списком
  grants см. [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), раздел
  "Конфигурация и переменные окружения".
- `PGADMIN_DEFAULT_EMAIL`, `PGADMIN_DEFAULT_PASSWORD` (нужны, только если
  запускается профиль `admin`, но объявлены обязательными и там).
- `JWT_SECRET_KEY` — секрет для подписи JWT (`app/core/config.py`,
  `Settings.jwt_secret_key`). Объявлен в `docker-compose.yml` как
  `${JWT_SECRET_KEY:?JWT_SECRET_KEY is required}` — без fallback, т.к.
  отсутствующий/пустой секрет не должен незаметно запустить backend с
  предсказуемым JWT. Требования к значению (проверяются самим Settings при
  старте backend, а не только документацией):
  - минимум 32 символа (`Field(..., min_length=32)`);
  - не должен совпадать с известными placeholder-значениями (`changeme`,
    `secret`, значение из `.env.example` и т.п.) — Settings явно отклонит
    такой секрет при старте.
  Сгенерировать реальный секрет локально, любой вариант:
  ```bash
  openssl rand -hex 32
  # или
  python -c "import secrets; print(secrets.token_urlsafe(64))"
  ```
  **Не коммитить** реальное значение в git ни в каком виде. На VPS секрет
  хранится только в `.env` (не в самом repo) с правами `600`:
  ```bash
  chmod 600 .env
  ```

Необязательны (есть безопасные значения по умолчанию, совпадающие с
`Settings` в `app/core/config.py`):

- `JWT_ALGORITHM` (по умолчанию `HS256`; допустимы только симметричные
  HMAC-варианты — `HS256`/`HS384`/`HS512`).
- `ACCESS_TOKEN_EXPIRE_MINUTES` (по умолчанию `30`).

Registry свои учетные данные из `.env` не берет — см. "Почему так".

Проверить конфигурацию без запуска контейнеров:

```bash
docker compose --env-file .env.example config
docker compose --env-file .env.example --profile admin config
```

## Локальная разработка и тесты

### Полный локальный стек (Docker + Vite dev) — быстрый старт с чистого клона

`docker-compose.yml` рассчитан на production VPS: `nginx` ждёт реальный
Let's Encrypt сертификат из `/etc/letsencrypt`, собранный `frontend/dist` и
`registry/auth/htpasswd`, которых на чистом клоне нет и быть не может. Для
локальной разработки используется отдельный override-файл
[docker-compose.local.yml](docker-compose.local.yml), который НЕ меняет
`docker-compose.yml` (production/CI-поведение `docker compose up -d` без
этого override — то же самое, что и раньше), а только:

- гасит `nginx`/`registry` профилем `production`, который здесь никто не
  активирует, — они не создаются вообще, и их VPS-only volume-мounты
  (`/etc/letsencrypt`, `registry/auth`, `frontend/dist`) не трогаются;
- публикует `backend` на `127.0.0.1:8000` (только loopback, никогда
  `0.0.0.0` — см. "UFW — не единственный механизм защиты" выше), чтобы к
  нему мог обратиться Vite dev-сервер, запущенный на хосте.

Сам frontend в Docker не заворачивается: `frontend/vite.config.ts` уже
содержит dev-only proxy `/api` -> `http://127.0.0.1:8000`, так что `npm run
dev` на хосте обращается к контейнерному backend напрямую, тем же
контрактом `/api/*`, что и production.

**1. Предварительные требования:** Docker Engine + Compose plugin,
Node.js 22. Production TLS-сертификаты, домены, Registry-креденшлы и
`registry/auth/htpasswd` не нужны и не используются.

**2. Настройка окружения:**

```bash
cp .env.example .env
# Обязательно замените JWT_SECRET_KEY на реальный секрет — плейсхолдер
# из .env.example намеренно отклоняется валидацией Settings при старте
# backend (см. "Обязательные переменные окружения" выше):
python -c "import secrets; print(secrets.token_urlsafe(64))"
# Остальные значения (пароли ролей PostgreSQL, PGADMIN_*) можно оставить
# как есть — это не production-секреты, а креденшлы одноразового локального
# контейнера.
```

**3. База данных + backend:**

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d
```

Поднимает ровно ту же цепочку, что и в production (см. "Provisioning базы
данных" в [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)): `postgres` (healthy)
-> `db-roles-bootstrap` -> `db-migrate` -> `db-roles-finalize` -> `backend`
(healthy). `nginx`/`registry` не создаются вообще (гашены override'ом
выше); `pgadmin` по-прежнему опционален через `--profile admin`, если он
нужен и локально.

**4. Frontend:**

```bash
cd frontend
npm ci
npm run dev
```

**5. URL'ы:** frontend — `http://localhost:5173`; backend напрямую —
`http://127.0.0.1:8000` (включая `/docs`/`/redoc` — локально их никто не
блокирует, в отличие от production-периметра Nginx, см. "API и публичный
security allowlist" выше).

**6. Проверка готовности:**

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml ps --all
curl http://127.0.0.1:8000/api/health
curl http://localhost:5173/api/admin-settings/active   # публичный эндпоинт через Vite proxy
```

Первая команда должна показать `backend`/`postgres` — `healthy`, три
one-shot lifecycle-сервиса — `Exited (0)` (см. "Health / readiness" в
DEPLOYMENT.md за тем, почему это ожидаемо, а не сбой). Первого
администратора можно создать так же, как на VPS: `docker compose -f
docker-compose.yml -f docker-compose.local.yml exec -it backend python -m
app.cli bootstrap-admin`.

**7. Остановка/очистка:**

```bash
# Ctrl+C у npm run dev, затем:
docker compose -f docker-compose.yml -f docker-compose.local.yml down -v
```

(`-v` также удаляет volume `postgres-data` — для полностью чистого
следующего старта; без `-v` данные между запусками сохраняются.)

**8. Что осознанно НЕ используется локально:** production Nginx (TLS,
security-заголовки, allowlist, SPA-раздача `frontend/dist`) — Vite dev
сам раздаёт frontend и проксирует `/api`; Docker Registry — образ backend
собирается локально (`build: ./backend`), а не тянется из приватного
registry; pgAdmin — опционален, не требуется для базового сценария;
реальные TLS-сертификаты, домены и htpasswd — не существуют локально и не
нужны.

Этот путь эмпирически проверен с чистого клона (Docker-стек поднят,
`GET /api/health`/`GET /api/admin-settings/active` отвечали через
контейнерный backend напрямую и через Vite-прокси на `:5173`).

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

Итог прогона против отдельной реальной PostgreSQL test-базы (включая auth,
admin CRUD, приоритизацию заявок с поиском/фильтром по приоритету, и
analytics), на техническом baseline `28932b1` (2026-09-05; родительский
коммит — `95299dc`): **650 passed, 0 skipped**. Это результат конкретного прогона на
конкретном состоянии кода, не гарантия на будущее — число меняется вместе с
кодом, актуальное значение всегда можно получить самостоятельным прогоном
выше.

### Frontend

```bash
cd frontend
npm ci
npm test
npm run build
npm audit
```

Итог: **469 tests passed**, `npm run build` — успешно, `npm audit` (без
флагов, все severity) — **0 vulnerabilities** на момент этой проверки.
Это результат конкретного прогона, не гарантия на будущее и не то же
самое, что порог, который реально enforced в CI — см. "CI" ниже. Покрытие
тестами (coverage) не измерялось — количество тестов не эквивалентно
проценту покрытия кода.

## CI

GitHub Actions (`.github/workflows/ci.yml`), три независимых job'а на
каждый push/PR в `main`:

- **backend-tests** — полный `pytest` против реального `postgres:16.15-alpine`
  service-контейнера (не mock — те же интеграционные тесты, что и
  локально, включая Alembic fresh-install/legacy-upgrade/drift и реальный
  `pg_dump`/`pg_restore` round-trip), зависимости ставятся из
  hash-verified lock-файла тем же способом, что и `backend/Dockerfile`.
- **frontend** — `npm ci` → `vitest` → `tsc --noEmit` → `npm run build` →
  `npm audit --audit-level=moderate` (реальная политика: сборка падает на
  находках severity `moderate` и выше; `low`/`info` не блокируют CI — это
  принятый практический порог, не буквальный "ноль уязвимостей любой
  критичности" — прогон выше просто зафиксировал, что на тот момент их не
  было ни одной, а не то, что low-severity находки где-то отдельно
  запрещены политикой).
- **containers** — реальная сборка `backend/Dockerfile` и валидация
  `docker-compose.yml` (`docker compose config`, включая профиль `admin`)
  тем же способом, что описан в разделе "Обязательные переменные
  окружения" выше.

CI не разворачивает production-инфраструктуру и не требует секретов —
service-контейнер PostgreSQL одноразовый, его учётные данные фиктивны и
уничтожаются вместе с job'ом.

## Production build frontend

`frontend/dist` собирается **вне VPS** — на отдельной build-машине или
временным официальным Node Docker-контейнером там же, например:

```bash
npm run build
# или, без локального Node/npm, тем же принципом на build-машине:
docker run --rm -v "$PWD/frontend:/app" -w /app node:22-slim sh -c "npm ci && npm run build"
```

(Точная команда может отличаться деталями — важен принцип: сборка Node-
инструментами происходит на build-машине, а не на самом VPS — на VPS с
~1GB RAM сборка не рекомендуется, см. "Resource protection" ниже; сам
образ `nginx:alpine`, работающий на VPS, Node/npm не содержит.)

Результат сборки передается на VPS как **версионированный artifact с
контрольной суммой**, а не заново собирается на месте:

```bash
tar -czf "frontend-dist-$(git rev-parse --short HEAD).tar.gz" -C frontend dist
sha256sum "frontend-dist-$(git rev-parse --short HEAD).tar.gz" \
  > "frontend-dist-$(git rev-parse --short HEAD).tar.gz.sha256"
scp "frontend-dist-$(git rev-parse --short HEAD).tar.gz"* vibe-vps:/tmp/
```

На VPS перед разворачиванием контрольная сумма перепроверяется
(`sha256sum -c ...sha256`) — распаковка происходит только после успешной
проверки. Публикация выполняется в два шага, чтобы не было окна, когда
`index.html` уже ссылается на хэшированные assets, которых еще нет на
диске:

1. Новые хэшированные файлы из `frontend/dist/assets/` копируются в
   веб-корень (`./frontend/dist/assets/` на хосте) **без удаления** старых
   файлов — старые хэшированные assets остаются доступны, пока на них
   могут ссылаться уже загруженные в браузерах старые `index.html`.
2. Только после этого `index.html` заменяется **атомарно** (`mv` внутри
   одной файловой системы — атомарная операция на POSIX), когда все новые
   assets уже на месте.

Устаревшие хэшированные assets, на которые больше не ссылается ни один
живой `index.html`, можно убрать best-effort уборкой не раньше следующего
релиза — не автоматически и не в момент самого деплоя.

Nginx раздает `frontend/dist` через read-only bind mount
(`./frontend/dist:/usr/share/nginx/html:ro`, см. "Docker Compose" выше).

## Порядок деплоя

### Первый деплой (bootstrap)

Основные шаги этого порядка (создание `.env`, `registry/create-user.sh`,
сборка/доставка release-образа, первый `docker compose up -d --no-build`)
были пройдены на этом VPS при самом первом деплое, предшествовавшем
задокументированной VPS-приемке на commit `7547704` (см. "Статус проекта"
выше). Шаг 9 ниже в текущем виде репозитория дополнительно поднимает
цепочку Stage 2 database lifecycle (`db-roles-bootstrap` → `db-migrate` →
`db-roles-finalize`), появившуюся в репозитории позже `7547704` — из
содержимого репозитория не следует, выполнялась ли именно эта цепочка на
текущем VPS; она проверена локально/в CI (см. "Локальная разработка и
тесты"/"CI"). Процедура ниже в целом остается здесь как инструкция для
повторного/дополнительного bootstrap. Registry **не считается готовым к
запуску** без файла `registry/auth/htpasswd` — до его появления контейнер
стартует, но любой запрос к Registry будет отклонен на этапе аутентификации
(это ожидаемо и правильно, а не баг).

**Почему здесь не просто `docker compose up -d`.** `backend` (и три
one-shot DB-lifecycle сервиса, ссылающиеся на тот же тег — см. "Docker
Compose" выше) объявляют `build: ./backend`. Если тег
`vibe-order-infra-backend:latest` еще не существует локально на VPS (а на
по-настоящему первом деплое его там нет), голый `docker compose up -d`
собрал бы образ **прямо на VPS** — противоречит задокументированной
production-модели ("образ собирается вне VPS", см. "Docker Compose" выше)
и недетерминированно (два first-deploy запуска, разделенные любым
изменением в `backend/`, дали бы два разных образа без единого
проверяемого тега). Поэтому первый деплой явно доставляет на VPS
конкретный, заранее собранный образ и запускает Compose с `--no-build`, не
полагаясь на build-fallback:

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
5. **Собрать release image вне VPS**, immutable tag, сразу под тем же
   именем, что использует Registry (тот же принцип, что и "Обновление /
   повторный деплой" ниже) — `--load` гарантирует, что образ реально
   попадёт в локальный Docker этой build-машины, а не только в кэш
   builder'а, что нужно для следующего шага (`docker save`):
   ```bash
   docker buildx build --platform linux/amd64 --load \
     -t registry-vibe.elivcloud.org/vibe-order-infra/backend:<immutable-tag> \
     ./backend
   docker image inspect registry-vibe.elivcloud.org/vibe-order-infra/backend:<immutable-tag> \
     --format '{{.Id}}'   # записать это значение - понадобится на шаге 8
   ```
6. **Доставить этот образ на VPS.** `docker pull` из
   `registry-vibe.elivcloud.org` здесь еще не вариант: Registry снаружи
   достижим только через Nginx по HTTPS, а Nginx сам объявляет
   `depends_on: backend: condition: service_healthy` (см. "Docker Compose"
   выше и `docker-compose.yml`) — на VPS еще нет ни одного контейнера
   `backend`, значит Nginx еще не поднимется, значит Registry еще
   недостижим снаружи. Вместо `pull` используется тот же принцип
   "versioned artifact + sha256", что и для `frontend/dist` (см.
   "Production build frontend" выше):
   ```bash
   docker save registry-vibe.elivcloud.org/vibe-order-infra/backend:<immutable-tag> | gzip \
     > "backend-<immutable-tag>.tar.gz"
   sha256sum "backend-<immutable-tag>.tar.gz" \
     > "backend-<immutable-tag>.tar.gz.sha256"
   scp "backend-<immutable-tag>.tar.gz"* vibe-vps:/tmp/
   ```
7. **На VPS: проверить контрольную сумму и только потом загрузить образ**
   — тот же принцип "без окна недоверенного состояния", что и у
   frontend-доставки:
   ```bash
   cd /tmp
   sha256sum -c "backend-<immutable-tag>.tar.gz.sha256"
   docker load < "backend-<immutable-tag>.tar.gz"
   ```
8. **Валидировать идентичность загруженного образа** (не доверять голому
   факту, что `docker load` не упал с ошибкой) и только потом переключить
   на него локальный тег, который читает Compose-декларация:
   ```bash
   docker image inspect registry-vibe.elivcloud.org/vibe-order-infra/backend:<immutable-tag> \
     --format '{{.Id}}'
   # сверить с Image Id, записанным на build-машине на шаге 5 - должны совпадать
   docker tag registry-vibe.elivcloud.org/vibe-order-infra/backend:<immutable-tag> \
     vibe-order-infra-backend:latest
   ```
9. **Только теперь** `docker compose up -d --no-build` (без профиля
   `admin`) — `--no-build` запрещает Compose собирать образ на VPS, даже
   если бы локальный тег `vibe-order-infra-backend:latest` оказался по
   какой-то причине отсутствующим (команда откажет явно вместо тихого
   локального build); при наличии тега (шаг 8 уже его создал) это
   автоматически поднимает всю цепочку database lifecycle в правильном
   порядке (`db-roles-bootstrap` → `db-migrate` → `db-roles-finalize` →
   `backend`, через `depends_on: condition: service_completed_successfully`/
   `service_healthy`), без ручных дополнительных шагов — подробнее и про
   усыновление уже существующей (legacy) production-базы см.
   [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), раздел "Provisioning базы
   данных".
10. **(День 2, опционально, но рекомендуется).** Как только Nginx и
    Registry реально подняты, здоровы и TLS выпущен (см. следующий пункт
    ниже и "Registry — подтверждено полным push/pull smoke-test'ом" в
    "Почему так"), запушить тот же самый immutable tag в Registry:
    ```bash
    docker push registry-vibe.elivcloud.org/vibe-order-infra/backend:<immutable-tag>
    ```
    Тег уже собран под правильным именем на шаге 5 — дополнительный
    `docker tag` не нужен. Это делает образ доступным для будущего
    `docker pull` (например, для восстановления на другом VPS) тем же
    путем, что и все последующие релизы (см. "Обновление / повторный
    деплой" ниже), без повторного `docker save`/`scp`.

Выпуск TLS-сертификатов и включение HTTPS — отдельный шаг после этого
порядка (certbot на VPS, затем перезапуск Nginx); на текущем VPS уже
выполнен.

### Обновление / повторный деплой (текущий безопасный flow, применим к каждому будущему релизу)

Этот flow — не одноразовая процедура «добавить таблицу `admins`», а
повторяемая процедура для **любого** будущего обновления backend/frontend.
Backup PostgreSQL перед обновлением обязателен только тогда, когда релиз
меняет схему БД (новая Alembic-ревизия в `backend/alembic/versions/` — см.
"Ограничение: развертывание базы данных" ниже); для чисто frontend-релиза
или backend-релиза без изменений схемы этот шаг не обязателен, но не будет
лишним.

**Backend (собран вне VPS, доставлен через private Registry):**

1. Собрать release image **вне VPS**, для целевой архитектуры VPS
   (`linux/amd64`), с immutable tag (например, git SHA или semver — не
   `:latest`):
   ```bash
   docker buildx build --platform linux/amd64 \
     -t registry-vibe.elivcloud.org/vibe-order-infra/backend:<immutable-tag> \
     ./backend
   ```
2. Запушить image в private Registry:
   ```bash
   docker push registry-vibe.elivcloud.org/vibe-order-infra/backend:<immutable-tag>
   ```
3. Если релиз меняет схему БД — backup PostgreSQL, проверить файл непустым
   (см. "Ограничение: развертывание базы данных" ниже), не продолжать без
   этого подтверждения.
4. На VPS: `git pull`/`git fetch` — обновить репозиторий (docs/compose/nginx
   config), и `docker compose config --quiet` — убедиться, что `.env`
   полон и конфигурация валидна, до запуска чего-либо.
5. На VPS: `docker pull registry-vibe.elivcloud.org/vibe-order-infra/backend:<immutable-tag>`
   — скачать готовый image, **без** `docker compose build`.
6. Сохранить текущий работающий image под rollback-тегом (например,
   `docker tag <текущий backend image> vibe-order-infra-backend:rollback-<дата>`)
   — до пересоздания контейнера, чтобы откат был мгновенным (`docker tag` +
   `docker compose up -d --no-build --no-deps backend`) без повторного
   pull/build.
7. Переключить локальный тег, который ожидает Compose-декларация
   (`build: ./backend` → образ `vibe-order-infra-backend`), на только что
   запушенный/выкачанный release image (`docker tag
   registry-vibe.elivcloud.org/vibe-order-infra/backend:<immutable-tag>
   vibe-order-infra-backend:latest`), чтобы `docker compose up -d --no-build
   --no-deps backend` использовал его, а не запускал build.
8. **Применить миграции ДО пересоздания backend** — новый образ несёт
   новые файлы миграций (`backend/alembic/versions/`), но сам их не
   применяет: старт backend — это только read-only проверка схемы
   (`app/core/schema_check.py`), которая **откажет в старте**, если схема
   ещё не на той ревизии, которую ожидает новый код. Тот же локальный тег
   `vibe-order-infra-backend:latest`, что и у backend, используют и три
   one-shot DB-lifecycle сервиса — таргетируем последний в цепочке, Compose
   поднимает зависимости (`db-roles-bootstrap` → `db-migrate`) сам:
   ```bash
   docker compose up -d --no-build db-roles-finalize
   docker compose ps --all   # db-roles-bootstrap/db-migrate/db-roles-finalize — Exited (0);
                              # обычный "ps" без --all скрывает остановленные
                              # one-shot контейнеры вместо того, чтобы показать
                              # их "Exited (0)"
   ```
   Безопасно и дёшево выполнять этот шаг при каждом релизе, даже если он не
   меняет схему: `bootstrap_roles.py` идемпотентен, а `alembic upgrade head`
   на уже актуальной схеме — no-op (см. `backend/tests/test_migrations_fresh_install.py`).
9. Пересоздать **только** backend:
   ```bash
   docker compose up -d --no-build --no-deps backend
   ```
   `--no-build` запрещает Compose собирать образ на VPS даже при наличии
   `build: ./backend` в декларации — используется только уже загруженный
   через `docker pull`/`docker tag` image. `--no-deps` не поднимает и не
   затрагивает зависимости backend (`postgres` из `depends_on`) — Compose
   не проверяет и не трогает healthcheck `postgres`, а просто пересоздает
   контейнер backend на уже работающей БД (и уже применённой миграции —
   см. шаг 8). Пересоздается только backend; остальные сервисы — `nginx`,
   `postgres`, `registry`, `pgadmin` — не перезапускаются этим шагом.

**Frontend (собран вне VPS, см. "Production build frontend" выше):**

10. Собрать `frontend/dist` на build-машине, передать на VPS как
    версионированный artifact с SHA-256 контрольной суммой, опубликовать
    hashed assets без удаления старых, затем атомарно заменить `index.html`
    — полная процедура и обоснование порядка — см. "Production build
    frontend" выше.

**Nginx (только если конфигурация меняется в этом релизе):**

11. `nginx -t` **обязательно ДО** применения новой конфигурации — не после
    и не одновременно с ней.
12. Если `nginx -t` прошел — применить **только graceful reload**, без
    recreate контейнера: `docker compose exec nginx nginx -s reload` (или
    `docker compose kill -s HUP nginx`). Пересоздание/restart контейнера
    Nginx для смены конфигурации не требуется и не выполняется.
13. Если `nginx -t` НЕ прошел — не применять reload, откатить
    `nginx/conf.d/*.conf` до валидного состояния.

**После обновления любого компонента:**

14. Smoke tests — актуальная процедура приёмки живёт в
    [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), раздел "Текущий чеклист
    приёмки после деплоя" (публикация `/admin`, protected API без токена →
    `401`, `/docs`/`/openapi.json`/`/redoc` снаружи недоступны и т.д.).
    Исторический чеклист ниже в этом README ("Historical production smoke
    checklist") зафиксирован на commit `7547704` и для сегодняшнего кода
    не актуален (в частности, ссылается на `POST /api/auth/register`,
    которого в текущем коде больше нет).
15. `docker compose ps` — все сервисы в статусе `running`/`healthy`.
16. `docker stats --no-stream` — сверить фактическое потребление
    памяти/CPU с лимитами (см. "Resource protection").

`docker compose down` не используется как часть стандартного flow
обновления — он остановил бы все сервисы разом вместо контролируемого
поочередного обновления. Сборка (`docker compose build` / `npm run build`
напрямую на хосте) на самом VPS не рекомендуется в принципе — на VPS с
RAM около 1 GB build-процесс (особенно TypeScript/Vite или Python wheel
compilation) конкурирует за память с работающими сервисами и рискует
уронить их по OOM.

## Ограничение: развертывание базы данных

Схема управляется Alembic-миграциями (`backend/alembic/versions/`) через
трёхролевую модель прав (кластерный admin / migration-owner / runtime
app) — полная процедура, включая свежую установку и усыновление legacy-базы,
описана в [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), раздел "Provisioning
базы данных"; операционные инциденты (неудачная миграция, restore) —
[docs/RUNBOOK.md](docs/RUNBOOK.md). Здесь — только backup-процедура,
обязательная **перед каждым обновлением production, меняющим схему БД**
(это правило действует для любого будущего релиза со схемными изменениями,
а не только для исторически первого).

Backup — вне репозитория, с конкретным именем файла (без wildcard), без
раскрытия credentials в самой команде (значения читаются из переменных
окружения контейнера `postgres` через `$POSTGRES_USER`/`$POSTGRES_DB`
внутри контейнера, не подставляются в текст команды на хосте):

```bash
backup_dir="$HOME/vibe-order-infra-backups"
install -d -m 700 "$backup_dir"

backup_file="$backup_dir/backup-before-<release-tag>-$(date +%Y%m%d-%H%M%S).sql"

# --clean --if-exists - нужно для restore-модели в RUNBOOK.md ("Restore
# PostgreSQL": восстановление в заведомо чистую, только что пересозданную
# базу - НЕ прямо поверх текущей, см. этот раздел про то, почему).
#
# Оба условия обязательны: pg_dump exit 0 И $backup_file непустой (shell-
# редирект ">" обнуляет $backup_file ДО запуска pg_dump - упавший НЕ сразу
# pg_dump уже успевает записать обрезанный, но непустой файл; "test -s" в
# одиночку принял бы такой файл как валидный). Форма "if pg_dump ...; then"
# (а не "cmd; status=$?") обязательна для безопасности под "set -e" - если
# вызывающий deploy-скрипт уже включил "set -e", команда-условие "if" не
# триггерит errexit при падении, а вот отдельная строка "status=$?" после
# упавшей команды никогда не выполнилась бы, оставив обрезанный дамп под
# нормальным именем backup'а и без карантина (см. docs/RUNBOOK.md, "Backup
# PostgreSQL" - там же эмпирическая проверка под обоими режимами). Никакого
# "|| true" - падение здесь обязано реально останавливать деплой.
if docker compose exec -T postgres sh -c \
     'pg_dump --clean --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB"' \
     > "$backup_file"
then
  if [ -s "$backup_file" ]; then
    ls -lh "$backup_file"
  else
    echo "backup FAILED (pg_dump exited 0 but wrote no data) - do not proceed" >&2
    mv "$backup_file" "$backup_file.incomplete" 2>/dev/null || rm -f "$backup_file"
    exit 1
  fi
else
  dump_status=$?
  echo "backup FAILED (pg_dump exit=$dump_status) - do not proceed" >&2
  mv "$backup_file" "$backup_file.incomplete" 2>/dev/null || rm -f "$backup_file"
  exit 1
fi
```

`$backup_file` — единственная переменная, указывающая на конкретный,
только что созданный файл: последующая проверка относится именно к нему, а
не к произвольному файлу, попавшему под маску. Каталог
`$HOME/vibe-order-infra-backups` — вне рабочей копии репозитория (не
коммитится и не может быть случайно закоммичен), права `700` ограничивают
доступ к бэкапам (внутри — дамп БД, потенциально чувствительные данные
заявок) только владельцу.

**Если код возврата `pg_dump` ненулевой ИЛИ `$backup_file` пуст — НЕ
продолжать деплой** (см. код выше — это уже enforced, не только
рекомендация). Это жёсткий стоп-критерий, не рекомендация: без
подтверждённого непустого backup откатываться в случае проблемы с
обновлением будет нечем (Alembic-миграции в этом репозитории не имеют
проверенных `downgrade()`-путей — откат схемы назад не поддерживается
инструментарием, только restore из backup). Сам процесс НЕ включает
автоматический rollback — полная процедура restore, включая зависимость от
того, какой ревизии Alembic соответствует backup, —
[docs/RUNBOOK.md](docs/RUNBOOK.md), раздел "Restore PostgreSQL".

## First production admin

**Начиная с этого релиза (Stage 1A remediation "unsafe first-admin
bootstrap") публичной HTTP-регистрации администратора больше не
существует.** `POST /api/auth/register` удален из кода целиком — это не
"закрывается после первой регистрации", как было раньше, а отсутствует как
маршрут вообще, на пустой БД в том числе. `GET /api/auth/check` возвращает
только `{"admin_exists": true|false}` — поля `registration_allowed` в
ответе больше нет, ориентироваться на него в интеграциях/скриптах больше
нельзя.

Первый администратор на новой/пустой БД создается только оператором,
локально на сервере, командой CLI — **до** того, как сервис становится
публично доступным:

```bash
docker compose exec -it backend python -m app.cli bootstrap-admin
```

Команда интерактивно запрашивает имя пользователя и пароль (ввод пароля
скрыт, нигде не печатается и не логируется — включая случай непредвиденной
ошибки БД во время bootstrap, см. `backend/tests/test_cli_bootstrap_admin.py`).
Она использует ту же валидацию, Argon2id-хеширование и concurrency-защиту
(Postgres advisory lock), что и остальное приложение, и отказывает с
ненулевым exit code, если администратор уже существует — повторный запуск
безопасен. Пока bootstrap не выполнен, `/admin` показывает обычную форму
входа с поясняющим текстом ("Администратор ещё не настроен…"); войти
некем, пока команда не отработает успешно.

Восстановленная из backup БД (или любая БД, где администратор уже есть)
**bootstrap не требует**: команда выше для неё не нужна и, если всё же
запущена, корректно откажет ("администратор уже существует"); вход
выполняется как обычно, через `POST /api/auth/login`.

Историческая справка: на текущем production VPS первый администратор был
создан **до** этого релиза, через публичную форму регистрации, которая
существовала в коде на тот момент (см. "Ручная end-to-end приемка" ниже —
запись того прогона намеренно не переписана, это исторический лог). Этот
администратор продолжает работать без изменений — повторный bootstrap для
него не требуется. Публичная регистрация в текущем коде отсутствует
независимо от истории.

Реальные production username/password в README не публикуются.

## Ручная end-to-end приемка

### Финальная production-приемка (по состоянию на commit `7547704`)

Это последняя задокументированная в этом README ручная приемка на
production VPS. Репозиторий с тех пор продвинулся дальше (см. "Статус
проекта" выше и "Дальше по плану" ниже) — эта запись не переписывается
под более новый HEAD, она фиксирует то, что было реально проверено на
VPS на момент commit `7547704`.

Выполнена вручную через публичный домен `https://vibe.elivcloud.org` после
деплоя административной панели, JWT-аутентификации, приоритизации заявок и
поведенческой аналитики:

- Регистрация первого администратора (`POST /api/auth/register`) —
  успешна; endpoint регистрации закрылся сразу после нее — в production
  это подтверждено через `GET /api/auth/check` →
  `admin_exists: true`, `registration_allowed: false` (повторный
  `POST /api/auth/register` вручную в production не выполнялся; что он
  возвращает `409 Conflict` — подтверждено automated backend test suite,
  а не ручным вызовом на проде, см. "First production admin" выше).
- Повторные login/logout проверены: logout очищает JWT из
  `sessionStorage`, повторный login снова открывает панель.
- Публичный frontend доступен по HTTPS; `/admin` (административная панель)
  доступна по HTTPS.
- CRUD услуг доступен авторизованному администратору.
- Список заявок, поиск, фильтры по приоритету и detail modal с карточкой
  заявки работают.
- Explainable scoring отображает приоритеты **Высокий** / **Средний** /
  **Стандартный** с причинами и рекомендациями (recommended
  action/team/personal manager).
- Аналитика проверена за все три периода: **24 часа**, **7 дней**,
  **30 дней**.
- Проверены оба состояния карточки заявки: "поведенческие метрики есть" и
  "поведенческие метрики отсутствуют".
- Полный E2E-сценарий подтвержден: публичный frontend → API → PostgreSQL →
  scoring → behavior metrics → отображение в admin panel.
- Закрытые технические endpoints подтверждены: `/docs`, `/redoc`,
  `/openapi.json` → `404`; неизвестные `/api/*` → `404` от Nginx.
- Registry (`GET /v2/` без credentials) → `401` с Basic auth challenge.
- Итоговое состояние базы данных production после приемки: `admins` = 1,
  `applications` = 12, `behavior_metrics` = 10.

Реальные production username/password/email/JWT в README не публикуются.

### Исторический прогон (до admin-панели, JWT-auth и analytics)

Функциональный сценарий, пройденный вручную end-to-end на VPS на более
раннем этапе проекта — до появления административной панели, JWT-auth и
analytics:

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

Security smoke-tests (выполнены снаружи, через публичный домен, на ТОМ этапе,
т.е. до этой ветки — тогда `/admin` и весь admin API были жестко закрыты
Nginx, т.к. серверной auth еще не было):

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

Эта историческая запись **больше не описывает текущую Nginx-конфигурацию**:
начиная с деплоя административной панели и JWT-аутентификации,
задокументированного в рамках VPS-приемки на commit `7547704` (см.
"Финальная production-приемка" выше), `/admin` и admin API публикуются
(защищены JWT на уровне backend, а не 404 в Nginx) — это же верно и для
текущей конфигурации репозитория. См. "API и публичный security allowlist"
выше и "Финальная production-приемка" выше для актуального smoke-test этой
функциональности на VPS.

## Historical production smoke checklist (commit `7547704`)

**Этот чеклист — историческое свидетельство, не процедура для сегодняшнего
деплоя.** Актуальный чеклист приёмки для текущего репозитория —
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), раздел "Текущий чеклист приёмки
после деплоя"; в частности, пункт про регистрацию первого администратора
ниже ссылается на `POST /api/auth/register`, которого в текущем коде
больше нет (см. "First production admin" выше) — использовать этот пункт
как сегодняшнюю инструкцию нельзя.

Чеклист использован для деплоя и приемки релиза, зафиксированного как
commit `7547704` — последняя задокументированная ручная VPS-приемка (см.
"Статус проекта" выше). Все пункты ниже подтверждены на VPS по состоянию
на тот момент (см. также "Финальная production-приемка" выше) — это не
чеклист для текущего состояния репозитория (технический baseline
`28932b1`), которое продвинулось дальше без отдельной повторной ручной
VPS-приемки:

Перед деплоем:

- [x] `docker compose config` (и `--profile admin config`) проходит без
      ошибок с реальным `.env` на VPS.
- [x] Текущая конфигурация Nginx на VPS активна и обслуживает трафик
      корректно (`nginx` в статусе `healthy`, HTTPS/allowlist/blocked-paths
      работают штатно — см. HTTPS smoke checks выше).
- [x] Backup PostgreSQL перед обновлением создан и подтвержден непустым
      (файл в `$HOME/vibe-order-infra-backups/`, размер > 0) — см.
      "Ограничение: развертывание базы данных".

Функциональные проверки после деплоя:

- [x] `/` открывается по HTTPS (`200`).
- [x] `/admin` открывается и работает при прямом refresh страницы (`200`,
      без 404).
- [x] Административная авторизация — полный цикл:
      - [x] регистрация первого администратора (`POST /api/auth/register`)
        прошла успешно;
      - [x] закрытие регистрации первого admin подтверждено в production
        через `GET /api/auth/check` → `registration_allowed: false` (без
        повторного ручного вызова `POST /api/auth/register` на проде); что
        сам повторный вызов вернул бы `409 Conflict`, а не повторное
        создание, — часть backend-контракта, подтвержденная automated test
        suite, а не production smoke-test'ом;
      - [x] logout (кнопка "Выйти" в `/admin`) очищает JWT из
        `sessionStorage` и возвращает UI на экран входа;
      - [x] повторный login (`POST /api/auth/login`) после logout снова
        работает и восстанавливает доступ к панели.
- [x] Публичная форма заявки (`POST /api/applications`) и behavior metrics
      (`POST /api/behavior-metrics`) работают без токена.
- [x] Protected API без токена возвращает `401`, а не 200/404 (подтверждено
      логами backend — например, `GET /api/analytics/applications/{id}`
      без токена → `401`).
- [x] CRUD услуг (`/api/admin-settings`) работает из `/admin` под валидным
      токеном.
- [x] Заявки: список/поиск/фильтр по приоритету и приоритизация
      (`GET /api/applications/prioritized`, scoring reasons) отображаются в
      `/admin`.
- [x] Статистика за день/неделю/месяц (`GET /api/analytics/overview?period=`)
      отображается во вкладке "Статистика" (24 часа / 7 дней / 30 дней).
- [x] Detail-аналитика заявки, у которой ЕСТЬ behavior metrics
      (`GET /api/analytics/applications/{application_id}`), отображает
      реальные агрегаты.
- [x] Detail-аналитика заявки, у которой НЕТ behavior metrics, отображает
      пустое состояние, а не ошибку.
- [x] `/docs`, `/redoc`, `/openapi.json` снаружи недоступны (`404`);
      неизвестные `/api/*` тоже возвращают `404`.
- [x] Registry (`registry-vibe.elivcloud.org`) требует аутентификацию —
      запрос к `/v2/` без креденшлов возвращает `401` с Basic auth challenge.
- [x] pgAdmin доступен только через `127.0.0.1:5050` на самом VPS — не
      слушает внешний интерфейс, доступ с локальной машины только через
      SSH-туннель (см. "pgAdmin" в разделе "Почему так").
- [x] Backend (`:8000`) и PostgreSQL (`:5432`) не публикуют портов на host
      наружу (подтверждено `docker compose ps` на VPS — нет записей в
      колонке `PORTS`).

Здоровье инфраструктуры после запуска:

- [x] `docker compose ps` — все сервисы в статусе `running`/`healthy`,
      `RestartCount=0`.
- [x] Свежие логи `backend` и `nginx` не содержат неожиданных
      ошибок/трейсбэков.

## Скриншоты / результаты

Файлы скриншотов в репозитории отсутствуют. Сценарии, пройденные вручную и
подтвержденные (без файлов скриншотов в git):

Предыдущий этап (до admin-панели/auth/analytics):

- Swagger: `POST /api/admin-settings` (создание услуги).
- Swagger: `GET /api/admin-settings/active` (список активных услуг).
- PostgreSQL: содержимое таблицы `admin_settings`.
- Frontend: главная страница (home).
- Frontend: выбор услуги и бюджета.
- Frontend: успешная отправка заявки.
- PostgreSQL: содержимое таблицы `applications`.
- PostgreSQL: содержимое таблицы `behavior_metrics`.

Релиз с admin-панелью, JWT-auth, приоритизацией заявок и analytics
(зафиксирован как commit `7547704` — последняя задокументированная ручная
VPS-приемка, см. "Статус проекта" выше) — см. "Финальная
production-приемка" выше для полного списка проверенных сценариев
(регистрация первого администратора, login/logout, CRUD услуг,
список/приоритизация заявок, статистика 24 часа/7 дней/30 дней,
detail-метрики заявки с состояниями "есть"/"отсутствуют").

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

**Watchtower удалён (Stage 3).** Ранее в составе инфраструктуры присутствовал
Watchtower (`nickfedor/watchtower`) в label-based opt-in режиме — на практике
ни один сервис так и не был включен в автообновление (все label
`com.centurylinklabs.watchtower.enable` стояли в `"false"`), а сам контейнер
требовал bind-mount `/var/run/docker.sock` — фактически root-доступ к хосту
через Docker API (флаг `:ro` на монтировании ограничивает только замену
самого файла сокета, не вызовы API через него). Stage 3 убрал Watchtower и
этот docker.sock-mount из репозитория целиком: обновления образов теперь
только явные, ручные — выбор immutable release, `docker pull` конкретного
тега, явный `docker tag` на локальный alias `vibe-order-infra-backend:latest`
и `docker compose up -d --no-build` (полная процедура — "Порядок деплоя" →
"Обновление / повторный деплой" выше), без постоянно работающего
привилегированного контейнера, слушающего Docker API.

**Backend healthcheck и readiness (Stage 3).** У backend теперь есть
Docker `HEALTHCHECK` (см. `backend/Dockerfile`, `backend/healthcheck.py`) —
он вызывает `GET /api/ready` изнутри контейнера через stdlib `urllib` (без
добавления curl/wget в образ). `/api/ready` (`backend/app/main.py`)
переиспользует read-only schema-check из Stage 2
(`app/core/schema_check.py::ensure_database_ready`) — проверяет, что БД
доступна, роль рабочая и подключенная схема совпадает с ожидаемой, без
DDL/мутаций. `/api/health` остается чистой liveness-проверкой (без
обращения к БД). `/api/ready` НЕ проксируется наружу через Nginx (только
`/api/health` — см. `nginx/conf.d/vibe.elivcloud.org.conf`) — используется
только Docker healthcheck'ом и локально, изнутри `app-net`/контейнера.

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
плейсхолдер (реальные сертификаты — из `/etc/letsencrypt` на хосте). Всё
перечисленное выше про сам TLS/сертификат подтверждено на VPS-приемке
`7547704` (см. "Статус проекта" выше). HSTS — более позднее добавление в
текущем репозитории (Stage 4, добавлен после `7547704`; отдельная ручная
VPS-приемка этого изменения в README не задокументирована) и в текущей
конфигурации включен на HTTPS-ответах основного сайта — см. "Security
notes / ограничения" ниже за точной конфигурацией заголовка и
обоснованием значений `max-age`/`includeSubDomains`/`preload`.

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
пяти сервисов: postgres, backend, nginx, registry, pgadmin.

| Сервис     | memory limit | memory reservation | cpus |
|------------|-------------:|--------------------:|-----:|
| postgres   | 384M | 192M | 1.00 |
| pgadmin    | 256M | 128M | 0.50 |
| backend    | 192M | 128M | 0.50 |
| registry   | 192M |  64M | 0.50 |
| nginx      |  96M |  32M | 0.50 |

PostgreSQL намеренно получает наибольшую долю (лимит не занижен
агрессивно) — слишком туго ограниченная СУБД гарантированно упадет по OOM
под нагрузкой, что хуже, чем не ограничивать ее вовсе.

Сумма лимитов сервисов, работающих по умолчанию (без профиля `admin`):
postgres + backend + registry + nginx =
384+192+192+96 = **864M** (Stage 3: Watchtower удален, было 992M с ним).
Лимиты — это потолок (cgroup limit), а не одновременное резервирование:
сумма *reservation* для того же набора сервисов заметно ниже
(192+128+64+32 = 416M), и контейнер обычно потребляет меньше своего
лимита. Тем не менее, после любого редеплоя backend стоит явно сверять
фактическое потребление через `docker stats --no-stream` (см. "Порядок
деплоя" выше), а включение профиля `admin` (+256M pgadmin, суммарный
потолок — 1120M) на VPS с ~1GB RAM разумно только на короткое время
проверки, не постоянно.

**Log rotation.** Все пять сервисов используют `logging: driver:
json-file` с `max-size: "10m"`, `max-file: "3"` — до ~30MB логов на
контейнер, дальше старые файлы ротируются. Без этого логи Docker могут
неограниченно расти и забить диск VPS. (Для Nginx это работает благодаря
тому, что образ `nginx:alpine` по умолчанию симлинкует
`/var/log/nginx/access.log`/`error.log` на `/dev/stdout`/`stderr` — наш
`nginx.conf` эти пути не переопределяет.)

## Версии образов

Образы инфраструктуры зафиксированы на конкретных версиях (без `:latest`) и,
начиная со Stage 3, дополнительно закреплены immutable manifest-list digest
(см. `docker-compose.yml` и `backend/Dockerfile` — точные значения; digest
здесь — manifest list/image index, не одного per-platform манифеста, чтобы
не ломать мультиплатформенность):

| Сервис | Образ |
|---|---|
| postgres | `postgres:16.15-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685` |
| pgadmin | `dpage/pgadmin4:9.16@sha256:40fa840c5bb7c8463957f1255b01283732c2d8c9396a956d180f8e6c296753b3` |
| registry | `registry:3.1.1@sha256:1be55279f18a2fe1a74edf2664cac61c1bea305b7b4642dab412e7affdcb3e33` |
| nginx | `nginx:1.30.4-alpine@sha256:dc5069ad14f19660b141b21236140b91656bf89bbc3e2417c70ae650cd66104c` |
| backend | собственная сборка на базе `python:3.12.14-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea` |

Для `backend` — две разные, не противоречащие друг другу вещи:

- **Compose declaration** (`docker-compose.yml`): `build: ./backend` — build
  context только для локальной разработки.
- **Production delivery**: ни один production release, включая самый
  первый, не собирается на VPS. Production release доставляется через
  private Registry — release image собирается вне VPS для `linux/amd64` и
  пушится с immutable tag в `registry-vibe.elivcloud.org`, на VPS
  выполняется `docker pull` этого image, затем `docker tag` на локальный
  тег `vibe-order-infra-backend:latest`, затем `docker compose up -d
  --no-build` (см. "Порядок деплоя" ниже — тот же принцип применялся к
  релизу, зафиксированному как commit `7547704` (последняя задокументированная
  VPS-приемка, см. "Статус проекта" выше), и применяется к каждому
  последующему релизу backend). Самый первый деплой на этом VPS
  использовал тот же принцип "образ собран вне VPS + детерминированный
  локальный тег + `--no-build`", но доставлял образ через `docker
  save`/`scp`/`docker load` вместо `docker pull`, поскольку Registry на тот
  момент еще не был снаружи достижим (см. "Первый деплой (bootstrap)"
  выше).

Совместимость Registry v3.1.1 с текущей конфигурацией проверена по
официальной документации (см. раздел "Почему так") перед указанием версий
в `docker-compose.yml`.

## Security notes / ограничения

Текущее состояние репозитория (технический baseline `28932b1`;
родительский коммит — `95299dc`; это не описание того, что именно сейчас
развернуто на VPS сверх последней VPS-приемки на commit `7547704`, см.
"Статус проекта" выше):

- Admin-роут (frontend `/admin`) и admin CRUD (backend
  `/api/admin-settings/*` и т.д.) публикуются через Nginx и защищены JWT-
  аутентификацией на уровне самого backend (Argon2id-хэши паролей,
  `Depends(get_current_admin)` на каждом protected route) — см. "API и
  публичный security allowlist". Задеплоено на production VPS и подтверждено
  ручной приемкой на момент commit `7547704`, см. "Historical production
  smoke checklist" и "Ручная end-to-end приемка" — актуальная процедура
  приёмки для сегодняшнего репозитория живёт в
  [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
- Swagger/OpenAPI/Redoc закрыты Nginx на production-периметре независимо от
  auth-стадии — backend их сам не защищает и не отключает (нужны для
  локальной разработки/тестов).
- Схема БД управляется Alembic-миграциями с трёхролевой моделью прав
  (кластерный admin / migration-owner / runtime app) — backup PostgreSQL
  перед каждым обновлением production, меняющим схему, остаётся
  обязательным (нет проверенных `downgrade()`-путей — см. "Ограничение:
  развертывание базы данных" и [docs/RUNBOOK.md](docs/RUNBOOK.md)).
- Behavior-аналитика — первого рода (без сторонних сервисов), локальна и
  агрегирована; точные координаты курсора и содержимое полей формы не
  собираются. Пользователю показывается краткое уведомление о сборе данных
  прямо над формой заявки (см. `frontend/src/pages/home.ts`,
  `privacyNoticeTemplate`).
- Хранение данных заявок (Stage 4): автоматического срока хранения,
  запланированной очистки или воркера-по-расписанию нет. Заявки и связанные
  с ними поведенческие метрики хранятся до явного удаления администратором
  через аутентифицированный `DELETE /api/applications/{id}` — удаление
  каскадно удаляет и связанную запись `behavior_metrics` (FK
  `ON DELETE CASCADE`, см. `app/models/behavior_metric.py`). Эндпоинт уже
  существует и требует admin-токен, но в самой админ-панели (раздел
  "Заявки") пока нет кнопки/действия для удаления — на практике удаление
  сейчас делается прямым авторизованным запросом к API (curl/Postman и
  т.п.), не через UI. Это осознанно задокументированное текущее поведение,
  а не пробел: заводить Celery/cron/воркер под автоматическую ретенцию для
  этого учебного этапа избыточно (см. также "Дальше по плану").
- Секреты (`.env`, `registry/auth/htpasswd`, TLS-ключи, `JWT_SECRET_KEY`) не
  хранятся в git — создаются/монтируются на VPS отдельно, `.env` — с правами
  `600`.
- HSTS включён (Stage 4) на HTTPS-ответах основного сайта
  (`nginx/conf.d/vibe.elivcloud.org.conf`, 443 `server{}`), `max-age=180`
  дней, без `includeSubDomains`/`preload` (см. комментарий в этом файле
  рядом с заголовком) — никогда на порту 80 (HTTP), только после успешного
  редиректа на HTTPS. Content-Security-Policy добавлен там же, выведен из
  реально собранного frontend (`'self'`-only: свой JS/CSS/шрифты, без
  `unsafe-inline`/`unsafe-eval`) — см. комментарий там же. Автопродление
  сертификата Let's Encrypt не автоматизировано. Watchtower удален из
  инфраструктуры (Stage 3, см. "Почему так") — обновления образов только
  явные, ручные.
- Container hardening (Stage 4), проверено живым disposable Compose-стеком:
  `no-new-privileges` на postgres, backend, registry, nginx и всех трёх
  one-shot DB-lifecycle сервисах; `cap_drop: ALL` + `read_only: true`
  (+ tmpfs `/tmp`) на backend и всех трёх one-shot DB-lifecycle сервисах
  (чистый Python-процесс без записи в собственную ФС); `cap_drop: ALL` +
  точечный `cap_add` (`NET_BIND_SERVICE`, `SETUID`, `SETGID`, `CHOWN`) +
  `read_only: true` + tmpfs (`/var/cache/nginx`, `/var/run`, `/tmp`) на
  Nginx. PostgreSQL/Registry получили только `no-new-privileges` — их
  entrypoint'ы делают root-level инициализацию (chown тома, init-скрипты)
  перед сбросом привилегий, поэтому более глубокий hardening для них не
  применялся без отдельного точечного доказательства совместимости (см.
  `docker-compose.yml`, комментарии у каждого сервиса). pgAdmin намеренно
  НЕ получил даже `no-new-privileges`: живая проверка (`docker compose
  --profile admin up`) показала, что под ним pgAdmin детектирует
  "restricted security context" и молча переключает внутренний порт
  прослушивания с 80 на 8080, из-за чего фиксированный проброс порта
  `127.0.0.1:5050:80` (и задокументированный SSH-туннель к нему, см. раздел
  "pgAdmin" выше) перестаёт работать. Ровно тот случай "ломает нормальную
  работу", когда Stage 4 требует откатить hardening, а не обходить его —
  см. комментарий у сервиса `pgadmin` в `docker-compose.yml`.
  Заодно исправлено: three one-shot DB-lifecycle сервиса (db-roles-bootstrap/
  db-migrate/db-roles-finalize) больше не наследуют HTTP-based HEALTHCHECK
  backend-образа — раньше Docker после их успешного exit 0 всё равно помечал
  их "unhealthy" (`healthcheck: disable: true`).

Инфраструктура и подход к security сделаны production-like, но проект не
претендует на полную production-readiness (нет автообновления сертификата,
нет CD/автоматического деплоя — обновления production по-прежнему
выполняются оператором вручную по процедуре из
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).

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
- Watchtower удален из инфраструктуры целиком (Stage 3, включая
  `/var/run/docker.sock`-mount) — консервативнее, чем буквально требовал
  первый проход задания: обновления образов теперь только явные, ручные,
  без постоянно работающего привилегированного контейнера с доступом к
  Docker API.
- Осознанно НЕ включен `internal: true` на Docker-сетях (хотя это
  дополнительно ужесточило бы изоляцию `app-net`/`proxy-net`), так как это
  взаимодействует с публикацией портов не всегда очевидным образом.
  Реальный деплой прошел без этой настройки, поэтому ее влияние на
  публикацию портов (в частности, `127.0.0.1:5050` у pgAdmin) так и не
  проверялось на практике. Возможное улучшение на будущее, требующее
  отдельной проверки перед включением.

## Дальше по плану

Bootstrap, HTTPS, Registry-smoke-test, backend/frontend (публичная форма,
behavior metrics), административная панель, JWT-auth, приоритизация заявок
и поведенческая аналитика — реализованы; технический baseline `28932b1`
(2026-09-05; родительский коммит — `95299dc`) покрыт тестами локально (backend:
650 passed, 0 skipped; frontend: 469 passed — см. "Локальная разработка и
тесты"/"CI"). Полная
ручная production-приемка пройдена по состоянию на commit `7547704` (см.
"Статус проекта" выше и "Ручная end-to-end приемка"); репозиторий с тех
пор продвинулся дальше без отдельной повторной ручной VPS-приемки.
Из содержательного остается:

1. ~~Внедрить Alembic-миграции вместо `Base.metadata.create_all()`~~ —
   сделано: полный Stage 2 database lifecycle (Alembic, трёхролевая модель
   прав, one-shot DB-lifecycle сервисы, усыновление legacy-базы) — см.
   [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
2. ~~Точечный opt-in Watchtower-label для backend~~ — снято с повестки:
   Watchtower удален из инфраструктуры целиком в Stage 3 (см. "Почему
   так"), обновления образов теперь только явные/ручные.
3. Автоматизировать продление сертификата Let's Encrypt (cron/systemd timer
   с `certbot renew`) — в рамках текущего деплоя настраивался только
   первичный выпуск.
4. ~~HSTS~~ — сделано (Stage 4, см. "Security notes / ограничения" выше).
5. CI (GitHub Actions, `.github/workflows/ci.yml`) — реализован (Stage 5,
   см. раздел "CI" ниже); CD/автоматический деплой на VPS по-прежнему не
   реализован и не планируется в рамках этого учебного проекта — production
   обновляется вручную оператором по процедуре из
   [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
