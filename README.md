# Home Assistant Energy Export (Excel + SQLite)

Exports day-by-day energy data from Home Assistant into an `.xlsx` file with:

- `solar_production_kwh`
- `house_consumption_kwh`
- `self_consumed_kwh`
- `grid_import_kwh`
- `grid_export_kwh`

`self_consumed_kwh` is computed as:

`solar_production_kwh - grid_export_kwh` (clamped at 0)

The script automatically loads `HASS_URL` and `HASS_TOKEN` from `.env` on startup.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Run

Fill `.env` with your Home Assistant URL and long-lived token:

`HASS_URL=https://your-home-assistant-url`

`HASS_TOKEN=your_long_lived_token`

Run the exporter:

```bash
python3 export_energy_to_excel.py \
  --start 2026-01-01 \
  --end 2026-02-28 \
  --output energy_daily.xlsx
```

Default statistic IDs (already set in the script):

- Solar production: `sensor.inverter_total_yield`
- House consumption: `sensor.consumo_casa_total_energy`
- Grid import: `sensor.power_meter_consumption`
- Grid export: `sensor.power_meter_exported`

Override them if needed:

```bash
python3 export_energy_to_excel.py \
  --solar-stat sensor.some_other_solar_total \
  --consumption-stat sensor.some_house_total \
  --grid-import-stat sensor.grid_import_total \
  --grid-export-stat sensor.grid_export_total
```

## Scheduled SQLite Sync

Use `sync_energy_to_sqlite.py` for recurring jobs. It always syncs up to the **previous day** (today is skipped because it may be incomplete).

Default start behavior:

- If the SQLite table already has data, sync starts from `max(date) + 1 day` (incremental).
- If the table is empty/missing, sync starts from `ENERGY_SYNC_START_DATE` (or `2024-01-01`).
- `--start` forces a specific start date for backfills.

Set these optional `.env` values:

- `ENERGY_SYNC_START_DATE=2025-01-01`
- `ENERGY_SQLITE_DB=energy_daily.sqlite`
- `ENERGY_SQLITE_TABLE=daily_energy`
- `HEALTHCHECKS_PING_URL=https://hc-ping.com/<uuid>` (optional monitoring)

Run once manually:

```bash
python3 sync_energy_to_sqlite.py
```

Or override values from CLI:

```bash
python3 sync_energy_to_sqlite.py \
  --start 2025-11-20 \
  --db-path energy_daily.sqlite \
  --table daily_energy
```

SQLite schema columns match the Excel export:

- `date` (primary key)
- `solar_production_kwh`
- `house_consumption_kwh`
- `self_consumed_kwh`
- `grid_import_kwh`
- `grid_export_kwh`

Healthchecks behavior (when `HEALTHCHECKS_PING_URL` is set):

- Sends `<url>/start` at sync start.
- Sends `<url>` on success with script output in the request body.
- Sends `<url>/fail` on failure with script output in the request body.

Example cron (daily at 01:15):

```bash
15 1 * * * cd /path/to/export-homeassistant-energy && /path/to/export-homeassistant-energy/.venv/bin/python sync_energy_to_sqlite.py >> sync.log 2>&1
```

## Docker (Daily Sync at 01:00)

The Docker image is Alpine-based (`python:3.12-alpine`) to keep size lower.

Build image:

```bash
docker build -t ha-energy-sync .
```

Run container (non-root, sync every day at 01:00):

```bash
docker run -d \
  --name ha-energy-sync \
  --restart unless-stopped \
  --env-file .env \
  -v $(pwd)/data:/data \
  ha-energy-sync
```

Notes:

- Container runs as unprivileged user `appuser` (uid `10001`).
- Default schedule is daily at `01:00`. Override with `-e SYNC_HOUR=2 -e SYNC_MINUTE=30`.
- SQLite DB defaults to `/data/energy_daily.sqlite` in container (`ENERGY_SQLITE_DB`).
- Use `-e RUN_ON_STARTUP=1` to execute one sync immediately when container starts.
- Healthchecks also works in Docker via `HEALTHCHECKS_PING_URL` in `.env` or `-e HEALTHCHECKS_PING_URL=...`.
- Logs: `docker logs ha-energy-sync`.
