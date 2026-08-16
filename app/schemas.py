"""Kleine Hilfsfunktionen zum Parsen von Formulareingaben aus der Web-UI.

Für dieses interne Admin-Tool wird bewusst auf einen vollen Satz Pydantic-Request-Schemas
verzichtet - die Router validieren direkt über FastAPI-`Form(...)`-Parameter; diese Datei bündelt
nur die Parsing-Logik, die von mehreren Routern gebraucht wird.
"""
from __future__ import annotations

from datetime import date

from app.workers.filters import normalize_extension


def parse_date_de(raw: str) -> date | None:
    """Parst ein Datum im deutschen Format TT.MM.JJJJ aus dem eigenen (nicht-nativen)
    Datumsfeld (siehe app/web/static/date-input.js). Leerer String -> None (unbegrenzt).
    Ungültige Eingaben werden defensiv als None behandelt statt einen 500er zu werfen."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        day, month, year = raw.split(".")
        return date(int(year), int(month), int(day))
    except (ValueError, IndexError):
        return None


def parse_extensions(raw: str) -> list[str]:
    """"'.pdf, .docx pdf' -> ['.pdf', '.docx'] (dedupliziert, normalisiert, leere Einträge raus)."""
    parts = [p for p in raw.replace(",", " ").split() if p]
    seen: dict[str, None] = {}
    for part in parts:
        seen.setdefault(normalize_extension(part), None)
    return list(seen.keys())


def parse_keywords(raw: str) -> list[tuple[str, str]]:
    """'Avis, Zahlungserinnerung' -> [("avis", "Avis"), ("zahlungserinnerung", "Zahlungserinnerung")].

    Gibt (normalisiert, Original-Anzeige) zurück; Duplikate (case-insensitive) werden entfernt,
    wobei die erste Schreibweise erhalten bleibt.
    """
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    seen: dict[str, str] = {}
    for part in parts:
        key = part.lower()
        seen.setdefault(key, part)
    return list(seen.items())
