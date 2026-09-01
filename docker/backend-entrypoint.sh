#!/bin/sh
set -eu

echo "Applying database migrations..."
alembic -c app/alembic.ini upgrade head
echo "Database migrations applied successfully."

exec "$@"
