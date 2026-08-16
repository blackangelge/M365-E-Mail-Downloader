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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import FilterSet, Job, JobMailbox, Mailbox, Tenant
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


@router.get("")
async def list_jobs(request: Request, session: Annotated[AsyncSession, Depends(get_session)]):
    result = await session.execute(
        select(Job, Tenant, FilterSet)
        .join(Tenant, Job.tenant_id == Tenant.id)
        .join(FilterSet, Job.filter_set_id == FilterSet.id)
        .order_by(Job.name)
    )
    jobs = [{"job": j, "tenant": t, "filter_set": fs} for j, t, fs in result.all()]
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
            "tenants": tenants_result.scalars().all(),
            "filter_sets": filter_sets_result.scalars().all(),
            "mailboxes": [],
        },
    )


@router.get("/{job_id}/edit")
async def edit_job_form(request: Request, job_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]):
    job = await session.get(Job, job_id)
    detail = await _load_job_detail(session, job)
    tenants_result = await session.execute(select(Tenant).order_by(Tenant.name))
    filter_sets_result = await session.execute(select(FilterSet).order_by(FilterSet.name))
    mailboxes_result = await session.execute(select(Mailbox).where(Mailbox.tenant_id == job.tenant_id))
    return templates.TemplateResponse(
        request,
        "jobs/form.html",
        {
            "active_nav": "jobs",
            "detail": detail,
            "tenants": tenants_result.scalars().all(),
            "filter_sets": filter_sets_result.scalars().all(),
            "mailboxes": mailboxes_result.scalars().all(),
        },
    )


@router.get("/mailboxes-for-tenant/{tenant_id}")
async def mailboxes_for_tenant(request: Request, tenant_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]):
    """HTMX-Partial: aktualisiert die Postfach-Checkboxen, wenn im Formular der Tenant gewechselt wird."""
    result = await session.execute(select(Mailbox).where(Mailbox.tenant_id == tenant_id).order_by(Mailbox.email_address))
    return templates.TemplateResponse(
        request, "jobs/partials/mailbox_checkboxes.html", {"mailboxes": result.scalars().all(), "selected_ids": set()}
    )


async def _save_job_mailboxes(session: AsyncSession, job: Job, mailbox_ids: list[uuid.UUID]) -> None:
    await session.execute(JobMailbox.__table__.delete().where(JobMailbox.job_id == job.id))
    for mid in mailbox_ids:
        session.add(JobMailbox(job_id=job.id, mailbox_id=mid))


@router.post("")
async def create_job(
    session: Annotated[AsyncSession, Depends(get_session)],
    name: Annotated[str, Form()],
    tenant_id: Annotated[uuid.UUID, Form()],
    filter_set_id: Annotated[uuid.UUID, Form()],
    target_subfolder: Annotated[str, Form()],
    poll_interval_minutes: Annotated[int, Form()] = 15,
    mailbox_ids: Annotated[list[uuid.UUID], Form()] = [],
):
    job = Job(
        name=name,
        tenant_id=tenant_id,
        filter_set_id=filter_set_id,
        target_subfolder=sanitize_path_segment(target_subfolder),
        poll_interval_minutes=poll_interval_minutes,
    )
    session.add(job)
    await session.flush()
    await _save_job_mailboxes(session, job, mailbox_ids)
    await session.commit()
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
    job = await session.get(Job, job_id)
    job.name = name
    job.filter_set_id = filter_set_id
    job.target_subfolder = sanitize_path_segment(target_subfolder)
    job.poll_interval_minutes = poll_interval_minutes
    job.enabled = bool(enabled)
    if job.enabled:
        job.auto_completed = False
    await _save_job_mailboxes(session, job, mailbox_ids)
    await session.commit()
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
