#!/bin/sh
set -e

echo "Iniciando Tickets Service..."

# Esperar PostgreSQL
echo "Esperando base de datos en ${DB_HOST:-localhost}:${DB_PORT:-5432}..."
retries=0
while ! nc -z "${DB_HOST:-localhost}" "${DB_PORT:-5432}"; do
    retries=$((retries+1))
    if [ "$retries" -ge 30 ]; then
        echo "Base de datos no disponible después de 30 intentos"
        exit 1
    fi
    echo "Intento $retries/30..."
    sleep 2
done
echo "Base de datos disponible"

# Aplicar migraciones Alembic
# PYTHONPATH se desactiva para evitar que /app/alembic/ tape al paquete alembic del venv
echo "Aplicando migraciones..."
env -u PYTHONPATH alembic upgrade head
echo "Migraciones completadas"

# Iniciar servidor
echo "Iniciando servidor..."
exec gunicorn app.main:app \
    --workers "${UVICORN_WORKERS:-2}" \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind "0.0.0.0:8000" \
    --log-level "${LOG_LEVEL:-info}" \
    --forwarded-allow-ips "${UVICORN_FORWARDED_ALLOW_IPS:-*}"
