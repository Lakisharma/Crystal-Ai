#!/usr/bin/env bash
# Exit on error
set -o errexit

# Run migrations before starting the web server
python manage.py migrate

# Ensure static files are collected if missing
python manage.py collectstatic --no-input

# Start Gunicorn WSGI server
gunicorn config.wsgi:application
