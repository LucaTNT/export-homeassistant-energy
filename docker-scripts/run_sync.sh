#!/bin/sh
set -eu

if [ -f /app/.env ]; then
  set -a
  # shellcheck disable=SC1091
  . /app/.env
  set +a
fi

cd /app
exec /usr/local/bin/python /app/sync_energy_to_sqlite.py "$@"
