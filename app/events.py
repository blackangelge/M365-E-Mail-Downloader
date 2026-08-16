"""Zentrale Hilfsfunktion zum Schreiben von System-Ereignissen (Download-Ordner-Prüfung,
M365-Verbindungsversuche) - wird sowohl von den Web-Routern (manueller Verbindungstest) als auch
von den Procrastinate-Tasks (Sync-Läufe) verwendet, damit diese Vorgänge in der Weboberfläche
sichtbar sind statt nur in den Docker-Container-Logs."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EventCategory, EventLevel, SystemEvent

logger = logging.getLogger("app.events")


async def log_event(
    session: AsyncSession,
    *,
    category: EventCategory,
    level: EventLevel,
    message: str,
    tenant_id: uuid.UUID | None = None,
    mailbox_id: uuid.UUID | None = None,
    commit: bool = True,
) -> None:
    session.add(
        SystemEvent(category=category, level=level, message=message, tenant_id=tenant_id, mailbox_id=mailbox_id)
    )
    if commit:
        await session.commit()

    # Zusätzlich weiterhin in den Container-Logs sichtbar (z.B. für `docker compose logs -f`).
    log_fn = logger.error if level == EventLevel.ERROR else logger.info
    log_fn("[%s] %s", category.value, message)
