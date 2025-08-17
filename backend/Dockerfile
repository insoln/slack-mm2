FROM python:3.11-slim

# Установка tini для корректного управления процессами
RUN apt-get update && apt-get install -y tini && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get update && apt-get install -y unzip && rm -rf /var/lib/apt/lists/*

COPY infra/db/migrations backend/infra/db/migrations

COPY backend/app ./app
COPY backend/alembic backend/alembic
COPY backend/alembic/versions backend/alembic/versions
COPY alembic.ini .
COPY infra ./infra

ENV PYTHONUNBUFFERED=1

# Использование tini для запуска процесса
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"] 