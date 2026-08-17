"""Postgres-Advisory-Lock, der verhindert, dass zwei `sync_then_match`-Läufe für DASSELBE
Postfach gleichzeitig aktiv sind.

Der `queueing_lock` von Procrastinate (siehe `run_job` in app/workers/tasks.py) verhindert nur,
dass mehrere Läufe gleichzeitig in der Warteschlange (Status "todo") stehen - sobald ein Worker
einen Lauf aus der Warteschlange holt (Status "doing"), wird der queueing_lock bereits wieder
freigegeben. Läuft ein Sync länger als das Poll-Intervall eines Jobs (genau der vom Nutzer
beobachtete Fall: über eine Stunde Laufzeit bei 21542 Nachrichten), kann der nächste Tick daher
trotzdem einen zweiten, tatsächlich GLEICHZEITIG laufenden Sync für dasselbe Postfach starten.

Dieser Lock schließt die Lücke: `pg_try_advisory_lock` ist sitzungsgebunden (nicht nur
transaktionsgebunden wie `pg_advisory_xact_lock` in app/workers/dedup.py) und wird über eine
eigene, für die gesamte Sync-Dauer offen gehaltene Verbindung gehalten - genau die Laufzeit, die
abgesichert werden muss. Ein zweiter, sich überschneidender Lauf erkennt beim (non-blocking)
Versuch, dass der Lock bereits vergeben ist, und überspringt sich selbst statt zu warten oder
parallel weiterzumachen.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import select, func

from app.database import engine

logger = logging.getLogger("app")


def _advisory_lock_key(mailbox_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"mailbox-sync:{mailbox_id}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


@asynccontextmanager
async def mailbox_sync_guard(mailbox_id: uuid.UUID) -> AsyncIterator[bool]:
    """Non-blocking: liefert `True`, wenn der Lock erfolgreich belegt wurde (Aufrufer darf
    weitermachen), sonst `False` (ein anderer Lauf für dasselbe Postfach ist bereits aktiv -
    Aufrufer sollte sich selbst überspringen). Der Lock wird beim Verlassen des `with`-Blocks in
    jedem Fall wieder freigegeben, sofern er zuvor erfolgreich belegt wurde."""
    lock_key = _advisory_lock_key(mailbox_id)
    async with engine.connect() as conn:
        acquired = (await conn.execute(select(func.pg_try_advisory_lock(lock_key)))).scalar_one()
        try:
            yield bool(acquired)
        finally:
            if acquired:
                await conn.execute(select(func.pg_advisory_unlock(lock_key)))
