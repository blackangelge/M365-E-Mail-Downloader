"""Gemeinsam genutzte Jinja2Templates-Instanz für alle Router."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from app.config import get_settings

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _to_local(value: datetime) -> datetime:
    """Postgres liefert tz-aware UTC-Datetimes (DateTime(timezone=True)) - für die Anzeige müssen
    sie in APP_TIMEZONE umgerechnet werden, sonst zeigt die UI z.B. bei UTC+2 (Sommerzeit) zwei
    Stunden zu früh an. Naive Datetimes werden defensiv als UTC angenommen."""
    tz = ZoneInfo(get_settings().app_timezone)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(tz)


def _format_datetime(value: datetime | None, fmt: str = "%d.%m.%Y %H:%M") -> str:
    if value is None:
        return "-"
    return _to_local(value).strftime(fmt)


def _format_date(value, fmt: str = "%d.%m.%Y") -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        value = _to_local(value)
    return value.strftime(fmt)


templates.env.filters["datetime_de"] = _format_datetime
templates.env.filters["date_de"] = _format_date
