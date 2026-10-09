#!/usr/bin/env bash
# Exit on error
set -o errexit

# Run migrations before starting the web server
python manage.py migrate

# Ensure static files are collected if missing
python manage.py collectstatic --no-input

# Start Gunicorn WSGI server bound to Render port
PORT="${PORT:-8000}"
exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT}"
