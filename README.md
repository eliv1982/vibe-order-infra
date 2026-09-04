# vibe-order-infra

Учебный full-stack проект приема и обработки клиентских заявок для премиального
автомобильного детейлинга (AUREL Detailing). Включает клиентский frontend,
административную панель (JWT-аутентификация, CRUD услуг, обработка и
приоритизация заявок, поведенческая аналитика), backend на FastAPI,
PostgreSQL, сбор агрегированных behavior metrics, Nginx как reverse proxy и
static server, HTTPS, Docker Compose, приватный Docker Registry и pgAdmin.

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

- **Инфраструктура**: `nginx:1.30.4-alpine`, `postgres:16.14-alpine` и
  собственный образ `backend` — запущены на production VPS,
  `RestartCount=0` у всех сервисов. `registry:3.1.1` запущен и работает (у
  Registry по дизайну нет Docker-healthcheck, см. "Почему так" ниже;
  `backend` начиная со Stage 3 — есть, см. "Backend healthcheck и
  readiness"). `pgAdmin` запускается только через профиль `admin`, по
  требованию. Watchtower удалён из инфраструктуры в Stage 3 (см. "Почему
  так") — обновления образов теперь только явные, вручную.
- **HTTPS**: Let's Encrypt сертификат на один SAN на оба домена
  (`vibe.elivcloud.org`, `registry-vibe.elivcloud.org`); `https://vibe.elivcloud.org`
  отвечает `200`; HTTP редиректит на HTTPS, кроме ACME challenge и `/healthz`.
- **Registry**: пользователь создан через `registry/create-user.sh`, полный
  push/pull smoke-test пройден (подробности — в разделе "Почему так");
  `GET /v2/` без credentials возвращает `401` с Basic auth challenge.
- **PostgreSQL / pgAdmin**: 5432 наружу не публикуется; pgAdmin проверен
  через SSH-туннель и остановлен после проверки.
- **Backend + frontend, включая административную панель, JWT-аутентификацию,
  приоритизацию заявок и поведенческую аналитику**: реализованы, покрыты
  тестами локально (backend suite на реальной PostgreSQL: **355 passed**,
  frontend suite: **447 passed**) и **задеплоены на production VPS**.
  Полная ручная production-приемка пройдена (см. "Ручная end-to-end
  приемка" ниже): регистрация первого администратора, повторные
  login/logout, CRUD услуг, публичная форма заявки, behavior metrics,
  explainable scoring с приоритетами Высокий/Средний/Стандартный,
  поведенческая аналитика за 24 часа/7 дней/30 дней (включая состояния
  "метрики есть"/"метрики отсутствуют"), закрытые технические endpoints
  (`/docs`, `/redoc`, `/openapi.json`, неизвестные `/api/*`).
- Первый администратор существует — `GET /api/auth/check` возвращает
  `admin_exists: true` (поле `registration_allowed` в текущем API-контракте
  отсутствует: публичной HTTP-регистрации администратора больше нет вообще
  — см. "First production admin" ниже).

Не сделано осознанно (см. "Security notes / ограничения" ниже): HSTS,
автоматизация продления сертификата.

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
только явные, ручные: `docker compose pull && docker compose up -d`.)
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
│   │   ├── models/          # SQLAlchemy ORM: Admin, Application, BehaviorMetric, AdminSetting
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

Сервисы: `postgres`, `backend`, `nginx`, `registry`, `pgadmin` (профиль
`admin`).

**backend**: Compose-декларация — `build: ./backend` (build context для
локальной разработки и для самого первого bootstrap, когда локального
image еще нет). Фактическая production-доставка — отдельная: release
image собирается вне VPS для `linux/amd64` и доставляется через private
Registry (`registry-vibe.elivcloud.org`) с immutable tag, на VPS
выполняется `docker pull`, а не `docker compose build` (см. "Порядок
деплоя" ниже); сети — `app-net` (доступ к `postgres`) и `proxy-net`
(доступность для Nginx); порт 8000 НЕ публикуется на host (нет `ports:`);
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

### Обновление / повторный деплой (текущий безопасный flow, применим к каждому будущему релизу)

Этот flow — не одноразовая процедура «добавить таблицу `admins`», а
повторяемая процедура для **любого** будущего обновления backend/frontend.
Backup PostgreSQL перед обновлением обязателен только тогда, когда релиз
меняет схему БД (добровольная DDL-операция через
`Base.metadata.create_all()` — см. "Ограничение: развертывание базы
данных" ниже); для чисто frontend-релиза или backend-релиза без изменений
схемы этот шаг не обязателен, но не будет лишним.

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
8. Пересоздать **только** backend:
   ```bash
   docker compose up -d --no-build --no-deps backend
   ```
   `--no-build` запрещает Compose собирать образ на VPS даже при наличии
   `build: ./backend` в декларации — используется только уже загруженный
   через `docker pull`/`docker tag` image. `--no-deps` не поднимает и не
   затрагивает зависимости backend (`postgres` из `depends_on`) — Compose
   не проверяет и не трогает healthcheck `postgres`, а просто пересоздает
   контейнер backend на уже работающей БД. Пересоздается только backend;
   остальные сервисы — `nginx`, `postgres`, `registry`, `pgadmin` — не
   перезапускаются этим шагом. Если релиз меняет схему БД,
   при старте backend вызовет `Base.metadata.create_all()` — создаст
   отсутствующие таблицы, существующие таблицы/данные не затрагивает.

**Frontend (собран вне VPS, см. "Production build frontend" выше):**

9. Собрать `frontend/dist` на build-машине, передать на VPS как
   версионированный artifact с SHA-256 контрольной суммой, опубликовать
   hashed assets без удаления старых, затем атомарно заменить `index.html`
   — полная процедура и обоснование порядка — см. "Production build
   frontend" выше.

**Nginx (только если конфигурация меняется в этом релизе):**

10. `nginx -t` **обязательно ДО** применения новой конфигурации — не после
    и не одновременно с ней.
11. Если `nginx -t` прошел — применить **только graceful reload**, без
    recreate контейнера: `docker compose exec nginx nginx -s reload` (или
    `docker compose kill -s HUP nginx`). Пересоздание/restart контейнера
    Nginx для смены конфигурации не требуется и не выполняется.
12. Если `nginx -t` НЕ прошел — не применять reload, откатить
    `nginx/conf.d/*.conf` до валидного состояния.

**После обновления любого компонента:**

13. Smoke tests — см. "Production smoke checklist" ниже (публикация `/admin`,
    protected API без токена → `401`, `/docs`/`/openapi.json`/`/redoc`
    снаружи недоступны и т.д.).
14. `docker compose ps` — все сервисы в статусе `running`/`healthy`.
15. `docker stats --no-stream` — сверить фактическое потребление
    памяти/CPU с лимитами (см. "Resource protection").

`docker compose down` не используется как часть стандартного flow
обновления — он остановил бы все сервисы разом вместо контролируемого
поочередного обновления. Сборка (`docker compose build` / `npm run build`
напрямую на хосте) на самом VPS не рекомендуется в принципе — на VPS с
RAM около 1 GB build-процесс (особенно TypeScript/Vite или Python wheel
compilation) конкурирует за память с работающими сервисами и рискует
уронить их по OOM.

## Ограничение: развертывание базы данных (Alembic отсутствует)

- Alembic-миграции в проекте пока не используются.
- Backend вызывает `Base.metadata.create_all()` при старте (`lifespan` в
  `app/main.py`) — этот вызов создает только отсутствующие таблицы и НЕ
  удаляет и не изменяет существующие таблицы/данные.
- При первом запуске текущей версии backend на существующей production-базе
  была создана недостающая таблица `admins` — остальные таблицы
  (`applications`, `behavior_metrics`, `admin_settings`) и их данные
  остались нетронутыми.
- Тем не менее, **перед каждым обновлением production, меняющим схему БД,
  обязателен backup PostgreSQL** — `create_all()` осознанно принят для
  текущего учебного этапа именно при этом условии, а не как замена
  миграциям в общем случае. Это правило действует для любого будущего
  релиза со схемными изменениями, а не только для того релиза, что впервые
  добавил таблицу `admins`.
- Для полноценного production-grade развития схемы БД в дальнейшем
  запланирован переход на Alembic (см. "Дальше по плану").

Backup — вне репозитория, с конкретным именем файла (без wildcard), без
раскрытия credentials в самой команде (значения читаются из переменных
окружения контейнера `postgres` через `$POSTGRES_USER`/`$POSTGRES_DB`
внутри контейнера, не подставляются в текст команды на хосте):

```bash
backup_dir="$HOME/vibe-order-infra-backups"
install -d -m 700 "$backup_dir"

backup_file="$backup_dir/backup-before-<release-tag>-$(date +%Y%m%d-%H%M%S).sql"

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

### Финальная production-приемка (текущий релиз, commit `7547704`)

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
начиная с текущего релиза `/admin` и admin API публикуются (защищены JWT на
уровне backend, а не 404 в Nginx) — см. "API и публичный security
allowlist" выше и "Финальная production-приемка" выше для актуального
smoke-test этой функциональности на VPS.

## Production smoke checklist

Чеклист использован для деплоя и приемки текущего релиза на VPS. Все пункты
подтверждены (актуальный read-only production-аудит — см. также
"Финальная production-приемка" выше):

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

Текущий релиз (admin-панель, JWT-auth, приоритизация заявок, analytics) —
см. "Финальная production-приемка" выше для полного списка проверенных
сценариев (регистрация первого администратора, login/logout, CRUD услуг,
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
только явные, ручные (`docker compose pull && docker compose up -d`), без
постоянно работающего привилегированного контейнера, слушающего Docker API.

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
| postgres | `postgres:16.14-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777` |
| pgadmin | `dpage/pgadmin4:9.16@sha256:40fa840c5bb7c8463957f1255b01283732c2d8c9396a956d180f8e6c296753b3` |
| registry | `registry:3.1.1@sha256:1be55279f18a2fe1a74edf2664cac61c1bea305b7b4642dab412e7affdcb3e33` |
| nginx | `nginx:1.30.4-alpine@sha256:dc5069ad14f19660b141b21236140b91656bf89bbc3e2417c70ae650cd66104c` |
| backend | собственная сборка на базе `python:3.12.10-slim@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db` |

Для `backend` — две разные, не противоречащие друг другу вещи:

- **Compose declaration** (`docker-compose.yml`): `build: ./backend` — build
  context для локальной разработки и для самого первого bootstrap.
- **Production delivery**: текущий production release доставлен не через
  локальный `docker compose build` на VPS, а через private Registry —
  release image собран вне VPS для `linux/amd64` и запушен с immutable tag
  в `registry-vibe.elivcloud.org`, на VPS выполнен `docker pull` этого
  image (см. "Порядок деплоя" ниже — этот же принцип применяется к каждому
  будущему релизу backend, не только к текущему).

Совместимость Registry v3.1.1 с текущей конфигурацией проверена по
официальной документации (см. раздел "Почему так") перед указанием версий
в `docker-compose.yml`.

## Security notes / ограничения

Текущее состояние:

- Admin-роут (frontend `/admin`) и admin CRUD (backend
  `/api/admin-settings/*` и т.д.) публикуются через Nginx и защищены JWT-
  аутентификацией на уровне самого backend (Argon2id-хэши паролей,
  `Depends(get_current_admin)` на каждом protected route) — см. "API и
  публичный security allowlist". Задеплоено на production VPS и подтверждено
  ручной приемкой, см. "Production smoke checklist" и "Ручная end-to-end
  приемка".
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
  автоматизировано. Watchtower удален из инфраструктуры (Stage 3, см.
  "Почему так") — обновления образов только явные, ручные.

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
и поведенческая аналитика — реализованы, покрыты тестами локально
(backend: 355 passed, frontend: 447 passed) и **задеплоены на production
VPS**; полная ручная приемка пройдена (см. "Ручная end-to-end приемка").
Из содержательного остается:

1. Внедрить Alembic-миграции вместо `Base.metadata.create_all()` — нужно
   для безопасной эволюции схемы БД в будущем.
2. ~~Точечный opt-in Watchtower-label для backend~~ — снято с повестки:
   Watchtower удален из инфраструктуры целиком в Stage 3 (см. "Почему
   так"), обновления образов теперь только явные/ручные.
3. Автоматизировать продление сертификата Let's Encrypt (cron/systemd timer
   с `certbot renew`) — в рамках текущего деплоя настраивался только
   первичный выпуск.
4. HSTS — включить отдельным шагом после более длительного периода
   стабильной работы HTTPS.
