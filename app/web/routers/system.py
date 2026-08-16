"""System-Ereignisse: Download-Ordner-Prüfung beim Start und M365-Verbindungsversuche - in der
Weboberfläche sichtbar statt nur in den Docker-Container-Logs (siehe app/events.py)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import EventCategory, Mailbox, SystemEvent, Tenant
from app.web.templating import templates

router = APIRouter(prefix="/system")

_PAGE_SIZE = 50


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
    if category in (EventCategory.STARTUP.value, EventCategory.GRAPH_CONNECTION.value):
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
