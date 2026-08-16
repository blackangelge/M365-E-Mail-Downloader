"""Procrastinate-Tasks: die komplette Pipeline von "Job ist fällig" bis "Anhang liegt auf Disk".

Kette: tick_scheduler (periodisch) -> run_job -> sync_then_match (pro Postfach) ->
evaluate_job_for_message (pro neuer Nachricht) -> download_attachment (pro Treffer).

Resumability: jede Stufe ist idempotent (Unique-Constraints / ON CONFLICT DO NOTHING,
delta_link- bzw. job_message_evaluations-Wasserzeichen). Procrastinate persistiert jeden
deferred/laufenden Task in Postgres; nach einem Container-Neustart übernimmt der neue Worker
verwaiste Tasks eines toten Workers automatisch und setzt an der letzten committeten Stelle fort.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.database import async_session_factory
from app.graph.attachments import download_single_attachment, list_and_download_attachments
from app.graph.client_factory import get_graph_client
from app.graph.errors import PermanentGraphError
from app.graph.mailbox_scanner import scan_mailbox_delta
from app.models import (
    FilterSet,
    FilterSetExtension,
    FilterSetKeyword,
    Job,
    JobMailbox,
    JobMessageEvaluation,
    Mailbox,
    MailboxSyncState,
    ProcessedEmail,
    SyncStatus,
    Tenant,
)
from app.workers.dedup import check_and_record, compute_sha256, record_sighting
from app.workers.filters import attachment_matches, date_in_range
from app.workers.procrastinate_app import app, graph_retry_strategy
from app.workers.storage import build_target_path, write_once

settings = get_settings()


@app.periodic(cron="* * * * *")
@app.task(queue="scheduler", pass_context=False)
async def tick_scheduler(timestamp: int) -> None:
    """Läuft jede Minute (Procrastinate-eigene Auflösung). Deferred `run_job` für jeden fälligen,
    aktivierten Job und pausiert Jobs automatisch, deren `date_to` bereits erreicht wurde."""
    now = datetime.now(timezone.utc)
    today = now.date()

    async with async_session_factory() as session:
        result = await session.execute(
            select(Job).where(Job.enabled.is_(True)).where(
                (Job.next_run_at.is_(None)) | (Job.next_run_at <= now)
            )
        )
        due_jobs = result.scalars().all()

        for job in due_jobs:
            filter_set = await session.get(FilterSet, job.filter_set_id)
            if filter_set and filter_set.date_to and filter_set.date_to < today:
                # Kein zukünftiger Lauf kann noch etwas finden -> Job automatisch pausieren.
                job.enabled = False
                job.auto_completed = True
                continue

            job.next_run_at = now + timedelta(minutes=job.poll_interval_minutes)
            job.last_run_at = now

        await session.commit()

        for job in due_jobs:
            if job.enabled:
                await run_job.defer_async(job_id=str(job.id))


@app.task(queue="jobs")
async def run_job(job_id: str) -> None:
    """Stößt für jedes dem Job zugeordnete Postfach einen Sync+Auswertungs-Lauf an."""
    async with async_session_factory() as session:
        job = await session.get(Job, uuid.UUID(job_id))
        if job is None or not job.enabled:
            return
        result = await session.execute(select(JobMailbox.mailbox_id).where(JobMailbox.job_id == job.id))
        mailbox_ids = [row[0] for row in result.all()]

    for mailbox_id in mailbox_ids:
        await sync_then_match.defer_async(job_id=job_id, mailbox_id=str(mailbox_id))


@app.task(queue="jobs", retry=graph_retry_strategy)
async def sync_then_match(job_id: str, mailbox_id: str) -> None:
    """Synchronisiert ein Postfach per Graph-Delta-Query und deferred anschließend die
    Auswertung neuer Nachrichten für den auslösenden Job.

    Der Delta-Sync selbst ist postfachbezogen (nicht jobbezogen) - beobachten mehrere Jobs
    dasselbe Postfach, läuft der Sync entsprechend mehrfach, liefert aber ab dem zweiten Mal
    dank des bereits fortgeschrittenen `delta_link` nur noch eine leere Seite.
    """
    mailbox_uuid = uuid.UUID(mailbox_id)

    async with async_session_factory() as session:
        mailbox = await session.get(Mailbox, mailbox_uuid)
        if mailbox is None or not mailbox.is_active:
            return
        tenant = await session.get(Tenant, mailbox.tenant_id)
        if tenant is None or not tenant.is_active:
            return

        sync_state = await session.get(MailboxSyncState, mailbox_uuid)
        if sync_state is None:
            sync_state = MailboxSyncState(mailbox_id=mailbox_uuid, status=SyncStatus.IDLE)
            session.add(sync_state)
            await session.flush()

        delta_link = sync_state.delta_link
        sync_state.status = SyncStatus.RUNNING
        await session.commit()

    try:
        client = await get_graph_client(tenant)
        async for page in scan_mailbox_delta(client, mailbox.email_address, delta_link):
            async with async_session_factory() as session:
                for msg in page.messages:
                    stmt = (
                        pg_insert(ProcessedEmail)
                        .values(
                            mailbox_id=mailbox_uuid,
                            message_id=msg.message_id,
                            internet_message_id=msg.internet_message_id,
                            subject=msg.subject,
                            from_address=msg.from_address,
                            received_at=msg.received_at,
                            has_attachments=msg.has_attachments,
                        )
                        .on_conflict_do_update(
                            constraint="uq_processed_email",
                            set_={
                                "subject": msg.subject,
                                "has_attachments": msg.has_attachments,
                            },
                        )
                    )
                    await session.execute(stmt)

                if page.next_delta_link:
                    sync_state = await session.get(MailboxSyncState, mailbox_uuid)
                    sync_state.delta_link = page.next_delta_link
                    sync_state.last_delta_run_at = datetime.now(timezone.utc)
                    sync_state.status = SyncStatus.IDLE
                    sync_state.last_error = None

                await session.commit()

    except PermanentGraphError as exc:
        async with async_session_factory() as session:
            sync_state = await session.get(MailboxSyncState, mailbox_uuid)
            sync_state.status = SyncStatus.ERROR
            sync_state.last_error = str(exc)
            await session.commit()
        return  # kein Retry, kein Weiterlaufen zur Auswertung

    except Exception as exc:
        # Transiente Fehler (Netzwerk, 5xx, Throttling, ...) sollen weiterhin von Procrastinate
        # per graph_retry_strategy wiederholt werden (daher hier "raise" statt "return") - aber
        # der Status wird trotzdem sichtbar auf "Fehler" gesetzt, statt scheinbar endlos bei
        # "läuft" hängen zu bleiben, während im Hintergrund wiederholt fehlgeschlagen wird. Sobald
        # ein Versuch erfolgreich ist, wird der Status oben wieder auf IDLE zurückgesetzt.
        async with async_session_factory() as session:
            sync_state = await session.get(MailboxSyncState, mailbox_uuid)
            sync_state.status = SyncStatus.ERROR
            sync_state.last_error = str(exc)
            await session.commit()
        raise

    await match_job_against_mailbox(job_id=job_id, mailbox_id=mailbox_id)


async def match_job_against_mailbox(job_id: str, mailbox_id: str) -> None:
    """Findet für den Job noch nicht ausgewertete Nachrichten des Postfachs (innerhalb des
    Datumsfilters) und deferred `evaluate_job_for_message` je Nachricht."""
    job_uuid, mailbox_uuid = uuid.UUID(job_id), uuid.UUID(mailbox_id)

    async with async_session_factory() as session:
        job = await session.get(Job, job_uuid)
        filter_set = await session.get(FilterSet, job.filter_set_id) if job else None
        date_from = filter_set.date_from if filter_set else None
        date_to = filter_set.date_to if filter_set else None

        already_evaluated = select(JobMessageEvaluation.processed_email_id).where(
            JobMessageEvaluation.job_id == job_uuid
        )
        result = await session.execute(
            select(ProcessedEmail)
            .where(ProcessedEmail.mailbox_id == mailbox_uuid)
            .where(ProcessedEmail.has_attachments.is_(True))
            .where(ProcessedEmail.id.not_in(already_evaluated))
        )
        candidates = result.scalars().all()

    for email in candidates:
        if not date_in_range(email.received_at, date_from, date_to):
            continue
        await evaluate_job_for_message.defer_async(job_id=job_id, processed_email_id=str(email.id))


@app.task(queue="jobs", retry=graph_retry_strategy)
async def evaluate_job_for_message(job_id: str, processed_email_id: str) -> None:
    """Prüft die Anhänge einer Nachricht gegen die Endungs-/Keyword-Filter des Jobs und deferred
    `download_attachment` für jeden Treffer. Idempotent über `job_message_evaluations`."""
    job_uuid, email_uuid = uuid.UUID(job_id), uuid.UUID(processed_email_id)

    async with async_session_factory() as session:
        job = await session.get(Job, job_uuid)
        email = await session.get(ProcessedEmail, email_uuid)
        if job is None or email is None:
            return

        ext_result = await session.execute(
            select(FilterSetExtension.extension).where(FilterSetExtension.filter_set_id == job.filter_set_id)
        )
        allowed_extensions = {row[0] for row in ext_result.all()}

        kw_result = await session.execute(
            select(FilterSetKeyword.keyword_normalized).where(FilterSetKeyword.filter_set_id == job.filter_set_id)
        )
        keywords = [row[0] for row in kw_result.all()]

        mailbox = await session.get(Mailbox, email.mailbox_id)
        tenant = await session.get(Tenant, mailbox.tenant_id)

    matched_count = 0
    try:
        client = await get_graph_client(tenant)
        attachments = await list_and_download_attachments(client, mailbox.email_address, email.message_id)
        for attachment in attachments:
            if attachment_matches(
                attachment_filename=attachment.name,
                email_subject=email.subject,
                allowed_extensions=allowed_extensions,
                keywords=keywords,
            ):
                matched_count += 1
                await download_attachment.defer_async(
                    job_id=job_id,
                    processed_email_id=processed_email_id,
                    mailbox_id=str(email.mailbox_id),
                    graph_message_id=email.message_id,
                    graph_attachment_id=attachment.attachment_id,
                    attachment_name=attachment.name,
                )
    except PermanentGraphError:
        # Zugangsdaten wurden zwischenzeitlich ungültig - Auswertung nicht als erledigt markieren,
        # damit sie bei einem erneuten Job-Lauf mit gültigen Credentials nachgeholt wird.
        return

    async with async_session_factory() as session:
        stmt = (
            pg_insert(JobMessageEvaluation)
            .values(
                job_id=job_uuid,
                processed_email_id=email_uuid,
                matched=matched_count > 0,
                attachments_downloaded_count=matched_count,
            )
            .on_conflict_do_nothing(constraint="uq_job_message_eval")
        )
        await session.execute(stmt)
        await session.commit()


@app.task(queue="downloads", retry=graph_retry_strategy)
async def download_attachment(
    job_id: str,
    processed_email_id: str,
    mailbox_id: str,
    graph_message_id: str,
    graph_attachment_id: str,
    attachment_name: str,
) -> None:
    """Lädt einen einzelnen Anhang, prüft/verbucht das Dedup und schreibt ihn ggf. auf Disk."""
    mailbox_uuid = uuid.UUID(mailbox_id)

    async with async_session_factory() as session:
        mailbox = await session.get(Mailbox, mailbox_uuid)
        tenant = await session.get(Tenant, mailbox.tenant_id)
        job = await session.get(Job, uuid.UUID(job_id))
        email = await session.get(ProcessedEmail, uuid.UUID(processed_email_id))

    client = await get_graph_client(tenant)
    attachment = await download_single_attachment(
        client, mailbox.email_address, graph_message_id, graph_attachment_id
    )
    sha256 = compute_sha256(attachment.content)

    async with async_session_factory() as session:
        target_path = build_target_path(
            settings.download_root, job.target_subfolder, email.received_at or datetime.now(timezone.utc), attachment.name
        )
        relative_path = str(target_path.relative_to(settings.download_root))

        dedup_result = await check_and_record(
            session,
            mailbox_id=mailbox_uuid,
            sha256=sha256,
            original_filename=attachment.name,
            content_type=attachment.content_type,
            size_bytes=attachment.size_bytes,
            stored_path_if_new=relative_path,
        )

        if dedup_result.is_new_download:
            write_once(target_path, attachment.content)

        await record_sighting(
            session,
            attachment_file_id=dedup_result.attachment_file_id,
            processed_email_id=email.id,
            job_id=job.id,
            filename_on_email=attachment.name,
            is_new_download=dedup_result.is_new_download,
        )
        await session.commit()
