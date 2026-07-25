# vibe-order-infra

Учебный full-stack проект приема и обработки клиентских заявок для премиального
автомобильного детейлинга (AUREL Detailing). Включает клиентский frontend,
административную панель (JWT-аутентификация, CRUD услуг, обработка и
приоритизация заявок, поведенческая аналитика), backend на FastAPI,
PostgreSQL, сбор агрегированных behavior metrics, Nginx как reverse proxy и
static server, HTTPS, Docker Compose, приватный Docker Registry, Watchtower и
pgAdmin.

Проект учебный, но архитектура и security-подход сделаны в production-like
стиле: минимальный набор публичных портов, allowlist на уровне Nginx по
`/api/`-префиксам (реальную авторизацию — public/protected, JWT — выполняет
сам backend, а не Nginx), разделение Docker-сетей, лимиты ресурсов, версии
образов зафиксированы, секреты не хранятся в git. Локальный репозиторий
остается источником истины для конфигурации: секреты (`.env`,
`registry/auth/htpasswd`, TLS-ключи) на VPS создаются/монтируются отдельно и
в git не попадают.

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
- **Backend + frontend (базовый функционал)**: реализованы, задеплоены,
  ручная end-to-end приемка пройдена (см. раздел "Ручная end-to-end
  приемка" ниже) — публичная форма заявки, behavior metrics, список
  активных услуг.
- **Административная панель, JWT-аутентификация, приоритизация заявок и
  поведенческая аналитика** (ветка `feature/final-admin-analytics`):
  реализованы и покрыты тестами локально — backend suite на реальной
  PostgreSQL: **355 passed**, frontend suite: **447 passed**.
  Nginx-конфигурация для публикации `/admin` и финального API surface
  подготовлена в рамках deployment-preparation этапа этой ветки (см. "API и
  публичный security allowlist" и "Nginx" ниже), но **сам деплой этой
  функциональности на VPS в рамках данной сессии не выполнялся** — см.
  "Production smoke checklist" и "Порядок деплоя" ниже для процедуры перед
  reload на боевом сервере (обязателен backup PostgreSQL до обновления, см.
  "Ограничение: развертывание базы данных" ниже).

Не сделано осознанно (см. "Security notes / ограничения" ниже): HSTS,
автоматизация продления сертификата, Alembic-миграции, Watchtower opt-in
для backend.

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
│   │   ├── routes/          # HTTP-обработчики (/api/applications, /api/behavior-metrics,
│   │   │                    #   /api/admin-settings, /api/auth, /api/analytics)
│   │   └── main.py          # FastAPI app, lifespan (create_all), сборка /api-роутера
│   ├── tests/                # pytest: unit (schemas, db safety guard) + integration (реальный PostgreSQL)
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

Административная страница (`frontend/src/pages/admin.ts`) отвечает
клиентскому роуту `/admin` и публикуется Nginx как обычный SPA-путь
(`location = /admin` / `location = /admin/`, см. "Nginx" ниже) — сам Nginx
никакой авторизации не делает, ей полностью занимается backend через JWT:

- `GET /api/auth/check` — есть ли уже созданный администратор;
- пока администратора нет — форма регистрации первого администратора
  (`POST /api/auth/register`), после чего endpoint регистрации перестает
  разрешать создание следующего первого admin (см. "First production admin"
  ниже);
- иначе — форма входа (`POST /api/auth/login`), токен хранится в
  `sessionStorage` (не `localStorage`, чтобы забытая открытой вкладка не
  держала сессию бессрочно) и прикладывается как `Authorization: Bearer` к
  защищенным запросам; `GET /api/auth/me` подтверждает валидность сессии
  при каждой загрузке страницы.

После входа административная панель состоит из трех вкладок:

- **Услуги** — CRUD активных/неактивных услуг (`/api/admin-settings`);
- **Заявки** — список/поиск/фильтр заявок по приоритету
  (`/api/applications`, `/api/applications/prioritized`) и карточка
  заявки;
- **Статистика** — поведенческая аналитика (`/api/analytics/*`).

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
| POST | `/api/auth/register` — только пока не создан ни один администратор |
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
учебный API не должен светить схему эндпоинтов наружу, даже после включения
auth. `/api/openapi.json` тоже не проксируется — падает в общий `/api/` →
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

- `WATCHTOWER_POLL_INTERVAL` (по умолчанию 86400с = раз в сутки).
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
admin CRUD, приоритизацию заявок и analytics): **355 passed**.

### Frontend

```bash
cd frontend
npm ci
npm test
npm run build
npm audit
```

Итог: **447 tests passed**, `npm run build` — успешно, `npm audit` — **0
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

### Обновление / повторный деплой (текущий flow, с backend + frontend + admin/analytics)

1. **Backup PostgreSQL — обязателен перед этим конкретным обновлением**, т.к.
   оно добавляет новую таблицу `admins` через `Base.metadata.create_all()`
   (Alembic пока не используется — см. "Ограничение: развертывание базы
   данных" ниже). Не переходить к шагу 2, пока backup-файл не создан и не
   проверен непустым.
2. `git pull`/`git fetch` — обновить репозиторий на VPS.
3. `docker compose config --quiet` — убедиться, что `.env` полон (включая
   `JWT_SECRET_KEY`) и конфигурация валидна, до запуска чего-либо.
4. Собрать backend-образ: `docker compose build backend`.
5. Поднять/пересоздать backend: `docker compose up -d backend` (Compose
   дождется `postgres` healthy благодаря `depends_on`). При старте backend
   вызовет `Base.metadata.create_all()` — создаст отсутствующую таблицу
   `admins`, существующие таблицы/данные не затрагивает.
6. Собрать frontend вне хоста — временным официальным Node-контейнером
   (см. "Production build frontend" выше) — `frontend/dist` обновляется на
   хосте.
7. Пересоздать/перезапустить `nginx` — конфигурация в этом релизе меняется
   (body limit, `/admin`, финальный API allowlist), поэтому это обязательный
   шаг, а не опциональный.
8. `nginx -t` — проверить синтаксис конфигурации перед reload/restart.
9. Smoke tests — см. "Production smoke checklist" ниже (публикация `/admin`,
   первый admin, protected API без токена → `401`, `/docs`/`/openapi.json`/
   `/redoc` снаружи недоступны и т.д.).
10. `docker compose ps` — все сервисы в статусе `running`/`healthy`.
11. `docker stats --no-stream` — сверить фактическое потребление
    памяти/CPU с лимитами (особенно важно теперь, когда backend добавлен в
    набор постоянно работающих сервисов — см. "Resource protection").

`docker compose down` не используется как часть стандартного flow
обновления — он остановил бы все сервисы разом вместо контролируемого
поочередного обновления.

## Ограничение: развертывание базы данных (Alembic отсутствует)

- Alembic-миграции в проекте пока не используются.
- Backend вызывает `Base.metadata.create_all()` при старте (`lifespan` в
  `app/main.py`) — этот вызов создает только отсутствующие таблицы и НЕ
  удаляет и не изменяет существующие таблицы/данные.
- При первом запуске версии backend с этой ветки на существующей
  production-базе будет создана недостающая таблица `admins` — остальные
  таблицы (`applications`, `behavior_metrics`, `admin_settings`) и их данные
  остаются нетронутыми.
- Тем не менее, **перед обновлением production обязателен backup
  PostgreSQL** — `create_all()` осознанно принят для текущего учебного этапа
  именно при этом условии, а не как замена миграциям в общем случае.
- Для полноценного production-grade развития схемы БД в дальнейшем
  запланирован переход на Alembic (см. "Дальше по плану").

Backup — вне репозитория, с конкретным именем файла (без wildcard), без
раскрытия credentials в самой команде (значения читаются из переменных
окружения контейнера `postgres` через `$POSTGRES_USER`/`$POSTGRES_DB`
внутри контейнера, не подставляются в текст команды на хосте):

```bash
backup_dir="$HOME/vibe-order-infra-backups"
install -d -m 700 "$backup_dir"

backup_file="$backup_dir/backup-before-admin-analytics-$(date +%Y%m%d-%H%M%S).sql"

docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > "$backup_file"

test -s "$backup_file"
ls -lh "$backup_file"
```

`$backup_file` — единственная переменная, указывающая на конкретный,
только что созданный файл: последующая проверка (`test -s`) относится
именно к нему, а не к произвольному файлу, попавшему под маску. Каталог
`$HOME/vibe-order-infra-backups` — вне рабочей копии репозитория (не
коммитится и не может быть случайно закоммичен), права `700` ограничивают
доступ к бэкапам (внутри — дамп БД, потенциально чувствительные данные
заявок) только владельцу.

**Если `test -s "$backup_file"` возвращает ненулевой код (файла нет или он
пустой) — НЕ продолжать деплой.** Это жёсткий стоп-критерий, не
рекомендация: `Base.metadata.create_all()` не заменяет полноценные
миграции (см. выше), и без подтверждённого непустого backup откатываться
в случае проблемы с обновлением будет нечем. Сам процесс НЕ включает
автоматический rollback — восстановление из `$backup_file` в случае
проблемы выполняется вручную (`psql`/`pg_restore` по обстоятельствам), это
не одношаговая операция и не описывается здесь как таковая.

## First production admin

После деплоя этой ветки на VPS (см. "Обновление / повторный деплой" выше):

1. Открыть `https://vibe.elivcloud.org/admin` в браузере.
2. Т.к. администраторов еще нет (`GET /api/auth/check` вернет
   `admin_exists: false`), страница покажет форму регистрации первого
   администратора — заполнить имя пользователя и пароль.
3. После успешной регистрации (`POST /api/auth/register`) endpoint
   регистрации перестает разрешать создание следующего первого admin —
   повторный `POST /api/auth/register` вернет `409 Conflict` независимо от
   переданных данных.
4. Все последующие входы — через `POST /api/auth/login` (форма входа на
   `/admin`), пароли хранятся в виде Argon2id-хэша.

Реальные production username/password в README не публикуются.

## Ручная end-to-end приемка

Функциональный сценарий, пройденный вручную end-to-end на VPS (до
`feature/final-admin-analytics` — на состоянии проекта без admin-панели,
JWT-auth и analytics):

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
начиная с `feature/final-admin-analytics` `/admin` и admin API публикуются
(защищены JWT на уровне backend, а не 404 в Nginx) — см. "API и публичный
security allowlist" выше. Свежий smoke-test для этой функциональности на VPS
пока не проводился — см. "Production smoke checklist" ниже.

## Production smoke checklist

Проверки для выполнения на VPS **после** деплоя этой ветки (не выполнены в
рамках данной deployment-preparation сессии — эта сессия не подключалась к
VPS и не проводила production-деплой):

Перед деплоем:

- [ ] `docker compose config` (и `--profile admin config`) проходит без
      ошибок с реальным `.env` на VPS.
- [ ] `nginx -t` проходит на самом VPS перед `reload`/`restart` Nginx —
      обязателен независимо от любых локальных проверок конфигурации.
- [ ] Backup PostgreSQL создан и подтверждён непустым (`test -s
      "$backup_file"`) — см. "Ограничение: развертывание базы данных". Без
      этого деплой не продолжается.

Функциональные проверки после деплоя:

- [ ] `/` открывается по HTTPS.
- [ ] `/admin` открывается и работает при прямом refresh страницы (без 404).
- [ ] Административная авторизация — полный цикл:
      - [ ] регистрация первого администратора (`POST /api/auth/register`)
        проходит успешно;
      - [ ] повторная регистрация первого admin получает ожидаемый отказ
        (`409 Conflict`, а не повторное создание);
      - [ ] logout (кнопка "Выйти" в `/admin`) очищает JWT из
        `sessionStorage` и возвращает UI на экран входа (не оставляет
        доступным ни один protected-раздел панели без повторной
        аутентификации);
      - [ ] повторный login (`POST /api/auth/login`) после logout снова
        работает и восстанавливает доступ к панели.
- [ ] Публичная форма заявки (`POST /api/applications`) и behavior metrics
      (`POST /api/behavior-metrics`) работают без токена.
- [ ] Protected API без токена (например `GET /api/applications`,
      `GET /api/analytics/overview`) возвращает `401`, а не 200/404.
- [ ] CRUD услуг (`/api/admin-settings`) работает из `/admin` под валидным
      токеном.
- [ ] Заявки: список/поиск/фильтр по приоритету и приоритизация
      (`GET /api/applications/prioritized`, scoring reasons) отображаются в
      `/admin`.
- [ ] Статистика за день/неделю/месяц (`GET /api/analytics/overview?period=`)
      отображается во вкладке "Статистика".
- [ ] Detail-аналитика заявки, у которой ЕСТЬ behavior metrics
      (`GET /api/analytics/applications/{application_id}`), отображает
      реальные агрегаты.
- [ ] Detail-аналитика заявки, у которой НЕТ behavior metrics (сабмит без
      последующего `POST /api/behavior-metrics`), отображает пустое
      состояние, а не ошибку.
- [ ] `/docs`, `/docs/`, `/redoc`, `/redoc/`, `/openapi.json`,
      `/api/openapi.json` снаружи недоступны (`404`).
- [ ] Registry (`registry-vibe.elivcloud.org`) требует аутентификацию —
      запрос к `/v2/` без креденшлов возвращает `401` с Basic auth challenge.
- [ ] pgAdmin доступен только через `127.0.0.1:5050` на самом VPS — не
      слушает внешний интерфейс, доступ с локальной машины только через
      SSH-туннель (см. "pgAdmin" в разделе "Почему так").
- [ ] Backend (`:8000`) и PostgreSQL (`:5432`) не публикуют портов на host
      наружу (`docker compose port backend 8000` / аналогичная проверка не
      находит публичного маппинга).

Здоровье инфраструктуры после запуска:

- [ ] `docker compose ps` — все сервисы в статусе `running`/`healthy`.
- [ ] Свежие логи каждого сервиса после запуска не содержат неожиданных
      ошибок/трейсбэков (`docker compose logs --tail=100 <service>` —
      особенно `backend` и `nginx`).

## Скриншоты / результаты

Файлы скриншотов в репозитории отсутствуют — здесь только перечислены
сценарии, подтвержденные скриншотами при сдаче предыдущего этапа (до
`feature/final-admin-analytics`):

- Swagger: `POST /api/admin-settings` (создание услуги).
- Swagger: `GET /api/admin-settings/active` (список активных услуг).
- PostgreSQL: содержимое таблицы `admin_settings`.
- Frontend: главная страница (home).
- Frontend: выбор услуги и бюджета.
- Frontend: успешная отправка заявки.
- PostgreSQL: содержимое таблицы `applications`.
- PostgreSQL: содержимое таблицы `behavior_metrics`.

Скриншоты для admin-панели/auth/analytics (регистрация первого
администратора, вход, CRUD услуг, список/приоритизация заявок, статистика
день/неделя/месяц, detail-метрики заявки) появятся после реального
server-side E2E прогона этой ветки на VPS — точные имена файлов определим
по итогам этого прогона, не раньше. Production deployment этой ветки в
рамках текущей сессии **не выполнялся**.

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
  `/api/admin-settings/*` и т.д.) публикуются через Nginx и защищены JWT-
  аутентификацией на уровне самого backend (Argon2id-хэши паролей,
  `Depends(get_current_admin)` на каждом protected route) — см. "API и
  публичный security allowlist". Сам деплой этой конфигурации на VPS в
  рамках данной сессии не выполнялся, см. "Production smoke checklist".
- Swagger/OpenAPI/Redoc закрыты Nginx на production-периметре независимо от
  auth-стадии — backend их сам не защищает и не отключает (нужны для
  локальной разработки/тестов).
- Автоматические миграции БД через Alembic пока не внедрены;
  `Base.metadata.create_all()` — осознанное решение для текущего учебного
  этапа, принятое строго при условии обязательного backup PostgreSQL перед
  каждым обновлением production (см. "Ограничение: развертывание базы
  данных").
- Behavior-аналитика — первого рода (без сторонних сервисов), локальна и
  агрегирована; точные координаты курсора и содержимое полей формы не
  собираются.
- Секреты (`.env`, `registry/auth/htpasswd`, TLS-ключи, `JWT_SECRET_KEY`) не
  хранятся в git — создаются/монтируются на VPS отдельно, `.env` — с правами
  `600`.
- HSTS не включен, автопродление сертификата Let's Encrypt не
  автоматизировано, Watchtower не обновляет автоматически ни один сервис
  (opt-in выключен везде, включая backend).

Проект в целом — учебный: инфраструктура и подход к security сделаны
production-like, но проект не претендует на полную production-readiness (нет
миграций схемы через Alembic, нет автообновления сертификата).

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

Bootstrap, HTTPS, Registry-smoke-test, базовый backend/frontend (публичная
форма, behavior metrics) уже реализованы, задеплоены и прошли приемку (см.
"Ручная end-to-end приемка"). Административная панель, JWT-auth,
приоритизация заявок и analytics реализованы и покрыты тестами локально на
ветке `feature/final-admin-analytics`, Nginx подготовлен к их публикации.
Из содержательного остается:

1. Задеплоить эту ветку на VPS (backup PostgreSQL → обновление backend →
   пересборка frontend → reload Nginx) и пройти "Production smoke checklist"
   — см. "Порядок деплоя".
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
