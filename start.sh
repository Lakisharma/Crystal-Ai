#!/usr/bin/env bash
# Exit on error
set -o errexit

# Run migrations before starting the web server
python manage.py migrate

# Start Gunicorn WSGI server
gunicorn config.wsgi:application
