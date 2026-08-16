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
from app.graph.client_factory import test_mailbox_access
from app.models import AttachmentFile, Job, JobMailbox, Mailbox, MailboxSyncState, ProcessedEmail, Tenant
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

    sync_states_result = await session.execute(select(MailboxSyncState))
    sync_by_mailbox = {s.mailbox_id: s for s in sync_states_result.scalars().all()}

    tenants_result = await session.execute(select(Tenant).order_by(Tenant.name))

    return templates.TemplateResponse(
        request,
        "mailboxes/list.html",
        {
            "active_nav": "mailboxes",
            "mailboxes": mailboxes,
            "sync_by_mailbox": sync_by_mailbox,
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
    state = await session.get(MailboxSyncState, mailbox_id)
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
            "state": state,
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


@router.post("/{mailbox_id}/test")
async def test_mailbox(mailbox_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]):
    mailbox = await session.get(Mailbox, mailbox_id)
    tenant = await session.get(Tenant, mailbox.tenant_id)
    ok, error = await test_mailbox_access(tenant, mailbox.email_address)
    if ok:
        return RedirectResponse(f"/mailboxes?msg=Verbindung+zu+{quote(mailbox.email_address)}+erfolgreich", status_code=303)
    return RedirectResponse(f"/mailboxes?err={quote(error or 'Verbindungstest fehlgeschlagen')}", status_code=303)
