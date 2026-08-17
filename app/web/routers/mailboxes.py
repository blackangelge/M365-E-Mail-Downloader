"""Postfächer pro Tenant verwalten."""
from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.events import log_event
from app.graph.client_factory import test_mailbox_access
from app.models import (
    AttachmentFile,
    AttachmentSkip,
    EventCategory,
    EventLevel,
    Job,
    JobMailbox,
    JobMessageEvaluation,
    Mailbox,
    MailboxFolder,
    ProcessedEmail,
    SyncStatus,
    Tenant,
)
from app.web.templating import templates

router = APIRouter(prefix="/mailboxes")


async def _synced_message_counts(session: AsyncSession) -> dict[uuid.UUID, int]:
    """Anzahl bereits per Delta-Sync eingelesener Nachrichten je Postfach - die einzige
    zuverlässige Fortschritts-Näherung, die wir ohne Kenntnis der Gesamtgröße des Postfachs
    haben (Graph liefert bei Delta-Queries keine Gesamtanzahl vorab)."""
    result = await session.execute(
        select(ProcessedEmail.mailbox_id, func.count(ProcessedEmail.id)).group_by(ProcessedEmail.mailbox_id)
    )
    return dict(result.all())


async def _downloaded_counts(session: AsyncSession) -> dict[uuid.UUID, int]:
    result = await session.execute(
        select(AttachmentFile.mailbox_id, func.count(AttachmentFile.id)).group_by(AttachmentFile.mailbox_id)
    )
    return dict(result.all())


async def _folder_summary(session: AsyncSession, mailbox_id: uuid.UUID | None = None) -> dict[uuid.UUID, dict]:
    """Aggregiert den Sync-Status über ALLE Ordner (Posteingang + Unterordner) eines Postfachs:
    'Fehler' wenn mindestens ein Ordner einen Fehler hat, 'läuft' wenn mindestens einer noch
    synchronisiert, sonst 'idle'. Ein Postfach hat keinen einzelnen Sync-Status mehr, seit auch
    Unterordner verarbeitet werden (siehe app/workers/tasks.py::sync_then_match)."""
    query = select(MailboxFolder)
    if mailbox_id is not None:
        query = query.where(MailboxFolder.mailbox_id == mailbox_id)
    result = await session.execute(query)

    summary: dict[uuid.UUID, dict] = {}
    for folder in result.scalars().all():
        agg = summary.setdefault(
            folder.mailbox_id,
            {"status": SyncStatus.IDLE, "last_delta_run_at": None, "last_error": None, "total": 0, "errors": 0},
        )
        agg["total"] += 1
        if folder.status == SyncStatus.ERROR:
            agg["errors"] += 1
            if agg["status"] != SyncStatus.ERROR:
                agg["status"] = SyncStatus.ERROR
                agg["last_error"] = f"{folder.display_path}: {folder.last_error}"
        elif folder.status == SyncStatus.RUNNING and agg["status"] != SyncStatus.ERROR:
            agg["status"] = SyncStatus.RUNNING
        if folder.last_delta_run_at and (
            agg["last_delta_run_at"] is None or folder.last_delta_run_at > agg["last_delta_run_at"]
        ):
            agg["last_delta_run_at"] = folder.last_delta_run_at
    return summary


async def _next_sync_at(session: AsyncSession, mailbox_id: uuid.UUID | None = None):
    """Frühester `next_run_at` unter den aktivierten Jobs, die ein Postfach beobachten - ein
    Postfach hat ja keinen eigenen Zeitplan, sondern erbt ihn von den Jobs, die es verwenden."""
    query = (
        select(JobMailbox.mailbox_id, func.min(Job.next_run_at))
        .join(Job, Job.id == JobMailbox.job_id)
        .where(Job.enabled.is_(True))
        .group_by(JobMailbox.mailbox_id)
    )
    if mailbox_id is not None:
        query = query.where(JobMailbox.mailbox_id == mailbox_id)
        result = await session.execute(query)
        row = result.first()
        return row[1] if row else None

    result = await session.execute(query)
    return dict(result.all())


@router.get("")
async def list_mailboxes(request: Request, session: Annotated[AsyncSession, Depends(get_session)]):
    result = await session.execute(
        select(Mailbox, Tenant).join(Tenant, Mailbox.tenant_id == Tenant.id).order_by(Tenant.name, Mailbox.email_address)
    )
    mailboxes = [{"mailbox": m, "tenant": t} for m, t in result.all()]

    tenants_result = await session.execute(select(Tenant).order_by(Tenant.name))

    return templates.TemplateResponse(
        request,
        "mailboxes/list.html",
        {
            "active_nav": "mailboxes",
            "mailboxes": mailboxes,
            "folder_summary": await _folder_summary(session),
            "synced_counts": await _synced_message_counts(session),
            "downloaded_counts": await _downloaded_counts(session),
            "next_sync_ats": await _next_sync_at(session),
            "tenants": tenants_result.scalars().all(),
        },
    )


@router.get("/{mailbox_id}/status")
async def mailbox_status(request: Request, mailbox_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]):
    """HTMX-Partial: wird alle paar Sekunden nachgeladen, solange ein Postfach synchronisiert,
    damit sichtbar ist, dass (und wie weit) der Sync tatsächlich vorankommt - nicht nur, dass er
    'läuft'."""
    summary = (await _folder_summary(session, mailbox_id)).get(mailbox_id)
    synced_result = await session.execute(
        select(func.count(ProcessedEmail.id)).where(ProcessedEmail.mailbox_id == mailbox_id)
    )
    downloaded_result = await session.execute(
        select(func.count(AttachmentFile.id)).where(AttachmentFile.mailbox_id == mailbox_id)
    )
    return templates.TemplateResponse(
        request,
        "mailboxes/partials/status_cell.html",
        {
            "mailbox_id": mailbox_id,
            "summary": summary,
            "synced_count": synced_result.scalar_one(),
            "downloaded_count": downloaded_result.scalar_one(),
            "next_sync_at": await _next_sync_at(session, mailbox_id),
        },
    )


@router.post("")
async def create_mailbox(
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[uuid.UUID, Form()],
    email_address: Annotated[str, Form()],
    display_name: Annotated[str, Form()] = "",
):
    mailbox = Mailbox(tenant_id=tenant_id, email_address=email_address.strip(), display_name=display_name or None)
    session.add(mailbox)
    await session.commit()
    return RedirectResponse(f"/mailboxes?msg=Postfach+{quote(email_address)}+angelegt", status_code=303)


@router.post("/{mailbox_id}/delete")
async def delete_mailbox(mailbox_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]):
    mailbox = await session.get(Mailbox, mailbox_id)
    if mailbox:
        await session.delete(mailbox)
        await session.commit()
    return RedirectResponse("/mailboxes?msg=Postfach+gel%C3%B6scht", status_code=303)


@router.post("/{mailbox_id}/reset")
async def reset_mailbox(mailbox_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]):
    """Setzt das Dedup-/Auswertungsgedächtnis eines Postfachs komplett zurück: löscht alle
    `AttachmentFile`-Einträge (kaskadiert per FK automatisch auf `AttachmentSighting`), alle
    `AttachmentSkip`-Ausschluss-Aufzeichnungen und alle `JobMessageEvaluation`-Wasserzeichen für
    die Nachrichten dieses Postfachs. Beim nächsten Lauf wird dadurch JEDE bereits eingelesene
    Nachricht erneut gegen den aktuellen Filter geprüft und jeder passende Anhang erneut
    heruntergeladen - so als würde das Postfach zum ersten Mal verarbeitet.

    Bewusst NICHT zurückgesetzt: `ProcessedEmail` (die per Graph-Delta-Sync eingelesenen
    Nachrichten-Metadaten) und `MailboxFolder.delta_link` - es muss dafür nichts erneut von Graph
    geladen werden, nur die Bewertung/der Download-Status wird vergessen.

    WICHTIG: bereits auf die Platte geschriebene Dateien werden NICHT gelöscht (write-once-Prinzip,
    siehe app/workers/storage.py - die App fasst eine einmal geschriebene Datei nie wieder an).
    Ein erneuter Download landet dann als neue Datei mit "_1"-Suffix neben der alten, falls die
    alte noch im Zielordner liegt - bei Bedarf den Zielordner vorher manuell leeren.
    """
    mailbox = await session.get(Mailbox, mailbox_id)
    if mailbox is None:
        return RedirectResponse("/mailboxes?err=Postfach+nicht+gefunden", status_code=303)

    email_ids_subq = select(ProcessedEmail.id).where(ProcessedEmail.mailbox_id == mailbox_id).scalar_subquery()

    await session.execute(
        JobMessageEvaluation.__table__.delete().where(JobMessageEvaluation.processed_email_id.in_(email_ids_subq))
    )
    await session.execute(
        AttachmentSkip.__table__.delete().where(AttachmentSkip.processed_email_id.in_(email_ids_subq))
    )
    delete_result = await session.execute(
        AttachmentFile.__table__.delete().where(AttachmentFile.mailbox_id == mailbox_id)
    )
    await session.commit()

    return RedirectResponse(
        f"/mailboxes?msg=Postfach+{quote(mailbox.email_address)}+zur%C3%BCckgesetzt+-+"
        f"{delete_result.rowcount}+Dedup-Eintr%C3%A4ge+gel%C3%B6scht.+Bereits+geschriebene+"
        f"Dateien+bleiben+erhalten.",
        status_code=303,
    )


@router.post("/{mailbox_id}/test")
async def test_mailbox(mailbox_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]):
    mailbox = await session.get(Mailbox, mailbox_id)
    tenant = await session.get(Tenant, mailbox.tenant_id)
    ok, error = await test_mailbox_access(tenant, mailbox.email_address)
    if ok:
        await log_event(
            session,
            category=EventCategory.GRAPH_CONNECTION,
            level=EventLevel.INFO,
            message=f"Manueller Verbindungstest erfolgreich (Postfach {mailbox.email_address}).",
            tenant_id=tenant.id,
            mailbox_id=mailbox.id,
        )
        return RedirectResponse(f"/mailboxes?msg=Verbindung+zu+{quote(mailbox.email_address)}+erfolgreich", status_code=303)

    await log_event(
        session,
        category=EventCategory.GRAPH_CONNECTION,
        level=EventLevel.ERROR,
        message=f"Manueller Verbindungstest fehlgeschlagen (Postfach {mailbox.email_address}): {error}",
        tenant_id=tenant.id,
        mailbox_id=mailbox.id,
    )
    return RedirectResponse(f"/mailboxes?err={quote(error or 'Verbindungstest fehlgeschlagen')}", status_code=303)
