# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY pyproject.toml ./
# Kein Lockfile-Format vorgegeben: Dependencies werden aus pyproject.toml installiert.
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .


FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

COPY --from=builder /venv /venv
# PYTHONPATH=/app: sorgt dafür, dass "import app" auch dann auflöst, wenn Skripte unter
# scripts/ direkt als Datei ausgeführt werden (python scripts/foo.py setzt sonst nur das
# Skript-eigene Verzeichnis auf sys.path, nicht das Arbeitsverzeichnis /app).
ENV PATH="/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONPATH=/app

WORKDIR /app
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts ./scripts

RUN mkdir -p /data/Download && chown -R appuser:appuser /app /data

# Bewusst KEIN "USER appuser" hier: der Container startet als root, damit scripts/entrypoint.sh
# die appuser-UID/GID zur Laufzeit an PUID/PGID (siehe .env) anpassen kann - notwendig, damit
# Bind-Mounts auf nativem Linux-Docker unabhängig vom Besitzer des Host-Ordners funktionieren.
# entrypoint.sh wechselt danach selbst per "su appuser" zu einem unprivilegierten Prozess, bevor
# irgendein App-Code läuft.

EXPOSE 4000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:4000/healthz || exit 1

ENTRYPOINT ["scripts/entrypoint.sh"]
