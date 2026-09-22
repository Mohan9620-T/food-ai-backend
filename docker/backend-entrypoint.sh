#!/bin/sh
# Linux container entrypoint; keep LF line endings (see .gitattributes).
set -eu

export UVICORN_PORT="${PORT:-${UVICORN_PORT:-8000}}"
case "$UVICORN_PORT" in
    ''|*[!0-9]*) echo "PORT must be an integer between 1 and 65535." >&2; exit 1 ;;
esac
if [ "$UVICORN_PORT" -lt 1 ] || [ "$UVICORN_PORT" -gt 65535 ]; then
    echo "PORT must be an integer between 1 and 65535." >&2
    exit 1
fi

# Validate configuration before the retry loop; never print connection secrets.
python -c "from app.config.settings import DATABASE_URL"
echo "Applying database migrations..."
attempt=1
until alembic -c app/alembic.ini upgrade head; do
    if [ "$attempt" -ge 5 ]; then
        echo "Database migration failed after 5 attempts; check the database service and migration logs." >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    echo "Retrying database migration ($attempt/5)..."
    sleep 2
done
echo "Database migrations applied successfully."

exec "$@"
