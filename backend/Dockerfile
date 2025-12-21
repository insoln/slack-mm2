FROM python:3.14-slim

# Установка tini и ca-certificates для корректного управления процессами и SSL
RUN apt-get update && apt-get install -y tini ca-certificates && rm -rf /var/lib/apt/lists/*

# Update CA certificates
RUN update-ca-certificates

WORKDIR /app

COPY backend/requirements.txt .
# Install Python dependencies with trusted host to work around corporate proxy/firewall SSL interception
# This is necessary in CI environments with self-signed certificates in the chain
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt \
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