#!/bin/sh
set -e

# Wait for the database to accept connections
echo "Waiting for database at ${DB_HOST}:${DB_PORT}..."
until python -c "
import socket, sys, os
try:
    s = socket.create_connection(
        (os.environ.get('DB_HOST', 'localhost'), int(os.environ.get('DB_PORT', 3306))),
        timeout=2
    )
    s.close()
except OSError:
    sys.exit(1)
"; do
    echo "  database not ready, retrying in 3s..."
    sleep 3
done
echo "Database is ready."

# Apply migrations
echo "Running migrations..."
python manage.py migrate --noinput

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput --clear

# Create superuser if credentials are provided via environment variables
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ] && [ -n "$DJANGO_SUPERUSER_EMAIL" ]; then
    echo "Creating superuser..."
    python manage.py createsuperuser \
        --noinput \
        --username "$DJANGO_SUPERUSER_USERNAME" \
        --email "$DJANGO_SUPERUSER_EMAIL" || echo "Superuser already exists, skipping."
fi

# Hand off to the container CMD (gunicorn)
exec "$@"
