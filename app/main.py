"""FastAPI-App-Factory: Routen, Lifespan (startet/stoppt den Procrastinate-Worker), Static Files."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import get_settings
from app.database import async_session_factory, engine
from app.events import log_event
from app.models import EventCategory, EventLevel
from app.security.auth import require_admin
from app.web.routers import calendar, dashboard, filters, jobs, logs, mailboxes, system, tenants
from app.workers.procrastinate_app import app as procrastinate_app

_BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger("app")


def _current_uid_gid() -> str:
    try:
        return f"{os.getuid()}:{os.getgid()}"
    except AttributeError:
        return "1000:1000"  # Windows hat kein os.getuid() - Container-Standard-UID als Fallback


async def _check_download_root_mount() -> None:
    """Bestätigt beim Start, dass DOWNLOAD_ROOT tatsächlich von einem Host-Verzeichnis gemountet
    UND für den Container-User beschreibbar ist (dieselbe Prüfung wie in scripts/entrypoint.sh,
    hier zusätzlich DB-persistiert, damit das Ergebnis in der Weboberfläche sichtbar ist statt
    nur in den Container-Logs).

    Der Container läuft als non-root-User mit fester UID (siehe Dockerfile). Auf nativem
    Linux-Docker (anders als z.B. Docker Desktop auf Windows/Mac) zählt für Bind-Mounts exakt der
    Besitzer des Host-Verzeichnisses - passt der nicht, schlägt jeder Schreibversuch mit
    "Permission denied" fehl, obwohl der Mount selbst technisch korrekt ist.
    """
    root = get_settings().download_root
    uid_gid = _current_uid_gid()

    async with async_session_factory() as session:
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".write_test"
            probe.write_text("ok")
            probe.unlink()
        except PermissionError as exc:
            await log_event(
                session,
                category=EventCategory.STARTUP,
                level=EventLevel.ERROR,
                message=(
                    f"Download-Ordner-Prüfung fehlgeschlagen: {root} ist nicht beschreibbar "
                    f"({exc}). Der Container läuft mit UID:GID {uid_gid} - auf nativem "
                    f"Linux-Docker muss der Host-Ordner (DOWNLOAD_HOST_DIR) diesem Besitzer "
                    f"gehören: sudo chown -R {uid_gid} <Host-Ordner>"
                ),
            )
            return

        mounted_correctly = os.stat(root).st_dev != os.stat(root.parent).st_dev
        if mounted_correctly:
            await log_event(
                session,
                category=EventCategory.STARTUP,
                level=EventLevel.INFO,
                message=f"Download-Ordner-Prüfung erfolgreich: {root} ist vom Host gemountet und beschreibbar.",
            )
        else:
            await log_event(
                session,
                category=EventCategory.STARTUP,
                level=EventLevel.ERROR,
                message=(
                    f"Download-Ordner-Prüfung fehlgeschlagen: {root} scheint NICHT von einem "
                    f"Host-Verzeichnis gemountet zu sein. Downloads würden nur im Container "
                    f"landen und bei einem Neustart verloren gehen. DOWNLOAD_HOST_DIR in .env prüfen."
                ),
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=get_settings().log_level)
    await _check_download_root_mount()

    async with procrastinate_app.open_async():
        worker_task = asyncio.create_task(
            procrastinate_app.run_worker_async(wait=True, listen_notify=True, install_signal_handlers=False)
        )
        logger.info("Procrastinate-Worker gestartet")
        try:
            yield
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - sauberer Shutdown, egal was kommt
                pass
            logger.info("Procrastinate-Worker gestoppt")


def create_app() -> FastAPI:
    settings = get_settings()
    fastapi_app = FastAPI(title="PDF Download M365", lifespan=lifespan)

    protected = [Depends(require_admin)] if settings.auth_enabled else []

    static_dir = _BASE_DIR / "web" / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    fastapi_app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    fastapi_app.include_router(dashboard.router, dependencies=protected)
    fastapi_app.include_router(tenants.router, dependencies=protected)
    fastapi_app.include_router(mailboxes.router, dependencies=protected)
    fastapi_app.include_router(filters.router, dependencies=protected)
    fastapi_app.include_router(jobs.router, dependencies=protected)
    fastapi_app.include_router(calendar.router, dependencies=protected)
    fastapi_app.include_router(logs.router, dependencies=protected)
    fastapi_app.include_router(system.router, dependencies=protected)

    @fastapi_app.get("/healthz")
    async def healthz() -> dict:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    return fastapi_app
