#!/usr/bin/env bash
# filepath: build.sh
set -o errexit

pip install -r backend/requirements.txt
cd backend
python manage.py collectstatic --no-input