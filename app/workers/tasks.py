"""Procrastinate-Tasks: die komplette Pipeline von "Job ist fällig" bis "Anhang liegt auf Disk".

Kette: tick_scheduler (periodisch) -> run_job -> sync_then_match (pro Postfach) ->
evaluate_job_for_message (pro neuer Nachricht) -> download_attachment (pro Treffer).

Resumability: jede Stufe ist idempotent (Unique-Constraints / ON CONFLICT DO NOTHING,
delta_link- bzw. job_message_evaluations-Wasserzeichen). Procrastinate persistiert jeden
deferred/laufenden Task in Postgres; nach einem Container-Neustart übernimmt der neue Worker
verwaiste Tasks eines toten Workers automatisch und setzt an der letzten committeten Stelle fort.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.database import async_session_factory
from app.events import log_event
from app.graph.attachments import download_single_attachment, list_and_download_attachments
from app.graph.client_factory import get_graph_client
from app.graph.errors import PermanentGraphError
from app.graph.folders import list_inbox_and_subfolders
from app.graph.mailbox_scanner import scan_mailbox_delta
from app.graph.throttling import semaphore_for_tenant
from app.models import (
    AttachmentSkip,
    EventCategory,
    EventLevel,
    FilterSet,
    FilterSetExtension,
    FilterSetKeyword,
    Job,
    JobMailbox,
    JobMessageEvaluation,
    Mailbox,
    MailboxFolder,
    ProcessedEmail,
    SkipReason,
    SyncStatus,
    Tenant,
)
from app.workers.dedup import check_and_record, compute_sha256, record_sighting
from app.workers.filters import date_in_range, evaluate_attachment
from app.workers.locks import mailbox_sync_guard
from app.workers.procrastinate_app import app, graph_retry_strategy
from app.workers.storage import build_target_path, write_once

settings = get_settings()
logger = logging.getLogger("app")


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
        # queueing_lock verhindert, dass für dasselbe Postfach zwei sync_then_match-Läufe
        # gleichzeitig in der Warteschlange stehen - kann passieren, wenn mehrere Jobs dasselbe
        # Postfach beobachten und im selben Tick fällig werden, oder wenn ein Lauf länger dauert
        # als das Poll-Intervall des Jobs. AlreadyEnqueued ist dabei der Normalfall (kein Fehler):
        # der bereits wartende/laufende Sync deckt diesen Durchlauf ohnehin mit ab.
        try:
            await sync_then_match.configure(
                queueing_lock=f"mailbox-sync-{mailbox_id}"
            ).defer_async(job_id=job_id, mailbox_id=str(mailbox_id))
        except AlreadyEnqueued:
            continue


@app.task(queue="jobs", retry=graph_retry_strategy)
async def sync_then_match(job_id: str, mailbox_id: str) -> None:
    """Synchronisiert ALLE Ordner eines Postfachs (Posteingang + alle Unterordner, beliebig
    verschachtelt, rekursiv per Graph ermittelt - siehe app/graph/folders.py) per Delta-Query und
    deferred anschließend die Auswertung neuer Nachrichten für den auslösenden Job.

    Ein Fehler in einem einzelnen Ordner (z.B. während der Zugriff darauf zwischenzeitlich
    verweigert wird) blockiert NICHT die übrigen Ordner - jeder Ordner wird einzeln versucht,
    Fehler werden pro Ordner in MailboxFolder.status/last_error festgehalten. Bereits committeter
    Fortschritt einzelner Ordner bleibt in jedem Fall erhalten.

    Der Sync selbst ist postfachbezogen (nicht jobbezogen) - beobachten mehrere Jobs dasselbe
    Postfach, läuft der Sync entsprechend mehrfach, liefert aber ab dem zweiten Mal dank der
    bereits fortgeschrittenen `delta_link`s nur noch leere Seiten.
    """
    mailbox_uuid = uuid.UUID(mailbox_id)

    async with async_session_factory() as session:
        mailbox = await session.get(Mailbox, mailbox_uuid)
        if mailbox is None or not mailbox.is_active:
            return
        tenant = await session.get(Tenant, mailbox.tenant_id)
        if tenant is None or not tenant.is_active:
            return

    # Advisory-Lock (siehe app/workers/locks.py): verhindert, dass zwei Läufe für DASSELBE
    # Postfach gleichzeitig Ordner synchronisieren - kann passieren, wenn mehrere Jobs dasselbe
    # Postfach beobachten oder ein Lauf länger dauert als das Poll-Intervall (der vom Nutzer
    # beobachtete Fall bei großen Postfächern). Non-blocking: ein bereits laufender Sync deckt
    # diesen Durchlauf ohnehin ab, daher wird hier übersprungen statt gewartet.
    async with mailbox_sync_guard(mailbox_uuid) as acquired:
        if not acquired:
            logger.info(
                "Sync für Postfach %s übersprungen - ein anderer Lauf ist bereits aktiv.",
                mailbox.email_address,
            )
        else:
            try:
                client = await get_graph_client(tenant)
                async with semaphore_for_tenant(str(tenant.id)):
                    discovered = await list_inbox_and_subfolders(client, mailbox.email_address)
            except PermanentGraphError as exc:
                async with async_session_factory() as session:
                    await log_event(
                        session,
                        category=EventCategory.GRAPH_CONNECTION,
                        level=EventLevel.ERROR,
                        message=f"Verbindung zu M365 fehlgeschlagen (Postfach {mailbox.email_address}): {exc}",
                        tenant_id=tenant.id,
                        mailbox_id=mailbox_uuid,
                    )
                return  # kein Retry, kein Weiterlaufen zur Auswertung
            except Exception as exc:
                async with async_session_factory() as session:
                    await log_event(
                        session,
                        category=EventCategory.GRAPH_CONNECTION,
                        level=EventLevel.ERROR,
                        message=f"Verbindung zu M365 fehlgeschlagen (Postfach {mailbox.email_address}): {exc}",
                        tenant_id=tenant.id,
                        mailbox_id=mailbox_uuid,
                    )
                raise  # transient - Procrastinate versucht es per graph_retry_strategy erneut

            # Ordnerliste aktuell halten: neue Ordner anlegen, bestehende (samt ihrem delta_link!)
            # unangetastet lassen. Verschwundene/umbenannte Ordner werden bewusst nicht entfernt -
            # verwaiste Zeilen sind harmlos und der nächste Sync-Versuch für sie schlägt einfach fehl.
            async with async_session_factory() as session:
                existing_result = await session.execute(
                    select(MailboxFolder).where(MailboxFolder.mailbox_id == mailbox_uuid)
                )
                existing_by_graph_id = {f.graph_folder_id: f for f in existing_result.scalars().all()}

                folder_ids: list[uuid.UUID] = []
                for info in discovered:
                    existing = existing_by_graph_id.get(info.graph_folder_id)
                    if existing is not None:
                        if existing.display_path != info.display_path:
                            existing.display_path = info.display_path  # z.B. umbenannt/verschoben
                        folder_ids.append(existing.id)
                    else:
                        new_folder = MailboxFolder(
                            mailbox_id=mailbox_uuid,
                            graph_folder_id=info.graph_folder_id,
                            display_path=info.display_path,
                            status=SyncStatus.IDLE,
                        )
                        session.add(new_folder)
                        await session.flush()
                        folder_ids.append(new_folder.id)

                await session.commit()

            any_success = False
            for folder_id in folder_ids:
                try:
                    await _sync_single_folder(
                        client, mailbox_uuid, mailbox.email_address, folder_id, str(tenant.id)
                    )
                    any_success = True
                except Exception:  # noqa: BLE001 - ein fehlerhafter Ordner darf die anderen nicht blockieren
                    continue

            if any_success:
                async with async_session_factory() as session:
                    await log_event(
                        session,
                        category=EventCategory.GRAPH_CONNECTION,
                        level=EventLevel.INFO,
                        message=(
                            f"Verbindung zu M365 erfolgreich (Postfach {mailbox.email_address}, "
                            f"{len(folder_ids)} Ordner)."
                        ),
                        tenant_id=tenant.id,
                        mailbox_id=mailbox_uuid,
                    )

    await match_job_against_mailbox(job_id=job_id, mailbox_id=mailbox_id)


async def _sync_single_folder(
    client, mailbox_uuid: uuid.UUID, mailbox_address: str, folder_id: uuid.UUID, tenant_id: str
) -> None:
    """Delta-Sync für genau EINEN Ordner (siehe `sync_then_match` für die Ordner-Schleife).
    Wirft bei einem Fehler weiter, damit der Aufrufer weiß, dass dieser Ordner nicht erfolgreich
    war - der Fehler selbst ist zu diesem Zeitpunkt bereits in MailboxFolder persistiert.

    Die Semaphore wird für die GESAMTE Paginierung eines Ordners gehalten (nicht nur pro Seite) -
    die einzelnen Seiten hängen ohnehin sequenziell voneinander ab (jede braucht den nextLink der
    vorherigen), paralleles Anfordern würde also nichts bringen; die Begrenzung greift stattdessen
    zwischen verschiedenen Ordnern/Nachrichten desselben Tenants."""
    async with async_session_factory() as session:
        folder = await session.get(MailboxFolder, folder_id)
        delta_link = folder.delta_link
        graph_folder_id = folder.graph_folder_id
        folder.status = SyncStatus.RUNNING
        await session.commit()

    try:
        async with semaphore_for_tenant(tenant_id):
            page_iterator = scan_mailbox_delta(client, mailbox_address, delta_link, mail_folder=graph_folder_id)
            async for page in page_iterator:
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
                        folder = await session.get(MailboxFolder, folder_id)
                        folder.delta_link = page.next_delta_link
                        folder.last_delta_run_at = datetime.now(timezone.utc)
                        folder.status = SyncStatus.IDLE
                        folder.last_error = None

                    await session.commit()

    except PermanentGraphError as exc:
        async with async_session_factory() as session:
            folder = await session.get(MailboxFolder, folder_id)
            folder.status = SyncStatus.ERROR
            folder.last_error = str(exc)
            await session.commit()
        raise

    except Exception as exc:
        async with async_session_factory() as session:
            folder = await session.get(MailboxFolder, folder_id)
            folder.status = SyncStatus.ERROR
            folder.last_error = str(exc)
            await session.commit()
        raise


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

        # keyword_display (nicht keyword_normalized) verwenden, damit ein aufgezeichneter
        # Ausschluss (AttachmentSkip.matched_keyword) in der Original-Schreibweise angezeigt
        # werden kann - der Abgleich selbst ist ohnehin case-insensitive.
        kw_result = await session.execute(
            select(FilterSetKeyword.keyword_display).where(FilterSetKeyword.filter_set_id == job.filter_set_id)
        )
        keywords = [row[0] for row in kw_result.all()]

        mailbox = await session.get(Mailbox, email.mailbox_id)
        tenant = await session.get(Tenant, mailbox.tenant_id)

    matched_count = 0
    skips: list[tuple[str, SkipReason, str | None]] = []
    try:
        client = await get_graph_client(tenant)
        async with semaphore_for_tenant(str(tenant.id)):
            attachments = await list_and_download_attachments(client, mailbox.email_address, email.message_id)
        for attachment in attachments:
            evaluation = evaluate_attachment(
                attachment_filename=attachment.name,
                email_subject=email.subject,
                allowed_extensions=allowed_extensions,
                keywords=keywords,
            )
            if evaluation.matches:
                matched_count += 1
                await download_attachment.defer_async(
                    job_id=job_id,
                    processed_email_id=processed_email_id,
                    mailbox_id=str(email.mailbox_id),
                    graph_message_id=email.message_id,
                    graph_attachment_id=attachment.attachment_id,
                    attachment_name=attachment.name,
                )
            else:
                skips.append((attachment.name, SkipReason(evaluation.reason), evaluation.matched_keyword))
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

        # Ausschlüsse aufzeichnen, damit sie im Kalender sichtbar sind (siehe
        # app/web/routers/calendar.py) statt spurlos zu verschwinden. ON CONFLICT DO UPDATE (statt
        # DO NOTHING): nach einer Filter-Änderung kann sich der Ausschlussgrund für dieselbe
        # Nachricht ändern (z.B. anderes Keyword) - der Eintrag soll dann aktuell bleiben.
        for filename, reason, matched_keyword in skips:
            skip_stmt = (
                pg_insert(AttachmentSkip)
                .values(
                    processed_email_id=email_uuid,
                    job_id=job_uuid,
                    filename_on_email=filename,
                    reason=reason,
                    matched_keyword=matched_keyword,
                )
                .on_conflict_do_update(
                    constraint="uq_attachment_skip",
                    set_={"reason": reason, "matched_keyword": matched_keyword, "skipped_at": func.now()},
                )
            )
            await session.execute(skip_stmt)

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
    async with semaphore_for_tenant(str(tenant.id)):
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
            try:
                write_once(target_path, attachment.content)
            except OSError as exc:
                # Häufigste Ursache: lokale Antiviren-Software hat die temporäre Datei blockiert
                # (z.B. weil der Anhang als Schadsoftware erkannt wurde) - write_once() ist atomar
                # (Temp-Datei + os.replace), daher landet dabei NIE eine unvollständige oder
                # infizierte Datei am eigentlichen Zielpfad. Bisher war das nur in den rohen
                # Container-Logs sichtbar - jetzt zusätzlich als SystemEvent auf /system, mit
                # Betreff/Absender der auslösenden E-Mail, damit der Anhang manuell geprüft werden
                # kann. Eigene Session fürs Logging (siehe sync_then_match für dasselbe Muster) -
                # die aktuelle Session/Transaktion wird gleich durch das erneute Werfen verworfen.
                async with async_session_factory() as log_session:
                    await log_event(
                        log_session,
                        category=EventCategory.DOWNLOAD_ERROR,
                        level=EventLevel.ERROR,
                        message=(
                            f"Anhang '{attachment.name}' aus E-Mail '{email.subject}' "
                            f"(von {email.from_address}, Postfach {mailbox.email_address}) konnte "
                            f"nicht gespeichert werden: {exc}. Häufigste Ursache: Antiviren-Software "
                            f"hat die Datei beim Schreiben blockiert. Anhang manuell in der "
                            f"Original-E-Mail prüfen."
                        ),
                        tenant_id=tenant.id,
                        mailbox_id=mailbox_uuid,
                    )
                raise

        await record_sighting(
            session,
            attachment_file_id=dedup_result.attachment_file_id,
            processed_email_id=email.id,
            job_id=job.id,
            filename_on_email=attachment.name,
            is_new_download=dedup_result.is_new_download,
        )
        await session.commit()
