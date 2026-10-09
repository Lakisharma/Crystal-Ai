#!/usr/bin/env bash
# Exit on error
set -o errexit

# Install production dependencies
pip install -r requirements.txt

# Collect static files with WhiteNoise
python manage.py collectstatic --no-input --clear

# Apply database migrations
python manage.py migrate
