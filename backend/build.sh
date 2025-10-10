#!/usr/bin/env bash
# filepath: backend/build.sh
set -o errexit

echo "Installing dependencies..."
cd EnvisionBackend
pip install -r requirements.txt

echo "Collecting static files..."
python manage.py collectstatic --no-input

echo "Build completed! Migrations will run on startup."