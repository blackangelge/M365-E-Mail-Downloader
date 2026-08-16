"""Gemeinsame Zeitraum-Berechnung (heute/gestern/diese Woche/letzte Woche) für Dashboard und Kalender.

Grenzen werden in der konfigurierten APP_TIMEZONE berechnet, dann als tz-aware Datetimes für die
Postgres-Abfrage verwendet (Woche beginnt Montag, wie in DACH üblich).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def period_bounds(tz_name: str) -> dict[str, datetime]:
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    week_start = today_start - timedelta(days=today_start.weekday())  # Montag
    last_week_start = week_start - timedelta(days=7)
    return {
        "today_start": today_start,
        "yesterday_start": yesterday_start,
        "week_start": week_start,
        "last_week_start": last_week_start,
    }
