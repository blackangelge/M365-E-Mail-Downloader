#!/usr/bin/env bash
set -euo pipefail

# --- Container-User an den Besitzer der gemounteten Host-Ordner anpassen -----------------------
# Einfachere Docker-Projekte laufen oft als root im Container und haben deshalb nie
# Berechtigungsprobleme mit Bind-Mounts. Dieses Projekt startet bewusst NICHT als root (Security
# Best Practice) - dafür muss die UID/GID des Container-Users zu der des Host-Ordners passen,
# sonst schlägt jeder Schreibversuch auf nativem Linux-Docker mit "Permission denied" fehl
# (anders als z.B. auf Docker Desktop unter Windows/Mac, das das meist transparent überbrückt).
#
# Standardmäßig wird das automatisch erkannt: der tatsächliche Besitzer von DOWNLOAD_ROOT (also
# des gemounteten Host-Ordners DOWNLOAD_HOST_DIR) wird per "stat" ausgelesen und übernommen - kein
# manuelles Nachschlagen von UID/GID nötig. Gehört der Ordner ausnahmsweise root (z.B. weil er
# noch gar nicht existierte und frisch angelegt wurde), weicht der Container auf UID/GID 1000 aus
# und übereignet sich NUR die oberste Ordnerebene (nicht rekursiv - der Ordner kann bereits viele
# vorhandene Dateien mit anderem Besitzer enthalten, die dabei nicht angefasst werden sollen).
# PUID/PGID in .env setzen, um das für Sonderfälle (z.B. Netzlaufwerk) manuell zu überschreiben -
# gesetzte Werte haben immer Vorrang vor der Auto-Erkennung.
DOWNLOAD_ROOT="${DOWNLOAD_ROOT:-/data/Download}"
PUID="${PUID:-}"
PGID="${PGID:-}"

if [ -z "$PUID" ] || [ -z "$PGID" ]; then
    mkdir -p "$DOWNLOAD_ROOT"
    DETECTED_UID="$(stat -c '%u' "$DOWNLOAD_ROOT")"
    DETECTED_GID="$(stat -c '%g' "$DOWNLOAD_ROOT")"

    if [ "$DETECTED_UID" = "0" ]; then
        echo "[entrypoint] $DOWNLOAD_ROOT gehört root - weiche auf UID/GID 1000 aus und übereigne die oberste Ordnerebene."
        DETECTED_UID=1000
        DETECTED_GID=1000
        chown 1000:1000 "$DOWNLOAD_ROOT"
    fi

    PUID="${PUID:-$DETECTED_UID}"
    PGID="${PGID:-$DETECTED_GID}"
    echo "[entrypoint] Automatisch erkannt: PUID=$PUID PGID=$PGID (Besitzer von $DOWNLOAD_ROOT)."
else
    echo "[entrypoint] PUID/PGID manuell gesetzt: PUID=$PUID PGID=$PGID."
fi

if [ "$(id -u appuser)" != "$PUID" ]; then
    echo "[entrypoint] Passe UID von appuser an PUID=$PUID an..."
    usermod -o -u "$PUID" appuser
fi
if [ "$(id -g appuser)" != "$PGID" ]; then
    echo "[entrypoint] Passe GID von appuser an PGID=$PGID an..."
    groupmod -o -g "$PGID" appuser
fi
# Nur die (kleinen) Container-eigenen Verzeichnisse - NICHT rekursiv den ggf. riesigen
# Download-Ordner, dessen Rechte ja bewusst schon zum Host passen sollen.
chown appuser:appuser /app /home/appuser

run_as_app() {
    su appuser -s /bin/bash -c "$*"
}

echo "[entrypoint] Prüfe DOWNLOAD_ROOT-Mount..."
run_as_app "python scripts/check_download_root.py"

echo "[entrypoint] Warte auf Postgres..."
run_as_app "python scripts/wait_for_postgres.py"

echo "[entrypoint] Wende Alembic-Migrationen an (App-Tabellen)..."
run_as_app "alembic upgrade head"

echo "[entrypoint] Prüfe Procrastinate-Schema..."
# "procrastinate schema --apply" ist laut CLI-Hilfe explizit NUR für eine leere Datenbank gedacht
# ("This won't work if the schema has already been applied") - NICHT idempotent. Bei jedem echten
# Container-Neustart (nicht nur Uvicorn-Reload) würde ein erneuter Aufruf sonst mit
# "type ... already exists" abbrechen und den Container in eine Restart-Schleife schicken.
SCHEMA_EXISTS=$(run_as_app "python scripts/check_procrastinate_schema.py")

if [ "$SCHEMA_EXISTS" = "yes" ]; then
    echo "[entrypoint] Procrastinate-Schema bereits vorhanden, überspringe schema --apply."
else
    echo "[entrypoint] Wende Procrastinate-Schema erstmalig an..."
    # "python -m procrastinate" statt des "procrastinate"-Konsolenskripts: nur so landet das
    # Arbeitsverzeichnis (/app, enthaelt das Package "app") automatisch im sys.path, damit
    # der dotted Pfad "app.workers.procrastinate_app.app" aufloesbar ist.
    run_as_app "python -m procrastinate --app=app.workers.procrastinate_app.app schema --apply"
fi

echo "[entrypoint] Starte Anwendung..."
exec su appuser -s /bin/bash -c "exec python -m app.run"
