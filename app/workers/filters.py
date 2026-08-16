"""Reine, unit-testbare Filterlogik: Endungen, Dateinamens-/Betreff-Keywords, Datumsbereich.

Alle Funktionen sind bewusst frei von DB-/Graph-Zugriffen, damit sie ohne Infrastruktur
getestet werden können (siehe tests/unit/test_filters.py).
"""
from __future__ import annotations

from datetime import date, datetime


def normalize_extension(filename_or_ext: str) -> str:
    """Liefert die Dateiendung lowercased mit führendem Punkt.

    Akzeptiert sowohl reine Endungen (".PDF", "pdf") als auch volle Dateinamen
    ("Rechnung.PDF" -> ".pdf"; nur die letzte Endung zählt, auch bei "archiv.tar.gz" -> ".gz").
    """
    value = filename_or_ext.strip().lower()
    tail = value.rsplit(".", 1)[-1] if "." in value else value
    return "." + tail


def extension_matches(filename: str, allowed_extensions: set[str]) -> bool:
    """True, wenn die Dateiendung von `filename` in `allowed_extensions` enthalten ist.
    Eine leere Menge erlaubter Endungen matcht nichts (Endungsfilter ist Pflicht pro Job)."""
    if not allowed_extensions:
        return False
    if "." not in filename:
        return False
    ext = "." + filename.rsplit(".", 1)[-1].lower()
    normalized_allowed = {normalize_extension(e) for e in allowed_extensions}
    return ext in normalized_allowed


def keyword_matches(text: str | None, keywords: list[str]) -> bool:
    """Case-insensitive Teilstring-Suche: True, wenn IRGENDEIN Keyword in `text` vorkommt.
    Leere Keyword-Liste bedeutet 'keine Keywords definiert' -> False (nichts kann matchen)."""
    if not keywords or not text:
        return False
    haystack = text.lower()
    return any(kw.lower() in haystack for kw in keywords if kw)


def date_in_range(received_at: datetime | date | None, date_from: date | None, date_to: date | None) -> bool:
    """Beide Grenzen sind inklusiv und optional (None = unbegrenzt)."""
    if received_at is None:
        return False
    received_date = received_at.date() if isinstance(received_at, datetime) else received_at
    if date_from and received_date < date_from:
        return False
    if date_to and received_date > date_to:
        return False
    return True


def attachment_matches(
    *,
    attachment_filename: str,
    email_subject: str | None,
    allowed_extensions: set[str],
    keywords: list[str],
) -> bool:
    """Ein Anhang wird heruntergeladen, wenn seine Endung erlaubt ist UND KEIN Ausschluss-Keyword
    im Betreff oder im Anhang-Dateinamen vorkommt (case-insensitive).

    Die Keywords sind ein AUSSCHLUSS-Filter, kein Einschluss-Filter: z.B. "Avis" oder
    "Zahlungserinnerung" eingetragen bedeutet "Anhänge mit diesen Begriffen NICHT herunterladen",
    nicht "nur Anhänge mit diesen Begriffen herunterladen". Keine Keywords definiert = nichts
    wird ausgeschlossen, es zählt nur der Endungsfilter.
    """
    if not extension_matches(attachment_filename, allowed_extensions):
        return False
    if not keywords:
        return True
    excluded = keyword_matches(email_subject, keywords) or keyword_matches(attachment_filename, keywords)
    return not excluded
