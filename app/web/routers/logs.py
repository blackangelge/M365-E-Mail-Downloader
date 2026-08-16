"""Durchsuchbares Verlaufslog aller Anhang-Sichtungen (Downloads + wiederholte, dank Dedup
übersprungene Vorkommen)."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import AttachmentFile, AttachmentSighting, Job, Mailbox, ProcessedEmail
from app.schemas import parse_date_de
from app.web.templating import templates

router = APIRouter(prefix="/logs")

_PAGE_SIZE = 30


@router.get("")
async def list_logs(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    # Als reine Strings entgegennehmen statt als uuid.UUID/date: die "Alle"-Option der
    # Filter-Dropdowns sendet einen leeren String, den FastAPI für UUID/date-Query-Parameter
    # NICHT automatisch in None umwandelt, sondern mit einem 422-JSON-Validierungsfehler
    # ablehnt, bevor unser Code überhaupt läuft. Deshalb wird hier manuell und tolerant geparst.
    job_id: str = "",
    mailbox_id: str = "",
    date_from: str = "",
    date_to: str = "",
    q: str = "",
    page: int = 1,
):
    def _parse_uuid(raw: str) -> uuid.UUID | None:
        try:
            return uuid.UUID(raw) if raw else None
        except ValueError:
            return None  # z.B. bei einer manuell verstümmelten URL - einfach ignorieren statt 500

    job_id_uuid = _parse_uuid(job_id)
    mailbox_id_uuid = _parse_uuid(mailbox_id)
    date_from_parsed = parse_date_de(date_from)
    date_to_parsed = parse_date_de(date_to)

    query = (
        select(AttachmentSighting, AttachmentFile, ProcessedEmail, Mailbox, Job)
        .join(AttachmentFile, AttachmentSighting.attachment_file_id == AttachmentFile.id)
        .join(ProcessedEmail, AttachmentSighting.processed_email_id == ProcessedEmail.id)
        .join(Mailbox, AttachmentFile.mailbox_id == Mailbox.id)
        .join(Job, AttachmentSighting.job_id == Job.id)
    )
    if job_id_uuid:
        query = query.where(AttachmentSighting.job_id == job_id_uuid)
    if mailbox_id_uuid:
        query = query.where(Mailbox.id == mailbox_id_uuid)
    if date_from_parsed:
        query = query.where(AttachmentSighting.seen_at >= date_from_parsed)
    if date_to_parsed:
        query = query.where(AttachmentSighting.seen_at < date_to_parsed)
    if q:
        like = f"%{q.lower()}%"
        query = query.where(
            (ProcessedEmail.subject.ilike(like)) | (AttachmentFile.original_filename.ilike(like))
        )

    query = query.order_by(AttachmentSighting.seen_at.desc())
    page = max(page, 1)
    result = await session.execute(query.offset((page - 1) * _PAGE_SIZE).limit(_PAGE_SIZE + 1))
    rows = result.all()
    has_next = len(rows) > _PAGE_SIZE
    rows = rows[:_PAGE_SIZE]
    entries = [{"sighting": s, "file": f, "email": e, "mailbox": m, "job": j} for s, f, e, m, j in rows]

    jobs_result = await session.execute(select(Job).order_by(Job.name))
    mailboxes_result = await session.execute(select(Mailbox).order_by(Mailbox.email_address))

    return templates.TemplateResponse(
        request,
        "logs.html",
        {
            "active_nav": "logs",
            "entries": entries,
            "jobs": jobs_result.scalars().all(),
            "mailboxes": mailboxes_result.scalars().all(),
            "filters": {
                "job_id": job_id_uuid,
                "mailbox_id": mailbox_id_uuid,
                "date_from": date_from_parsed,
                "date_to": date_to_parsed,
                "q": q,
            },
            "page": page,
            "has_next": has_next,
        },
    )
