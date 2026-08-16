#!/usr/bin/env bash
set -euo pipefail

echo "[entrypoint] Prüfe DOWNLOAD_ROOT-Mount..."
# Das Dockerfile legt DOWNLOAD_ROOT bereits im Image an (Sicherheitsnetz für den Fall, dass
# überhaupt kein Bind-Mount konfiguriert ist). Das hat aber einen gefährlichen Nebeneffekt: legt
# man DOWNLOAD_HOST_DIR (siehe docker-compose.yml) auf einen Host-Pfad, der (noch) nicht
# existiert, mountet Docker auf manchen Setups (u.a. Docker Desktop unter Windows) NICHT auf den
# Host durch - es bleibt einfach das leere Verzeichnis aus dem Image übrig. Die App würde dann
# scheinbar erfolgreich Dateien schreiben, die aber nur in der Container-Schreibschicht landen
# und beim nächsten Neustart kommentarlos verloren gehen. Diese Prüfung bricht deshalb LAUT ab,
# statt das still geschehen zu lassen: ein echter Bind-Mount hat immer eine andere Geräte-ID
# (st_dev) als sein Elternverzeichnis (das noch im Image-Layer liegt); sind beide gleich, wurde
# nichts vom Host eingehängt.
python - <<'PYEOF'
import os
import sys

from app.config import get_settings

root = get_settings().download_root
root.mkdir(parents=True, exist_ok=True)

if os.stat(root).st_dev == os.stat(root.parent).st_dev:
    sys.exit(
        f"[entrypoint] ABBRUCH: {root} scheint NICHT von einem Host-Verzeichnis gemountet zu "
        f"sein (gleiche Geräte-ID wie {root.parent} - vermutlich nur der leere Ordner aus dem "
        f"Image). Downloads würden sonst beim nächsten Neustart kommentarlos verloren gehen. "
        f"Prüfe DOWNLOAD_HOST_DIR in .env: der Zielordner muss auf dem Host existieren, bevor "
        f"der Container gestartet wird."
    )
print(f"[entrypoint] {root} ist korrekt vom Host gemountet.")
PYEOF

echo "[entrypoint] Warte auf Postgres..."
python - <<'PYEOF'
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
PYEOF

echo "[entrypoint] Wende Alembic-Migrationen an (App-Tabellen)..."
alembic upgrade head

echo "[entrypoint] Prüfe Procrastinate-Schema..."
# "procrastinate schema --apply" ist laut CLI-Hilfe explizit NUR für eine leere Datenbank gedacht
# ("This won't work if the schema has already been applied") - anders als ursprünglich
# angenommen NICHT idempotent. Bei jedem echten Container-Neustart (nicht nur Uvicorn-Reload)
# würde ein erneuter Aufruf sonst mit "type ... already exists" abbrechen und den Container in
# eine Restart-Schleife schicken - das widerspricht direkt der Anforderung, dass die App nach
# einem Neustart automatisch weiterarbeitet. Deshalb wird hier explizit geprüft, ob das Schema
# schon existiert (Sentinel-Tabelle "procrastinate_jobs"), bevor "schema --apply" aufgerufen wird.
SCHEMA_EXISTS=$(python - <<'PYEOF'
import psycopg
from app.config import get_settings

url = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
with psycopg.connect(url) as conn, conn.cursor() as cur:
    cur.execute("SELECT to_regclass('public.procrastinate_jobs')")
    print("yes" if cur.fetchone()[0] else "no")
PYEOF
)

if [ "$SCHEMA_EXISTS" = "yes" ]; then
    echo "[entrypoint] Procrastinate-Schema bereits vorhanden, überspringe schema --apply."
else
    echo "[entrypoint] Wende Procrastinate-Schema erstmalig an..."
    # "python -m procrastinate" statt des "procrastinate"-Konsolenskripts: nur so landet das
    # Arbeitsverzeichnis (/app, enthaelt das Package "app") automatisch im sys.path, damit
    # der dotted Pfad "app.workers.procrastinate_app.app" aufloesbar ist.
    python -m procrastinate --app=app.workers.procrastinate_app.app schema --apply
fi

echo "[entrypoint] Starte Anwendung..."
exec python -m app.run
