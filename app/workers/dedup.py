"""Duplikat-Erkennung: dieselbe PDF (per SHA-256-Inhalts-Hash) wird pro Postfach nur einmal
auf die Platte geschrieben, auch wenn sie mehrfach beobachtet wird (mehrere E-Mails, mehrere
Jobs, die dasselbe Postfach beobachten).

Der Dedup-Key ist bewusst `(mailbox_id, sha256)` - NICHT `(job_id, sha256)`: beobachten zwei Jobs
denselben Anhang im selben Postfach, schreibt nur der zuerst auswertende Job die Datei; der
zweite bekommt nur einen `attachment_sightings`-Eintrag mit `is_new_download=False`.

Ein Postgres-Advisory-Lock schützt die Race-Bedingung, wenn zwei Tasks (z.B. zwei Jobs auf
demselben Postfach) gleichzeitig denselben Anhang auswerten.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AttachmentFile, AttachmentSighting


def compute_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _advisory_lock_key(mailbox_id: uuid.UUID, sha256: str) -> int:
    """Deterministischer 64-bit-Schlüssel für pg_advisory_xact_lock aus (mailbox_id, sha256)."""
    digest = hashlib.sha256(f"{mailbox_id}:{sha256}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


@dataclass(slots=True)
class DedupResult:
    attachment_file_id: uuid.UUID
    is_new_download: bool


async def check_and_record(
    session: AsyncSession,
    *,
    mailbox_id: uuid.UUID,
    sha256: str,
    original_filename: str,
    content_type: str | None,
    size_bytes: int,
    stored_path_if_new: str,
) -> DedupResult:
    """Prüft unter einem Postgres-Advisory-Lock, ob `(mailbox_id, sha256)` bereits existiert.

    - Existiert die Kombination bereits: es wird NICHTS geschrieben, nur die vorhandene
      `attachment_file_id` zurückgegeben (`is_new_download=False`).
    - Sonst: eine neue `attachment_files`-Zeile wird angelegt (`is_new_download=True`) - der
      Aufrufer (download_attachment-Task) schreibt die Datei via `storage.write_once()` und
      committet beides (DB-Zeile + Datei) in derselben Transaktion/demselben Schritt, damit nie
      eine Datei ohne DB-Eintrag oder umgekehrt entsteht.

    Der Advisory-Lock wird automatisch am Ende der umgebenden Transaktion freigegeben
    (`pg_advisory_xact_lock`) und schützt gegen zwei Tasks, die gleichzeitig denselben Anhang im
    selben Postfach auswerten (z.B. zwei Jobs auf demselben Postfach).
    """
    lock_key = _advisory_lock_key(mailbox_id, sha256)
    await session.execute(select(func.pg_advisory_xact_lock(lock_key)))

    existing = await session.execute(
        select(AttachmentFile).where(
            AttachmentFile.mailbox_id == mailbox_id, AttachmentFile.sha256 == sha256
        )
    )
    existing_file = existing.scalar_one_or_none()
    if existing_file is not None:
        return DedupResult(attachment_file_id=existing_file.id, is_new_download=False)

    new_file = AttachmentFile(
        mailbox_id=mailbox_id,
        sha256=sha256,
        original_filename=original_filename,
        content_type=content_type,
        size_bytes=size_bytes,
        stored_path=stored_path_if_new,
    )
    session.add(new_file)
    await session.flush()  # vergibt new_file.id, ohne zu committen
    return DedupResult(attachment_file_id=new_file.id, is_new_download=True)


async def record_sighting(
    session: AsyncSession,
    *,
    attachment_file_id: uuid.UUID,
    processed_email_id: uuid.UUID,
    job_id: uuid.UUID,
    filename_on_email: str,
    is_new_download: bool,
) -> None:
    """Idempotent per `ON CONFLICT DO NOTHING`: dieselbe (job_id, attachment_file_id,
    processed_email_id)-Sichtung kann mehrfach zu schreiben versucht werden, wenn eine
    `download_attachment`-Task doppelt läuft (z.B. durch manuelles mehrfaches "Jetzt ausführen"
    oder einen Neustart mitten in der Verarbeitung - Procrastinate garantiert "at least once",
    nicht "exactly once"). Ein einfaches `session.add()` würde dabei eine `UniqueViolation` werfen
    statt den Task sauber (erneut) erfolgreich enden zu lassen.

    job_id ist bewusst TEIL des Unique-Keys (siehe app/models.py::AttachmentSighting) - sonst
    bekäme bei zwei Jobs auf demselben Postfach mit überlappenden Filtern nur der zuerst
    auswertende Job je einen Sichtungs-Eintrag für einen gegebenen Anhang/eine gegebene E-Mail."""
    stmt = (
        pg_insert(AttachmentSighting)
        .values(
            attachment_file_id=attachment_file_id,
            processed_email_id=processed_email_id,
            job_id=job_id,
            filename_on_email=filename_on_email,
            is_new_download=is_new_download,
        )
        .on_conflict_do_nothing(constraint="uq_sighting_job_file_email")
    )
    await session.execute(stmt)
