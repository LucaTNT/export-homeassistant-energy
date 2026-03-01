#!/usr/bin/env python3
"""Simple non-root daily scheduler for sync_energy_to_sqlite.py."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timedelta


def next_run(now: datetime, hour: int, minute: int) -> datetime:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def main() -> int:
    hour = int(os.getenv("SYNC_HOUR", "1"))
    minute = int(os.getenv("SYNC_MINUTE", "0"))

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise SystemExit("SYNC_HOUR must be 0-23 and SYNC_MINUTE must be 0-59")

    while True:
        now = datetime.now()
        target = next_run(now, hour, minute)
        wait_seconds = max(1, int((target - now).total_seconds()))
        print(f"[{now.isoformat(timespec='seconds')}] next sync at {target.isoformat(timespec='seconds')}", flush=True)
        time.sleep(wait_seconds)

        run_at = datetime.now().isoformat(timespec="seconds")
        print(f"[{run_at}] running scheduled sync", flush=True)
        proc = subprocess.run(["/app/docker-scripts/run_sync.sh"], check=False)
        print(f"[{datetime.now().isoformat(timespec='seconds')}] sync exit code: {proc.returncode}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
