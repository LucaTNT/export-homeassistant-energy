#!/usr/bin/env python3
"""Sync Home Assistant daily energy statistics into SQLite for scheduled runs."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from export_energy_to_excel import (
    DEFAULT_METRICS,
    HomeAssistantClient,
    MetricConfig,
    build_rows,
    load_dotenv,
    parse_day,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("HASS_URL"), help="Home Assistant URL")
    parser.add_argument("--token", default=os.getenv("HASS_TOKEN"), help="Home Assistant long-lived token")
    parser.add_argument(
        "--start",
        type=parse_day,
        default=None,
        help="Start date (YYYY-MM-DD). If omitted, sync is incremental from DB max(date)+1.",
    )
    parser.add_argument(
        "--db-path",
        default=os.getenv("ENERGY_SQLITE_DB", "energy_daily.sqlite"),
        help="SQLite database path (default: energy_daily.sqlite)",
    )
    parser.add_argument(
        "--table",
        default=os.getenv("ENERGY_SQLITE_TABLE", "daily_energy"),
        help="SQLite table name (default: daily_energy)",
    )
    parser.add_argument(
        "--healthchecks-url",
        default=os.getenv("HEALTHCHECKS_PING_URL"),
        help="Optional base ping URL (for example https://hc-ping.com/<uuid>)",
    )

    parser.add_argument("--solar-stat", default=DEFAULT_METRICS[0].statistic_id, help="Statistic ID for solar production")
    parser.add_argument(
        "--consumption-stat",
        default=DEFAULT_METRICS[1].statistic_id,
        help="Statistic ID for total house consumption",
    )
    parser.add_argument("--grid-import-stat", default=DEFAULT_METRICS[2].statistic_id, help="Statistic ID for grid import")
    parser.add_argument("--grid-export-stat", default=DEFAULT_METRICS[3].statistic_id, help="Statistic ID for grid export")

    return parser.parse_args()


def ping_healthchecks(base_url: str | None, suffix: str, body: str = "") -> None:
    if not base_url:
        return

    url = base_url.rstrip("/") + suffix
    cmd = [
        "curl",
        "-fsS",
        "--max-time",
        "10",
        "-X",
        "POST",
        "--data-binary",
        "@-",
        "-o",
        "/dev/null",
        url,
    ]
    proc = subprocess.run(cmd, input=body, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"Warning: healthcheck ping failed for {url}: {proc.stderr.strip()}", file=sys.stderr)


def validate_table_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"Invalid table name: {name!r}")
    return name


def ensure_table(conn: sqlite3.Connection, table: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            date TEXT PRIMARY KEY,
            solar_production_kwh REAL NOT NULL,
            house_consumption_kwh REAL NOT NULL,
            self_consumed_kwh REAL NOT NULL,
            grid_import_kwh REAL NOT NULL,
            grid_export_kwh REAL NOT NULL
        )
        """
    )


def get_latest_date(conn: sqlite3.Connection, table: str) -> date | None:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    )
    if cur.fetchone() is None:
        return None

    cur = conn.execute(f"SELECT MAX(date) FROM {table}")
    value = cur.fetchone()[0]
    if not value:
        return None
    return date.fromisoformat(value)


def upsert_rows(conn: sqlite3.Connection, table: str, rows: list[dict]) -> None:
    conn.executemany(
        f"""
        INSERT INTO {table} (
            date,
            solar_production_kwh,
            house_consumption_kwh,
            self_consumed_kwh,
            grid_import_kwh,
            grid_export_kwh
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            solar_production_kwh = excluded.solar_production_kwh,
            house_consumption_kwh = excluded.house_consumption_kwh,
            self_consumed_kwh = excluded.self_consumed_kwh,
            grid_import_kwh = excluded.grid_import_kwh,
            grid_export_kwh = excluded.grid_export_kwh
        """,
        [
            (
                row["date"],
                row["solar_production_kwh"],
                row["house_consumption_kwh"],
                row["self_consumed_kwh"],
                row["grid_import_kwh"],
                row["grid_export_kwh"],
            )
            for row in rows
        ],
    )


def main() -> int:
    load_dotenv()
    args = parse_args()
    healthchecks_url = args.healthchecks_url
    run_log: list[str] = []

    def log(message: str, *, err: bool = False) -> None:
        run_log.append(message)
        print(message, file=sys.stderr if err else sys.stdout)

    ping_healthchecks(healthchecks_url, "/start", "sync started")

    try:
        if not args.base_url:
            log("Missing Home Assistant URL. Set --base-url or HASS_URL.", err=True)
            ping_healthchecks(healthchecks_url, "/fail", "\n".join(run_log))
            return 2
        if not args.token:
            log("Missing Home Assistant token. Set --token or HASS_TOKEN.", err=True)
            ping_healthchecks(healthchecks_url, "/fail", "\n".join(run_log))
            return 2

        try:
            table_name = validate_table_name(args.table)
        except ValueError as exc:
            log(str(exc), err=True)
            ping_healthchecks(healthchecks_url, "/fail", "\n".join(run_log))
            return 2

        try:
            env_start = parse_day(os.getenv("ENERGY_SYNC_START_DATE", "2024-01-01"))
        except argparse.ArgumentTypeError as exc:
            log(f"Invalid ENERGY_SYNC_START_DATE: {exc}", err=True)
            ping_healthchecks(healthchecks_url, "/fail", "\n".join(run_log))
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

        today_local = datetime.now(tz).date()
        end_date = today_local - timedelta(days=1)

        with sqlite3.connect(args.db_path) as conn:
            latest_in_db = get_latest_date(conn, table_name)

            if args.start is not None:
                effective_start = args.start
                start_reason = "--start"
            elif latest_in_db is not None:
                effective_start = latest_in_db + timedelta(days=1)
                start_reason = "db_max_plus_one"
            else:
                effective_start = env_start
                start_reason = "env_default"

            if effective_start > end_date:
                log(
                    f"No sync needed: start date {effective_start.isoformat()} is after previous day {end_date.isoformat()}.",
                    err=True,
                )
                ping_healthchecks(healthchecks_url, "", "\n".join(run_log))
                return 0

            stats = client.get_daily_changes([m.statistic_id for m in metrics], effective_start, end_date)
            rows = build_rows(stats, tz, effective_start, end_date, metrics)

            ensure_table(conn, table_name)
            upsert_rows(conn, table_name, rows)
            conn.commit()

        log(f"Synced {len(rows)} daily rows into {args.db_path} (table: {table_name})")
        log(f"Date range: {effective_start.isoformat()} -> {end_date.isoformat()} ({timezone_name})")
        log(f"Start source: {start_reason}")

        missing = [m.statistic_id for m in metrics if not stats.get(m.statistic_id)]
        if missing:
            log("Warning: no recorder statistics returned for:", err=True)
            for statistic_id in missing:
                log(f"  {statistic_id}", err=True)

        ping_healthchecks(healthchecks_url, "", "\n".join(run_log))
        return 0
    except Exception as exc:
        log(f"Sync failed: {exc}", err=True)
        ping_healthchecks(healthchecks_url, "/fail", "\n".join(run_log))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
