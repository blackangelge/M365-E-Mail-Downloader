#!/usr/bin/env bash
set -euo pipefail

# --- Container-User an den Besitzer der gemounteten Host-Ordner anpassen -----------------------
# Einfachere Docker-Projekte laufen oft als root im Container und haben deshalb nie
# Berechtigungsprobleme mit Bind-Mounts. Dieses Projekt startet bewusst NICHT als root (Security
# Best Practice) - dafür muss die UID/GID des Container-Users zu der des Host-Ordners passen,
# sonst schlägt jeder Schreibversuch auf nativem Linux-Docker mit "Permission denied" fehl
# (anders als z.B. auf Docker Desktop unter Windows/Mac, das das meist transparent überbrückt).
# Statt vom Nutzer zu verlangen, seinen Host-Ordner auf eine feste Container-UID zu chownen, passt
# sich der Container hier an: PUID/PGID in .env auf die UID/GID setzen, der/die der Host-Ordner
# (DOWNLOAD_HOST_DIR) bereits gehört - Standard-Pattern u.a. aus den LinuxServer.io-Images.
# Default 1000:1000 (die im Image fest angelegte appuser-UID/GID) für Rückwärtskompatibilität.
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

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
