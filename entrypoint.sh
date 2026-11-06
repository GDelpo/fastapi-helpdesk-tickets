#!/bin/sh
set -e

echo "Starting Tickets Service..."

# Wait for PostgreSQL
echo "Waiting for database at ${DB_HOST:-localhost}:${DB_PORT:-5432}..."
retries=0
while ! nc -z "${DB_HOST:-localhost}" "${DB_PORT:-5432}"; do
    retries=$((retries+1))
    if [ "$retries" -ge 30 ]; then
        echo "Database not available after 30 attempts"
        exit 1
    fi
    echo "Attempt $retries/30..."
    sleep 2
done
echo "Database available"

# Apply Alembic migrations
# INTENTIONAL: PYTHONPATH is unset so /app/alembic/ does not shadow the venv's alembic package
echo "Applying migrations..."
env -u PYTHONPATH alembic upgrade head
echo "Migrations complete"

# Start the server
echo "Starting server..."
exec gunicorn app.main:app \
    --workers "${UVICORN_WORKERS:-2}" \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind "0.0.0.0:8000" \
    --log-level "${LOG_LEVEL:-info}" \
    --forwarded-allow-ips "${UVICORN_FORWARDED_ALLOW_IPS:-*}"
