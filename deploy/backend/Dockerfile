# syntax=docker/dockerfile:1
# Ship backend (RFC-0006). Same image is the API server (`ship-server`)
# and the background worker (`ship-worker`); the entrypoint command differs.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
        libpq5 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-backend.txt ./
RUN pip install -r requirements-backend.txt

COPY backend ./backend
COPY VERSION ./VERSION
COPY deploy/backend/entrypoint.sh /usr/local/bin/ship-entrypoint
RUN chmod +x /usr/local/bin/ship-entrypoint

# Default command runs the API. docker-compose overrides this for the worker
# with `command: ["arq", "backend.app.workers.main.WorkerSettings"]`.
# Migrations run via the entrypoint before either command starts.
EXPOSE 8100
ENTRYPOINT ["ship-entrypoint"]
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8100"]
