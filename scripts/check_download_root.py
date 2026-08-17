"""Prüft beim Container-Start, dass DOWNLOAD_ROOT von einem Host-Verzeichnis gemountet UND für
den aktuellen Prozess-User beschreibbar ist. Wird von entrypoint.sh NACH der PUID/PGID-Anpassung
als appuser ausgeführt, damit das Ergebnis wirklich aussagekräftig ist (als root liefe der
Schreibtest immer erfolgreich, egal ob appuser tatsächlich Rechte hätte)."""
from __future__ import annotations

import os
import sys

from app.config import get_settings

root = get_settings().download_root

try:
    root.mkdir(parents=True, exist_ok=True)
    probe = root / ".write_test"
    probe.write_text("ok")
    probe.unlink()
except PermissionError as exc:
    sys.exit(
        f"[entrypoint] ABBRUCH: {root} ist nicht beschreibbar ({exc}). Aktueller Container-User: "
        f"UID:GID {os.getuid()}:{os.getgid()}. Setze PUID/PGID in .env auf die UID/GID, der der "
        f"Host-Ordner (DOWNLOAD_HOST_DIR) bereits gehört (auf dem Host z.B. mit 'id -u'/'id -g' "
        f"ermitteln), dann den Container neu erstellen (docker compose up -d)."
    )

if os.stat(root).st_dev == os.stat(root.parent).st_dev:
    sys.exit(
        f"[entrypoint] ABBRUCH: {root} scheint NICHT von einem Host-Verzeichnis gemountet zu "
        f"sein (gleiche Geräte-ID wie {root.parent} - vermutlich nur der leere Ordner aus dem "
        f"Image). Downloads würden sonst beim nächsten Neustart kommentarlos verloren gehen. "
        f"Prüfe DOWNLOAD_HOST_DIR in .env: der Zielordner muss auf dem Host existieren, bevor "
        f"der Container gestartet wird."
    )

print(f"[entrypoint] {root} ist korrekt vom Host gemountet und beschreibbar.")
