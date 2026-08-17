"""System-Ereignisse: Download-Ordner-Prüfung beim Start und M365-Verbindungsversuche - in der
Weboberfläche sichtbar statt nur in den Docker-Container-Logs (siehe app/events.py)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.events import log_event
from app.models import EventCategory, EventLevel, Mailbox, SystemEvent, Tenant
from app.web.templating import templates
from app.workers.storage import write_once

router = APIRouter(prefix="/system")

_PAGE_SIZE = 50


@router.post("/test-file")
async def create_test_file(session: Annotated[AsyncSession, Depends(get_session)]):
    """Legt eine simple Test-Textdatei direkt im Download-Ordner an - schneller Weg, um zu
    prüfen, dass der Ordner beschreibbar und korrekt gemountet ist, ohne auf eine echte E-Mail
    warten zu müssen."""
    settings = get_settings()
    timestamp = datetime.now(timezone.utc)
    filename = f"testdatei_{timestamp.strftime('%Y%m%d_%H%M%S')}.txt"
    target_path = settings.download_root / filename
    content = (
        f"Testdatei von PDF Download M365\n"
        f"Erstellt am: {timestamp.isoformat()}\n"
        f"Wenn diese Datei sichtbar ist, funktioniert der Download-Ordner-Mount korrekt.\n"
    ).encode("utf-8")

    try:
        write_once(target_path, content)
        await log_event(
            session,
            category=EventCategory.STARTUP,
            level=EventLevel.INFO,
            message=f"Testdatei erfolgreich angelegt: {filename}",
        )
        return RedirectResponse(
            f"/system?msg=Testdatei+angelegt%3A+{quote(settings.download_host_dir.rstrip('/'))}%2F{quote(filename)}",
            status_code=303,
        )
    except OSError as exc:
        await log_event(
            session,
            category=EventCategory.STARTUP,
            level=EventLevel.ERROR,
            message=f"Testdatei konnte nicht angelegt werden: {exc}",
        )
        return RedirectResponse(f"/system?err=Testdatei+fehlgeschlagen%3A+{quote(str(exc))}", status_code=303)


@router.get("")
async def list_system_events(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    category: str = "",
    page: int = 1,
):
    query = select(SystemEvent, Tenant, Mailbox).outerjoin(Tenant, SystemEvent.tenant_id == Tenant.id).outerjoin(
        Mailbox, SystemEvent.mailbox_id == Mailbox.id
    )
    if category in (
        EventCategory.STARTUP.value,
        EventCategory.GRAPH_CONNECTION.value,
        EventCategory.DOWNLOAD_ERROR.value,
    ):
        query = query.where(SystemEvent.category == category)

    query = query.order_by(SystemEvent.created_at.desc())
    page = max(page, 1)
    result = await session.execute(query.offset((page - 1) * _PAGE_SIZE).limit(_PAGE_SIZE + 1))
    rows = result.all()
    has_next = len(rows) > _PAGE_SIZE
    rows = rows[:_PAGE_SIZE]
    events = [{"event": e, "tenant": t, "mailbox": m} for e, t, m in rows]

    return templates.TemplateResponse(
        request,
        "system.html",
        {
            "active_nav": "system",
            "events": events,
            "category": category,
            "page": page,
            "has_next": has_next,
        },
    )
