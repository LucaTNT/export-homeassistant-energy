FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RUN_ON_STARTUP=0 \
    SYNC_HOUR=1 \
    SYNC_MINUTE=0 \
    ENERGY_SQLITE_DB=/data/energy_daily.sqlite

WORKDIR /app

RUN apk add --no-cache curl ca-certificates

RUN addgroup -S appgroup \
    && adduser -S -D -H -u 10001 -G appgroup appuser

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY export_energy_to_excel.py sync_energy_to_sqlite.py ./
COPY docker-scripts/ /app/docker-scripts/
RUN chmod +x /app/docker-scripts/docker-entrypoint.sh /app/docker-scripts/run_sync.sh /app/docker-scripts/scheduler_loop.py

RUN mkdir -p /data \
    && chown -R appuser:appgroup /app /data

VOLUME ["/data"]

USER appuser

ENTRYPOINT ["/app/docker-scripts/docker-entrypoint.sh"]
