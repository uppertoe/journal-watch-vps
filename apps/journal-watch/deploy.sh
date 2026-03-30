#!/usr/bin/env bash
set -euo pipefail

docker compose up -d jw_postgres
docker compose run --rm --no-deps jw_django python manage.py migrate --noinput
docker compose run --rm --no-deps jw_django python manage.py collectstatic --noinput