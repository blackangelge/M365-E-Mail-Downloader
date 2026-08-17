"""Jobs anlegen/bearbeiten: Tenant, Postfächer, Filter-Auswahl, Zielordner, Intervall.

Die eigentlichen Filter (Datum/Endungen/Ausschluss-Keywords) werden NICHT hier definiert,
sondern als eigenständige, wiederverwendbare FilterSets unter /filters verwaltet - ein Job
wählt hier nur aus, welcher bestehende Filter verwendet werden soll."""
from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import Date, and_, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import (
    FilterSet,
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
from app.workers.storage import sanitize_path_segment
from app.workers.tasks import run_job

router = APIRouter(prefix="/jobs")


async def _load_job_detail(session: AsyncSession, job: Job) -> dict:
    mailbox_result = await session.execute(
        select(Mailbox).join(JobMailbox, JobMailbox.mailbox_id == Mailbox.id).where(JobMailbox.job_id == job.id)
    )
    filter_set = await session.get(FilterSet, job.filter_set_id)
    return {
        "job": job,
        "filter_set": filter_set,
        "mailboxes": mailbox_result.scalars().all(),
    }


async def _pending_counts_by_job(session: AsyncSession) -> dict[uuid.UUID, int]:
    """Für jeden Job: Anzahl `ProcessedEmail`s mit Anhang, die im Datumsfilter des Jobs liegen,
    aber noch KEINE `JobMessageEvaluation` haben - also noch nicht gegen Endungen/Keywords geprüft
    wurden. Beantwortet "ist der Job fertig oder arbeitet er noch?" (siehe jobs/list.html),
    ohne dafür Procrastinate-interne Queue-Stände auslesen zu müssen.

    Der Datumsvergleich per CAST(... AS date) ist eine Näherung auf UTC-Tagesbasis (keine
    APP_TIMEZONE-Konvertierung wie im Kalender) - für eine reine Fortschrittsanzeige ausreichend;
    die tatsächliche, exakte Filterung passiert weiterhin in `evaluate_job_for_message`.
    """
    received_date = cast(ProcessedEmail.received_at, Date)
    result = await session.execute(
        select(Job.id, func.count(ProcessedEmail.id))
        .select_from(Job)
        .join(FilterSet, FilterSet.id == Job.filter_set_id)
        .join(JobMailbox, JobMailbox.job_id == Job.id)
        .join(ProcessedEmail, ProcessedEmail.mailbox_id == JobMailbox.mailbox_id)
        .outerjoin(
            JobMessageEvaluation,
            and_(
                JobMessageEvaluation.job_id == Job.id,
                JobMessageEvaluation.processed_email_id == ProcessedEmail.id,
            ),
        )
        .where(ProcessedEmail.has_attachments.is_(True))
        .where(JobMessageEvaluation.id.is_(None))
        .where(or_(FilterSet.date_from.is_(None), received_date >= FilterSet.date_from))
        .where(or_(FilterSet.date_to.is_(None), received_date <= FilterSet.date_to))
        .group_by(Job.id)
    )
    return {job_id: count for job_id, count in result.all()}


async def _mailbox_owner_map(
    session: AsyncSession, *, exclude_job_id: uuid.UUID | None = None
) -> dict[uuid.UUID, str]:
    """{mailbox_id: job_name} für alle Postfächer, die bereits einem Job zugeordnet sind - ein
    Postfach darf zu höchstens einem Job gehören (siehe JobMailbox.mailbox_id unique=True in
    app/models.py: zwei Jobs auf demselben Postfach mit überlappenden Filtern führen wegen des
    postfachweiten Content-Dedups sonst dazu, dass der zweite Job nie eine eigene Kopie in seinen
    Zielordner bekommt). `exclude_job_id` blendet die eigenen Zuordnungen aus - beim Bearbeiten
    eines Jobs sind dessen eigene Postfächer kein Konflikt mit sich selbst."""
    query = select(JobMailbox.mailbox_id, Job.name).join(Job, Job.id == JobMailbox.job_id)
    if exclude_job_id is not None:
        query = query.where(JobMailbox.job_id != exclude_job_id)
    result = await session.execute(query)
    return {mailbox_id: job_name for mailbox_id, job_name in result.all()}


async def _mailbox_conflict_error(
    session: AsyncSession, mailbox_ids: list[uuid.UUID], *, exclude_job_id: uuid.UUID | None = None
) -> str | None:
    """Liefert eine Fehlermeldung, falls eines der ausgewählten Postfächer bereits einem anderen
    Job gehört, sonst None."""
    owner_map = await _mailbox_owner_map(session, exclude_job_id=exclude_job_id)
    conflict_ids = [mid for mid in mailbox_ids if mid in owner_map]
    if not conflict_ids:
        return None
    mailboxes_result = await session.execute(select(Mailbox).where(Mailbox.id.in_(conflict_ids)))
    mailbox_by_id = {m.id: m for m in mailboxes_result.scalars().all()}
    details = ", ".join(
        f'{mailbox_by_id[mid].email_address} (bereits bei Job "{owner_map[mid]}")'
        for mid in conflict_ids
        if mid in mailbox_by_id
    )
    return f"Ein Postfach darf nur zu einem Job gehören - Konflikt bei: {details}."


def _friendly_integrity_error(exc: IntegrityError, *, name: str) -> str:
    """Übersetzt eine rohe Postgres-UniqueViolation in eine für den Nutzer verständliche Meldung -
    als Sicherheitsnetz für Races, die die App-seitige Vorab-Prüfung (siehe
    `_mailbox_conflict_error`) theoretisch durchrutschen lässt (z.B. zwei fast gleichzeitige
    Speicherversuche)."""
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if constraint == "uq_job_mailbox_mailbox":
        return "Mindestens eines der ausgewählten Postfächer gehört bereits zu einem anderen Job - ein Postfach darf nur einem Job zugeordnet sein."
    return f'Ein Job mit dem Namen "{name}" existiert bereits - bitte einen anderen Namen wählen.'


async def _running_mailbox_ids(session: AsyncSession) -> set[uuid.UUID]:
    """Postfächer, für die gerade mindestens ein Ordner synchronisiert wird (Status RUNNING) -
    zeigt in der Jobliste live an, ob ein Job gerade aktiv arbeitet."""
    result = await session.execute(
        select(MailboxFolder.mailbox_id).where(MailboxFolder.status == SyncStatus.RUNNING).distinct()
    )
    return {row[0] for row in result.all()}


@router.get("")
async def list_jobs(request: Request, session: Annotated[AsyncSession, Depends(get_session)]):
    result = await session.execute(
        select(Job, Tenant, FilterSet)
        .join(Tenant, Job.tenant_id == Tenant.id)
        .join(FilterSet, Job.filter_set_id == FilterSet.id)
        .order_by(Job.name)
    )
    rows = result.all()

    pending_by_job = await _pending_counts_by_job(session)
    running_mailbox_ids = await _running_mailbox_ids(session)

    job_ids = [j.id for j, _, _ in rows]
    mailbox_result = await session.execute(
        select(JobMailbox.job_id, JobMailbox.mailbox_id).where(JobMailbox.job_id.in_(job_ids))
    )
    mailbox_ids_by_job: dict[uuid.UUID, set[uuid.UUID]] = {}
    for job_id, mailbox_id in mailbox_result.all():
        mailbox_ids_by_job.setdefault(job_id, set()).add(mailbox_id)

    jobs = [
        {
            "job": j,
            "tenant": t,
            "filter_set": fs,
            "pending": pending_by_job.get(j.id, 0),
            "sync_running": bool(mailbox_ids_by_job.get(j.id, set()) & running_mailbox_ids),
        }
        for j, t, fs in rows
    ]
    return templates.TemplateResponse(request, "jobs/list.html", {"active_nav": "jobs", "jobs": jobs})


@router.get("/new")
async def new_job_form(request: Request, session: Annotated[AsyncSession, Depends(get_session)]):
    tenants_result = await session.execute(select(Tenant).where(Tenant.is_active.is_(True)).order_by(Tenant.name))
    filter_sets_result = await session.execute(select(FilterSet).order_by(FilterSet.name))
    return templates.TemplateResponse(
        request,
        "jobs/form.html",
        {
            "active_nav": "jobs",
            "detail": None,
            "prefill": None,
            "error": None,
            "tenants": tenants_result.scalars().all(),
            "filter_sets": filter_sets_result.scalars().all(),
            "mailboxes": [],
            "owner_by_mailbox": {},
        },
    )


async def _render_new_job_form_with_error(
    request: Request,
    session: AsyncSession,
    *,
    error: str,
    name: str,
    tenant_id: uuid.UUID,
    filter_set_id: uuid.UUID,
    target_subfolder: str,
    poll_interval_minutes: int,
    mailbox_ids: list[uuid.UUID],
):
    """Rendert das "Neuer Job"-Formular nach einem fehlgeschlagenen Speichern (z.B. Namenskonflikt)
    erneut MIT den bereits eingegebenen Werten, statt sie wegzuwerfen und den Nutzer alles noch
    einmal eintippen zu lassen."""
    tenants_result = await session.execute(select(Tenant).where(Tenant.is_active.is_(True)).order_by(Tenant.name))
    filter_sets_result = await session.execute(select(FilterSet).order_by(FilterSet.name))
    mailboxes_result = await session.execute(select(Mailbox).where(Mailbox.tenant_id == tenant_id))
    owner_by_mailbox = await _mailbox_owner_map(session)
    return templates.TemplateResponse(
        request,
        "jobs/form.html",
        {
            "active_nav": "jobs",
            "detail": None,
            "error": error,
            "prefill": {
                "name": name,
                "tenant_id": tenant_id,
                "filter_set_id": filter_set_id,
                "target_subfolder": target_subfolder,
                "poll_interval_minutes": poll_interval_minutes,
                "mailbox_ids": set(mailbox_ids),
            },
            "tenants": tenants_result.scalars().all(),
            "filter_sets": filter_sets_result.scalars().all(),
            "mailboxes": mailboxes_result.scalars().all(),
            "owner_by_mailbox": owner_by_mailbox,
        },
        status_code=409,
    )


@router.get("/{job_id}/edit")
async def edit_job_form(request: Request, job_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]):
    job = await session.get(Job, job_id)
    detail = await _load_job_detail(session, job)
    tenants_result = await session.execute(select(Tenant).order_by(Tenant.name))
    filter_sets_result = await session.execute(select(FilterSet).order_by(FilterSet.name))
    mailboxes_result = await session.execute(select(Mailbox).where(Mailbox.tenant_id == job.tenant_id))
    owner_by_mailbox = await _mailbox_owner_map(session, exclude_job_id=job_id)
    return templates.TemplateResponse(
        request,
        "jobs/form.html",
        {
            "active_nav": "jobs",
            "detail": detail,
            "prefill": None,
            "error": None,
            "tenants": tenants_result.scalars().all(),
            "filter_sets": filter_sets_result.scalars().all(),
            "mailboxes": mailboxes_result.scalars().all(),
            "owner_by_mailbox": owner_by_mailbox,
        },
    )


@router.get("/mailboxes-for-tenant/{tenant_id}")
async def mailboxes_for_tenant(request: Request, tenant_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]):
    """HTMX-Partial: aktualisiert die Postfach-Checkboxen, wenn im Formular der Tenant gewechselt wird."""
    result = await session.execute(select(Mailbox).where(Mailbox.tenant_id == tenant_id).order_by(Mailbox.email_address))
    owner_by_mailbox = await _mailbox_owner_map(session)
    return templates.TemplateResponse(
        request,
        "jobs/partials/mailbox_checkboxes.html",
        {"mailboxes": result.scalars().all(), "selected_ids": set(), "owner_by_mailbox": owner_by_mailbox},
    )


async def _save_job_mailboxes(session: AsyncSession, job: Job, mailbox_ids: list[uuid.UUID]) -> None:
    await session.execute(JobMailbox.__table__.delete().where(JobMailbox.job_id == job.id))
    for mid in mailbox_ids:
        session.add(JobMailbox(job_id=job.id, mailbox_id=mid))


@router.post("")
async def create_job(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    name: Annotated[str, Form()],
    tenant_id: Annotated[uuid.UUID, Form()],
    filter_set_id: Annotated[uuid.UUID, Form()],
    target_subfolder: Annotated[str, Form()],
    poll_interval_minutes: Annotated[int, Form()] = 15,
    mailbox_ids: Annotated[list[uuid.UUID], Form()] = [],
):
    # Vorab-Prüfung (freundliche Meldung statt roher 500er): ein Postfach darf nur zu einem Job
    # gehören (siehe _mailbox_owner_map). Die DB-Constraint uq_job_mailbox_mailbox greift zwar
    # ebenfalls, aber nur als Sicherheitsnetz für Races.
    conflict_error = await _mailbox_conflict_error(session, mailbox_ids)
    if conflict_error:
        return await _render_new_job_form_with_error(
            request,
            session,
            error=conflict_error,
            name=name,
            tenant_id=tenant_id,
            filter_set_id=filter_set_id,
            target_subfolder=target_subfolder,
            poll_interval_minutes=poll_interval_minutes,
            mailbox_ids=mailbox_ids,
        )

    job = Job(
        name=name,
        tenant_id=tenant_id,
        filter_set_id=filter_set_id,
        target_subfolder=sanitize_path_segment(target_subfolder),
        poll_interval_minutes=poll_interval_minutes,
    )
    session.add(job)
    try:
        await session.flush()
        await _save_job_mailboxes(session, job, mailbox_ids)
        await session.commit()
    except IntegrityError as exc:
        # Statt mit einem rohen 500er abzubrechen, wird das Formular mit einer klaren Meldung und
        # den bereits eingegebenen Werten erneut angezeigt (Job-Name ODER Postfach-Konflikt).
        await session.rollback()
        return await _render_new_job_form_with_error(
            request,
            session,
            error=_friendly_integrity_error(exc, name=name),
            name=name,
            tenant_id=tenant_id,
            filter_set_id=filter_set_id,
            target_subfolder=target_subfolder,
            poll_interval_minutes=poll_interval_minutes,
            mailbox_ids=mailbox_ids,
        )
    return RedirectResponse(f"/jobs?msg=Job+{quote(name)}+angelegt", status_code=303)


@router.post("/{job_id}")
async def update_job(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    name: Annotated[str, Form()],
    filter_set_id: Annotated[uuid.UUID, Form()],
    target_subfolder: Annotated[str, Form()],
    poll_interval_minutes: Annotated[int, Form()] = 15,
    mailbox_ids: Annotated[list[uuid.UUID], Form()] = [],
    enabled: Annotated[str, Form()] = "",
):
    conflict_error = await _mailbox_conflict_error(session, mailbox_ids, exclude_job_id=job_id)
    if conflict_error:
        return RedirectResponse(f"/jobs/{job_id}/edit?err={quote(conflict_error)}", status_code=303)

    job = await session.get(Job, job_id)
    job.name = name
    job.filter_set_id = filter_set_id
    job.target_subfolder = sanitize_path_segment(target_subfolder)
    job.poll_interval_minutes = poll_interval_minutes
    job.enabled = bool(enabled)
    if job.enabled:
        job.auto_completed = False
    await _save_job_mailboxes(session, job, mailbox_ids)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        return RedirectResponse(
            f"/jobs/{job_id}/edit?err={quote(_friendly_integrity_error(exc, name=name))}",
            status_code=303,
        )
    return RedirectResponse(f"/jobs?msg=Job+{quote(name)}+aktualisiert", status_code=303)


@router.post("/{job_id}/delete")
async def delete_job(job_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]):
    job = await session.get(Job, job_id)
    if job:
        await session.delete(job)
        await session.commit()
    return RedirectResponse("/jobs?msg=Job+gel%C3%B6scht", status_code=303)


@router.post("/{job_id}/toggle")
async def toggle_job(job_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]):
    job = await session.get(Job, job_id)
    job.enabled = not job.enabled
    if job.enabled:
        job.auto_completed = False
    await session.commit()
    return RedirectResponse("/jobs", status_code=303)


@router.post("/{job_id}/run-now")
async def run_job_now(job_id: uuid.UUID):
    await run_job.defer_async(job_id=str(job_id))
    return RedirectResponse("/jobs?msg=Job+wurde+zur+sofortigen+Ausf%C3%BChrung+eingeplant", status_code=303)


@router.get("/{job_id}/progress")
async def job_progress(request: Request, job_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]):
    """HTMX-Partial (per Polling nachgeladen, siehe jobs/list.html): zeigt, ob für diesen Job
    gerade noch Nachrichten ausgewertet werden und wie viele noch ausstehen - direkte Antwort auf
    "ist der Job fertig oder arbeitet er noch?", ohne dass man Container-Logs durchsuchen muss."""
    pending_by_job = await _pending_counts_by_job(session)
    running_mailbox_ids = await _running_mailbox_ids(session)
    mailbox_result = await session.execute(select(JobMailbox.mailbox_id).where(JobMailbox.job_id == job_id))
    job_mailbox_ids = {row[0] for row in mailbox_result.all()}
    return templates.TemplateResponse(
        request,
        "jobs/partials/progress_cell.html",
        {
            "job_id": job_id,
            "pending": pending_by_job.get(job_id, 0),
            "sync_running": bool(job_mailbox_ids & running_mailbox_ids),
        },
    )
