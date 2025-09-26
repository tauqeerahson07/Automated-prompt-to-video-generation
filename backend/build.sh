#!/usr/bin/env bash
set -o errexit

pip install -r EnvisionBackend/requirements.txt
python EnvisionBackend/manage.py collectstatic --no-input
python EnvisionBackend/manage.py migrate