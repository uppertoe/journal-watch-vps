#!/usr/bin/env bash
set -euo pipefail

docker compose run --rm jw_django python manage.py migrate
docker compose run --rm jw_django python manage.py collectstatic --noinput