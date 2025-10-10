#!/usr/bin/env bash
# filepath: build.sh
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input