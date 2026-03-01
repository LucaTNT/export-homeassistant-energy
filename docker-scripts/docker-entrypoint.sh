#!/bin/sh
set -eu

if [ "${RUN_ON_STARTUP:-0}" = "1" ]; then
  /app/docker-scripts/run_sync.sh || true
fi

exec /usr/local/bin/python /app/docker-scripts/scheduler_loop.py
