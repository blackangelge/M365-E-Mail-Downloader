"""FastAPI-App-Factory: Routen, Lifespan (startet/stoppt den Procrastinate-Worker), Static Files."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import get_settings
from app.database import engine
from app.security.auth import require_admin
from app.web.routers import calendar, dashboard, filters, jobs, logs, mailboxes, tenants
from app.workers.procrastinate_app import app as procrastinate_app

_BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=get_settings().log_level)

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

    @fastapi_app.get("/healthz")
    async def healthz() -> dict:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    return fastapi_app
