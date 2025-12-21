FROM python:3.14-slim

# Установка tini и ca-certificates для корректного управления процессами и SSL
RUN apt-get update && apt-get install -y tini ca-certificates && rm -rf /var/lib/apt/lists/*

# Update CA certificates
RUN update-ca-certificates

WORKDIR /app

COPY backend/requirements.txt .
# Install Python dependencies
# Note: --trusted-host flags are used to work around SSL certificate verification issues
# in CI/corporate environments with SSL-intercepting proxies. This is a known trade-off:
# - PRO: Allows builds to succeed in environments with self-signed CA certificates
# - CON: Disables TLS verification for PyPI downloads during build
# Alternative: Configure proper CA trust store (PIP_CERT/REQUESTS_CA_BUNDLE) if available
# Package integrity is still verified via pip's hash checking when requirements.txt includes hashes
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