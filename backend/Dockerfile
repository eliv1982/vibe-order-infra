FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir .

# Порт слушается только внутри Docker-сети — сервис backend в
# docker-compose.yml намеренно не публикует его через "ports:".
EXPOSE 8000

RUN useradd --no-create-home --shell /usr/sbin/nologin appuser
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
