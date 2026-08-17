"""Wartet beim Container-Start, bis Postgres erreichbar ist (bis zu 60s, alle 2s ein Versuch)."""
from __future__ import annotations

import sys
import time

import psycopg

from app.config import get_settings

url = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")

for attempt in range(30):
    try:
        with psycopg.connect(url, connect_timeout=3):
            print("[entrypoint] Postgres erreichbar.")
            sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"[entrypoint] Postgres noch nicht bereit ({attempt + 1}/30): {exc}")
        time.sleep(2)

sys.exit("[entrypoint] Postgres nach 60s nicht erreichbar - Abbruch.")
