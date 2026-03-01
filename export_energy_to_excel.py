#!/usr/bin/env python3
"""Export Home Assistant daily energy statistics to an Excel file."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Dict, Iterable
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = value


@dataclass(frozen=True)
class MetricConfig:
    key: str
    column: str
    statistic_id: str


DEFAULT_METRICS = (
    MetricConfig("solar_production", "solar_production_kwh", "sensor.inverter_total_yield"),
    MetricConfig("house_consumption", "house_consumption_kwh", "sensor.consumo_casa_total_energy"),
    MetricConfig("grid_import", "grid_import_kwh", "sensor.power_meter_consumption"),
    MetricConfig("grid_export", "grid_export_kwh", "sensor.power_meter_exported"),
)


class HomeAssistantClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(self, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        method = "GET"
        cmd = [
            "curl",
            "-sS",
            "-X",
            method,
            "-H",
            f"Authorization: Bearer {self.token}",
            "-H",
            "Content-Type: application/json",
            "-w",
            "\n%{http_code}",
            url,
        ]
        if payload is not None:
            method = "POST"
            cmd[3] = method
            cmd.extend(["--data", json.dumps(payload)])

        try:
            proc = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=90)
        except OSError as exc:
            raise RuntimeError("curl is required but was not found on PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Request timed out calling {path}") from exc

        if proc.returncode != 0:
            raise RuntimeError(f"curl failed for {path}: {proc.stderr.strip() or proc.stdout.strip()}")

        try:
            body, status_text = proc.stdout.rsplit("\n", 1)
            status_code = int(status_text.strip())
        except ValueError as exc:
            raise RuntimeError(f"Unexpected response from curl for {path}: {proc.stdout[:300]}") from exc

        if status_code < 200 or status_code >= 300:
            raise RuntimeError(f"Home Assistant API error {status_code} at {path}: {body.strip()}")

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON from {path}: {body[:300]}") from exc

    def get_config(self) -> dict:
        return self._request("/api/config")

    def get_daily_changes(
        self,
        statistic_ids: Iterable[str],
        start_date: date,
        end_date: date,
    ) -> dict:
        # Recorder's end_time is exclusive, so query until midnight of the day after end_date.
        end_exclusive = end_date + timedelta(days=1)
        payload = {
            "start_time": datetime.combine(start_date, time.min).strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": datetime.combine(end_exclusive, time.min).strftime("%Y-%m-%d %H:%M:%S"),
            "statistic_ids": list(statistic_ids),
            "period": "day",
            "types": ["change"],
        }
        response = self._request("/api/services/recorder/get_statistics?return_response", payload)

        try:
            return response["service_response"]["statistics"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError(f"Unexpected recorder response format: {response}") from exc


def parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date '{value}', expected YYYY-MM-DD") from exc


def build_rows(
    stats: dict,
    tz: ZoneInfo,
    start_date: date,
    end_date: date,
    metrics: Iterable[MetricConfig],
) -> list[dict]:
    per_metric: dict[str, Dict[date, float]] = {}

    for metric in metrics:
        day_values: Dict[date, float] = {}
        for row in stats.get(metric.statistic_id, []):
            raw_start = row.get("start")
            if raw_start is None:
                continue

            start_dt = datetime.fromisoformat(raw_start)
            local_day = start_dt.astimezone(tz).date()
            if not (start_date <= local_day <= end_date):
                continue

            day_values[local_day] = float(row.get("change") or 0.0)

        per_metric[metric.key] = day_values

    rows: list[dict] = []
    day = start_date
    while day <= end_date:
        solar = per_metric["solar_production"].get(day, 0.0)
        house = per_metric["house_consumption"].get(day, 0.0)
        grid_import = per_metric["grid_import"].get(day, 0.0)
        grid_export = per_metric["grid_export"].get(day, 0.0)
        self_consumed = max(solar - grid_export, 0.0)

        rows.append(
            {
                "date": day.isoformat(),
                "solar_production_kwh": round(solar, 3),
                "house_consumption_kwh": round(house, 3),
                "self_consumed_kwh": round(self_consumed, 3),
                "grid_import_kwh": round(grid_import, 3),
                "grid_export_kwh": round(grid_export, 3),
            }
        )
        day += timedelta(days=1)

    return rows


def write_excel(rows: list[dict], output_path: str) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "daily_energy"

    headers = [
        "date",
        "solar_production_kwh",
        "house_consumption_kwh",
        "self_consumed_kwh",
        "grid_import_kwh",
        "grid_export_kwh",
    ]
    ws.append(headers)

    for row in rows:
        ws.append([row[h] for h in headers])

    bold = Font(bold=True)
    for cell in ws[1]:
        cell.font = bold
        cell.alignment = Alignment(horizontal="center")

    for idx, width in enumerate((14, 22, 24, 20, 18, 18), start=1):
        ws.column_dimensions[chr(64 + idx)].width = width

    wb.save(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("HASS_URL"), help="Home Assistant URL")
    parser.add_argument("--token", default=os.getenv("HASS_TOKEN"), help="Home Assistant long-lived token")
    parser.add_argument("--start", type=parse_day, default=date(2024, 1, 1), help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=parse_day, default=date.today(), help="End date (YYYY-MM-DD)")
    parser.add_argument("--output", default="homeassistant_energy_export.xlsx", help="Output .xlsx path")

    parser.add_argument("--solar-stat", default=DEFAULT_METRICS[0].statistic_id, help="Statistic ID for solar production")
    parser.add_argument(
        "--consumption-stat",
        default=DEFAULT_METRICS[1].statistic_id,
        help="Statistic ID for total house consumption",
    )
    parser.add_argument("--grid-import-stat", default=DEFAULT_METRICS[2].statistic_id, help="Statistic ID for grid import")
    parser.add_argument("--grid-export-stat", default=DEFAULT_METRICS[3].statistic_id, help="Statistic ID for grid export")

    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    if not args.base_url:
        print("Missing Home Assistant URL. Set --base-url or HASS_URL.", file=sys.stderr)
        return 2
    if not args.token:
        print("Missing Home Assistant token. Set --token or HASS_TOKEN.", file=sys.stderr)
        return 2
    if args.start > args.end:
        print("--start must be <= --end.", file=sys.stderr)
        return 2

    metrics = (
        MetricConfig("solar_production", "solar_production_kwh", args.solar_stat),
        MetricConfig("house_consumption", "house_consumption_kwh", args.consumption_stat),
        MetricConfig("grid_import", "grid_import_kwh", args.grid_import_stat),
        MetricConfig("grid_export", "grid_export_kwh", args.grid_export_stat),
    )

    client = HomeAssistantClient(args.base_url, args.token)
    config = client.get_config()

    timezone_name = config.get("time_zone", "UTC")
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")

    stats = client.get_daily_changes([m.statistic_id for m in metrics], args.start, args.end)
    rows = build_rows(stats, tz, args.start, args.end, metrics)
    write_excel(rows, args.output)

    print(f"Wrote {len(rows)} daily rows to {args.output}")
    print("Using statistics IDs:")
    for metric in metrics:
        print(f"  {metric.column}: {metric.statistic_id}")

    missing = [m.statistic_id for m in metrics if not stats.get(m.statistic_id)]
    if missing:
        print("Warning: no recorder statistics returned for:", file=sys.stderr)
        for statistic_id in missing:
            print(f"  {statistic_id}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
