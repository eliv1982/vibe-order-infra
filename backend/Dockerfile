FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir .

# Порт слушается только внутри Docker-сети — сервис backend в
# docker-compose.yml намеренно не публикует его через "ports:".
EXPOSE 8000

RUN useradd --no-create-home --shell /usr/sbin/nologin appuser
USER appuser

# Один образ, четыре разных запуска (см. docker-compose.yml) — команда и
# набор переменных окружения (креденшлы) различаются per-service, сам образ
# нет:
#   - backend (CMD ниже): uvicorn, только APP_DB_USER/APP_DB_PASSWORD
#     (runtime-роль, без прав DDL — см. app/core/config.py).
#   - db-roles-bootstrap / db-roles-finalize: `python -m app.db_admin.
#     bootstrap_roles`, только кластерный admin-креденшл (POSTGRES_USER/
#     POSTGRES_PASSWORD).
#   - db-migrate: `alembic upgrade head`, только MIGRATION_DB_USER/
#     MIGRATION_DB_PASSWORD (migration/owner-роль — см. backend/alembic/env.py).
# appuser достаточно для всех четырёх: ни один из них не пишет файлы образа.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
