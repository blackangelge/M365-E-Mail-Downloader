"""Fehlerklassifikation für Graph-API-Aufrufe.

Permanente Fehler (ungültige/entzogene Zugangsdaten, fehlende Berechtigung) werden NICHT von
Procrastinate erneut versucht - sie sollen sofort sichtbar werden statt bis zu 8x wiederholt zu
werden. Transiente Fehler (Netzwerk, 5xx, erschöpftes Throttling) laufen durch die normale
Retry-Strategie der Tasks (siehe app/workers/tasks.py).
"""
from __future__ import annotations

from azure.core.exceptions import ClientAuthenticationError
from kiota_abstractions.api_error import APIError

_PERMANENT_STATUS_CODES = {401, 403}


class GraphTaskError(Exception):
    """Basisklasse für alle Fehler, die aus der Graph-Anbindung an die Task-Schicht durchgereicht werden."""


class TransientGraphError(GraphTaskError):
    """Netzwerkfehler, 5xx, Throttling - Procrastinate soll den Task erneut versuchen."""


class PermanentGraphError(GraphTaskError):
    """Ungültige Zugangsdaten / fehlende Berechtigung - kein weiteres Retry, Status auf 'error' setzen."""


def classify_and_wrap(exc: Exception) -> GraphTaskError:
    """Wandelt eine von der Graph-SDK/azure-identity geworfene Exception in eine unserer beiden
    Kategorien um."""
    # azure-identity wirft ClientAuthenticationError bereits bei der Token-Beschaffung (also VOR
    # dem eigentlichen Graph-Request) - z.B. bei falscher Tenant-ID, ungültigem/abgelaufenem
    # Client-Secret oder Zertifikat. Das ist per Definition permanent: ein Retry mit denselben
    # Zugangsdaten kann nie erfolgreich sein.
    if isinstance(exc, ClientAuthenticationError):
        return PermanentGraphError(f"Authentifizierung fehlgeschlagen (ungültige Zugangsdaten?): {exc}")

    if isinstance(exc, APIError):
        status_code = getattr(exc, "response_status_code", None)
        if status_code in _PERMANENT_STATUS_CODES:
            return PermanentGraphError(f"Graph-API meldet Berechtigungsfehler ({status_code}): {exc}")
        return TransientGraphError(f"Graph-API-Fehler (Status {status_code}): {exc}")

    # Alles andere (Netzwerk-Timeouts, Verbindungsfehler, ...) wird als transient behandelt,
    # damit Procrastinate es erneut versucht.
    return TransientGraphError(f"Unerwarteter Fehler beim Graph-Zugriff: {exc}")
