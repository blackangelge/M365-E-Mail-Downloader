"""Kalender-Monatsansicht: pro Tag Anzahl neuer Downloads, Klick auf einen Tag zeigt Details
(Betreff, Absender, Dateiname, Pfad) als HTMX-Partial."""
from __future__ import annotations

import calendar as calendar_module
from datetime import date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.models import AttachmentFile, AttachmentSighting, Mailbox, ProcessedEmail
from app.web.templating import templates

router = APIRouter(prefix="/calendar")


@router.get("")
async def calendar_view(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    year: int | None = None,
    month: int | None = None,
):
    settings = get_settings()
    tz = ZoneInfo(settings.app_timezone)
    today_local = datetime.now(tz).date()
    year = year or today_local.year
    month = month or today_local.month

    month_start = date(year, month, 1)
    days_in_month = calendar_module.monthrange(year, month)[1]
    month_end = date(year, month, days_in_month)

    # Gruppiert nach dem E-Mail-Empfangsdatum (ProcessedEmail.received_at), NICHT nach dem
    # Download-/Verarbeitungszeitpunkt (AttachmentSighting.seen_at) - ein initialer Voll-Sync
    # kann viele alte E-Mails an einem einzigen Tag verarbeiten, das soll den Kalender nicht
    # verzerren. Der Download-Zeitpunkt bleibt zusätzlich im Tages-Detail sichtbar.
    #
    # Neue Downloads und Duplikate (dank Dedup nicht erneut heruntergeladene, aber trotzdem
    # gesehene Anhänge) werden getrennt gezählt, damit Duplikate im Kalender sichtbar bleiben
    # statt komplett zu verschwinden.
    received_local_day = func.date(ProcessedEmail.received_at.op("AT TIME ZONE")(settings.app_timezone))
    result = await session.execute(
        select(
            received_local_day.label("day"),
            func.count(AttachmentSighting.id).filter(AttachmentSighting.is_new_download.is_(True)),
            func.count(AttachmentSighting.id).filter(AttachmentSighting.is_new_download.is_(False)),
        )
        .select_from(AttachmentSighting)
        .join(AttachmentFile, AttachmentSighting.attachment_file_id == AttachmentFile.id)
        .join(ProcessedEmail, AttachmentSighting.processed_email_id == ProcessedEmail.id)
        .where(received_local_day >= month_start)
        .where(received_local_day <= month_end)
        .group_by("day")
    )
    counts_by_day = {row[0]: {"new": row[1], "duplicates": row[2]} for row in result.all()}

    first_weekday = month_start.weekday()  # Montag=0
    weeks: list[list[dict | None]] = []
    week: list[dict | None] = [None] * first_weekday
    for day_num in range(1, days_in_month + 1):
        d = date(year, month, day_num)
        counts = counts_by_day.get(d, {"new": 0, "duplicates": 0})
        week.append({"date": d, "count": counts["new"], "duplicate_count": counts["duplicates"]})
        if len(week) == 7:
            weeks.append(week)
            week = []
    if week:
        week += [None] * (7 - len(week))
        weeks.append(week)

    prev_month = (month_start - timedelta(days=1)).replace(day=1)
    next_month = (month_end + timedelta(days=1))

    return templates.TemplateResponse(
        request,
        "calendar.html",
        {
            "active_nav": "calendar",
            "year": year,
            "month": month,
            "month_name": month_start.strftime("%B %Y"),
            "weeks": weeks,
            "prev_year": prev_month.year,
            "prev_month": prev_month.month,
            "next_year": next_month.year,
            "next_month": next_month.month,
            "today": today_local,
        },
    )


@router.get("/day/{iso_date}")
async def calendar_day(request: Request, iso_date: date, session: Annotated[AsyncSession, Depends(get_session)]):
    settings = get_settings()
    received_local_day = func.date(ProcessedEmail.received_at.op("AT TIME ZONE")(settings.app_timezone))

    result = await session.execute(
        select(AttachmentSighting, AttachmentFile, ProcessedEmail, Mailbox)
        .join(AttachmentFile, AttachmentSighting.attachment_file_id == AttachmentFile.id)
        .join(ProcessedEmail, AttachmentSighting.processed_email_id == ProcessedEmail.id)
        .join(Mailbox, AttachmentFile.mailbox_id == Mailbox.id)
        .where(received_local_day == iso_date)
        .order_by(ProcessedEmail.received_at)
    )
    entries = [{"sighting": s, "file": f, "email": e, "mailbox": m} for s, f, e, m in result.all()]

    return templates.TemplateResponse(
        request, "partials/calendar_day.html", {"day": iso_date, "entries": entries}
    )
