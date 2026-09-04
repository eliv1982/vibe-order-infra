# Stage 3: базовый образ закреплён и по точной patch-версии (3.12.10 —
# совпадает с локальным baseline из pyproject.toml/requires-python), и по
# immutable digest — это digest самого manifest list (image index), а не
# отдельного per-platform manifest (проверено: `docker buildx imagetools
# inspect python:3.12.10-slim` показывает MediaType
# application/vnd.oci.image.index.v1+json с несколькими Platform-записями,
# включая linux/amd64 — то, что реально используется в проде). Поэтому
# `docker pull`/`docker build` по этому digest продолжает резолвиться в
# правильный образ под текущую архитектуру, а не ломает мультиплатформенность.
# Тег (`3.12.10-slim`) оставлен рядом с digest намеренно — он документирует,
# какая это версия, для человека; digest гарантирует, что под этим тегом
# впоследствии не подменят содержимое молча.
FROM python:3.12.10-slim@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db

WORKDIR /app

# Stage 3: детерминированная установка зависимостей. requirements.lock.txt —
# полностью зафиксированное (включая транзитивные пакеты, И build-system
# зависимости из [build-system] requires — hatchling и его собственные
# зависимости, см. `--all-build-deps` в header-комментарии lock-файла) и
# хэш-верифицируемое дерево зависимостей, сгенерированное pip-compile
# (pip-tools) из pyproject.toml — см. backend/requirements.lock.txt
# (header-комментарий внутри него — это и есть команда для регенерации) и
# README/отчёт Stage 3 про выбранную стратегию локинга. `--require-hashes`
# заставляет pip отказаться установить что угодно, чей скачанный файл не
# совпал с одним из зафиксированных здесь хэшей — компрометация пакета на
# PyPI (или MITM) не может тихо подменить файл для уже запиненной версии.
#
# Копируется и устанавливается ДО остального кода — так пересборка образа при
# изменении только app/ переиспользует закэшированный слой с зависимостями
# (обычный Docker layer caching), а не переустанавливает их каждый раз.
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY healthcheck.py ./

# --no-deps: все зависимости уже установлены выше, зафиксированы и
# хэш-верифицированы — этот шаг лишь регистрирует сам локальный пакет `app`
# (см. [tool.hatch.build.targets.wheel] в pyproject.toml) в интерпретаторе,
# он не должен заново резолвить/скачивать что-либо из индекса.
# --no-build-isolation: hatchling (объявлен в [build-system] requires) уже
# установлен выше из requirements.lock.txt — без этого флага pip создал бы
# отдельное изолированное окружение и заново скачал hatchling из PyPI
# нерезолвленной/незапиненной версии специально для этой сборки, что было бы
# единственным источником сетевой неопределённости в собранном образе.
RUN pip install --no-cache-dir --no-deps --no-build-isolation .

# Порт слушается только внутри Docker-сети — сервис backend в
# docker-compose.yml намеренно не публикует его через "ports:".
EXPOSE 8000

RUN useradd --no-create-home --shell /usr/sbin/nologin appuser
USER appuser

# Stage 3: реальный healthcheck образа — проверяет не факт наличия процесса
# Python/Uvicorn, а то, что backend действительно готов принимать трафик
# (см. app/main.py::readiness_check и app/core/schema_check.py, и
# healthcheck.py — сам скрипт healthcheck'а). Обращается к /api/ready на
# 127.0.0.1:8000 — то есть тому же самому network namespace контейнера,
# снаружи через Nginx он не проксируется (см. nginx/conf.d — allowlist
# пропускает только /api/health; /api/ready намеренно внутренний, не
# публичный эндпоинт).
#
# healthcheck.py использует только stdlib (urllib), а не curl/wget — в
# образе python:*-slim ни того, ни другого нет, добавлять отдельный пакет
# только ради healthcheck не оправдано, когда нужный HTTP-клиент уже есть в
# стандартной библиотеке интерпретатора, который и так есть в образе.
#
# start_period=15s — backend стартует уже ПОСЛЕ того, как вся цепочка
# bootstrap -> migrate -> finalize (см. docker-compose.yml, depends_on:
# db-roles-finalize: condition: service_completed_successfully) успешно
# завершилась, то есть база к моменту запуска backend уже готова — grace
# здесь нужен только на инициализацию самого процесса Uvicorn/SQLAlchemy
# engine, не на миграции. interval/timeout/retries — минимально достаточные
# значения, чтобы не считать контейнер unhealthy из-за единичного медленного
# запроса, но и не ждать неоправданно долго при реальном сбое.
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "healthcheck.py"]

# Один образ, четыре разных запуска (см. docker-compose.yml) — команда и
# набор переменных окружения (креденшлы) различаются per-service, сам образ
# нет:
#   - backend (CMD ниже): uvicorn, только APP_DB_USER/APP_DB_PASSWORD
#     (runtime-роль, без прав DDL — см. app/core/config.py). Единственный
#     из четырёх, у кого есть HEALTHCHECK выше (db-roles-bootstrap/
#     db-migrate/db-roles-finalize — one-shot сервисы без "restart" и без
#     долгоживущего процесса, которому нужен бы был healthcheck).
#   - db-roles-bootstrap / db-roles-finalize: `python -m app.db_admin.
#     bootstrap_roles`, только кластерный admin-креденшл (POSTGRES_USER/
#     POSTGRES_PASSWORD).
#   - db-migrate: `alembic upgrade head`, только MIGRATION_DB_USER/
#     MIGRATION_DB_PASSWORD (migration/owner-роль — см. backend/alembic/env.py).
# appuser достаточно для всех четырёх: ни один из них не пишет файлы образа.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
