# Home Assistant Energy Export (Excel)

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
