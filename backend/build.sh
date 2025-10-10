#!/usr/bin/env bash
# filepath: backend/build.sh
set -o errexit
# Make sure we're executable
chmod +x "$0"

echo "=== Installing dependencies ==="
pip install -r requirements.txt

echo "=== Changing to Django project directory ==="
cd EnvisionBackend

echo "=== Collecting static files ==="
python manage.py collectstatic --no-input

echo "=== Build completed ==="