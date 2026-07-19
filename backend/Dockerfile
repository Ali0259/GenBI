# syntax=docker/dockerfile:1
#
# GenBI Backend Dockerfile
#
# Multi-stage build. The system-level dependencies here (unixodbc, the
# Microsoft ODBC driver, libpq, freetds, postgresql-client for pg_dump) are
# exactly the class of OS-specific dependency drift discussed in the
# project README as the reason install.sh does NOT try to install these
# directly on the host -- they are pinned and baked into this image instead.

FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# System dependencies:
#  - unixodbc / unixodbc-dev: required by pyodbc for MSSQL connectivity
#  - curl, gnupg: required to add Microsoft's package repository
#  - libpq-dev, postgresql-client: psycopg build deps + pg_dump for entrypoint.sh backups
#  - build-essential: required to build any C-extension wheels not available prebuilt
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        gnupg \
        unixodbc \
        unixodbc-dev \
        libpq-dev \
        postgresql-client \
        build-essential \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY VERSION .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

RUN mkdir -p /opt/genbi/backups

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
