"""Dashboard: Kennzahlen-Kacheln (heute/gestern/diese Woche/letzte Woche), Postfach-Aufschlüsselung,
letzte Downloads."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.models import AttachmentFile, AttachmentSighting, Mailbox, ProcessedEmail, Tenant
from app.web.stats import period_bounds
from app.web.templating import templates

router = APIRouter()


@router.get("/")
async def dashboard(request: Request, session: Annotated[AsyncSession, Depends(get_session)]):
    settings = get_settings()
    b = period_bounds(settings.app_timezone)
    is_new = AttachmentSighting.is_new_download.is_(True)

    totals_row = (
        await session.execute(
            select(
                func.count(AttachmentSighting.id).filter(and_(is_new, AttachmentSighting.seen_at >= b["today_start"])),
                func.count(AttachmentSighting.id).filter(
                    and_(is_new, AttachmentSighting.seen_at >= b["yesterday_start"], AttachmentSighting.seen_at < b["today_start"])
                ),
                func.count(AttachmentSighting.id).filter(and_(is_new, AttachmentSighting.seen_at >= b["week_start"])),
                func.count(AttachmentSighting.id).filter(
                    and_(is_new, AttachmentSighting.seen_at >= b["last_week_start"], AttachmentSighting.seen_at < b["week_start"])
                ),
                func.count(AttachmentSighting.id).filter(is_new),
            )
        )
    ).one()
    totals = {
        "today": totals_row[0],
        "yesterday": totals_row[1],
        "this_week": totals_row[2],
        "last_week": totals_row[3],
        "all_time": totals_row[4],
    }

    per_mailbox_result = await session.execute(
        select(
            Mailbox.id,
            Mailbox.email_address,
            Tenant.name,
            func.count(AttachmentSighting.id).filter(and_(is_new, AttachmentSighting.seen_at >= b["today_start"])),
            func.count(AttachmentSighting.id).filter(and_(is_new, AttachmentSighting.seen_at >= b["week_start"])),
            func.count(AttachmentSighting.id).filter(is_new),
        )
        .join(AttachmentFile, AttachmentFile.mailbox_id == Mailbox.id)
        .join(AttachmentSighting, AttachmentSighting.attachment_file_id == AttachmentFile.id)
        .join(Tenant, Tenant.id == Mailbox.tenant_id)
        .group_by(Mailbox.id, Mailbox.email_address, Tenant.name)
        .order_by(func.count(AttachmentSighting.id).filter(is_new).desc())
    )
    per_mailbox = [
        {"mailbox_id": row[0], "email": row[1], "tenant": row[2], "today": row[3], "this_week": row[4], "all_time": row[5]}
        for row in per_mailbox_result.all()
    ]

    recent_result = await session.execute(
        select(AttachmentSighting, AttachmentFile, ProcessedEmail)
        .join(AttachmentFile, AttachmentSighting.attachment_file_id == AttachmentFile.id)
        .join(ProcessedEmail, AttachmentSighting.processed_email_id == ProcessedEmail.id)
        .where(is_new)
        .order_by(AttachmentSighting.seen_at.desc())
        .limit(15)
    )
    recent = [
        {"sighting": s, "file": f, "email": e}
        for s, f, e in recent_result.all()
    ]

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"active_nav": "dashboard", "totals": totals, "per_mailbox": per_mailbox, "recent": recent},
    )
