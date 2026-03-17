#!/usr/bin/env bash
set -euo pipefail

# Defaults (override via env)
: "${APP_HOST:=0.0.0.0}"
: "${APP_PORT:=8000}"
: "${UVICORN_WORKERS:=1}"
: "${RUN_MIGRATIONS:=1}"
: "${ALEMBIC_CONFIG:=/app/apps/api-gateway/alembic.ini}"

export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="${PYTHONPATH:-/app}"

if [ "${RUN_MIGRATIONS}" = "1" ]; then
  if command -v alembic >/dev/null 2>&1; then
    if [ -f "${ALEMBIC_CONFIG}" ]; then
      echo "[entrypoint] Running alembic migrations with ${ALEMBIC_CONFIG} ..."
      alembic -c "${ALEMBIC_CONFIG}" upgrade head
    else
      echo "[entrypoint] ALEMBIC_CONFIG not found at ${ALEMBIC_CONFIG}, skipping migrations"
    fi
  else
    echo "[entrypoint] alembic not installed, skipping migrations"
  fi
fi

# Optional extra uvicorn flags via UVICORN_EXTRA (e.g. "--proxy-headers --forwarded-allow-ips=*")
echo "[entrypoint] Starting uvicorn (factory)"
cd /app/apps/api-gateway
exec uvicorn src.main:create_app \
  --factory --host "${APP_HOST}" --port "${APP_PORT}" \
  ${UVICORN_WORKERS:+--workers "${UVICORN_WORKERS}"} ${UVICORN_EXTRA:-}
