"""Kleine Hilfsfunktionen zum Parsen von Formulareingaben aus der Web-UI.

Für dieses interne Admin-Tool wird bewusst auf einen vollen Satz Pydantic-Request-Schemas
verzichtet - die Router validieren direkt über FastAPI-`Form(...)`-Parameter; diese Datei bündelt
nur die Parsing-Logik, die von mehreren Routern gebraucht wird.
"""
from __future__ import annotations

import re
from datetime import date

from app.workers.filters import normalize_extension

# Trennzeichen für Keyword-Listen: Komma ODER Zeilenumbruch (die Textarea erlaubt beides -
# Nutzer können frei zwischen "eins pro Zeile" und kommagetrennt wählen oder mischen).
_KEYWORD_SPLIT_RE = re.compile(r"[,\n\r]+")


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
    """'Avis, Zahlungserinnerung\\nMahnung' -> [("avis", "Avis"), ("zahlungserinnerung", "Zahlungserinnerung"),
    ("mahnung", "Mahnung")].

    Trennt sowohl auf Komma als auch auf Zeilenumbruch (die Textarea erlaubt beides). Führende/
    nachgestellte Leerzeichen um jeden Eintrag werden entfernt, mehrfache Leerzeichen INNERHALB
    eines Eintrags (z.B. "Zahlungs  erinnerung") auf ein einzelnes reduziert. Gibt
    (normalisiert, Original-Anzeige) zurück; Duplikate (case-insensitive) werden entfernt, wobei
    die erste Schreibweise erhalten bleibt.
    """
    parts = [re.sub(r"\s+", " ", p).strip() for p in _KEYWORD_SPLIT_RE.split(raw)]
    parts = [p for p in parts if p]
    seen: dict[str, str] = {}
    for part in parts:
        key = part.lower()
        seen.setdefault(key, part)
    return list(seen.items())
