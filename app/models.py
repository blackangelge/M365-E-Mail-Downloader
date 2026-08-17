"""SQLAlchemy-ORM-Modelle für alle App-eigenen Tabellen.

Die Tabellen von Procrastinate (`procrastinate_jobs`, `procrastinate_events`, ...) werden
bewusst NICHT hier abgebildet — sie werden ausschließlich über `procrastinate schema --apply`
verwaltet (siehe scripts/entrypoint.sh), nie über Alembic.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _pg_enum(enum_cls: type[enum.Enum], name: str) -> Enum:
    """SQLAlchemy schreibt für (str, Enum)-Subklassen standardmäßig den Python-Member-NAMEN
    (z.B. "CLIENT_SECRET") in die DB, nicht den Enum-WERT ("client_secret") - der aber dem
    Postgres-ENUM-Typ aus der Alembic-Migration entspricht. `values_callable` erzwingt die
    Verwendung der `.value`s, damit DB-Typ und Python-Enum konsistent bleiben."""
    return Enum(enum_cls, name=name, values_callable=lambda obj: [e.value for e in obj])


class AuthType(str, enum.Enum):
    CLIENT_SECRET = "client_secret"
    CERTIFICATE = "certificate"


class SyncStatus(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"


class EventCategory(str, enum.Enum):
    STARTUP = "startup"  # z.B. Download-Ordner-Mount-Prüfung
    GRAPH_CONNECTION = "graph_connection"  # M365-Verbindungsversuche (Sync + manueller Test)


class EventLevel(str, enum.Enum):
    INFO = "info"
    ERROR = "error"


class SkipReason(str, enum.Enum):
    EXTENSION = "extension"  # Dateiendung nicht in den erlaubten Endungen des Filters
    KEYWORD = "keyword"  # Ausschluss-Keyword im Betreff oder Dateinamen gefunden


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    azure_tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    azure_client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_type: Mapped[AuthType] = mapped_column(_pg_enum(AuthType, "auth_type"), nullable=False)

    client_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    certificate_pem_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    certificate_password_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    certificate_thumbprint: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Wird bei jeder Änderung der Zugangsdaten erhöht -> invalidiert den Graph-Client-Cache.
    credentials_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    mailboxes: Mapped[list["Mailbox"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    jobs: Mapped[list["Job"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class Mailbox(Base):
    __tablename__ = "mailboxes"
    __table_args__ = (UniqueConstraint("tenant_id", "email_address", name="uq_mailbox_tenant_email"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    email_address: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant: Mapped[Tenant] = relationship(back_populates="mailboxes")
    folders: Mapped[list["MailboxFolder"]] = relationship(back_populates="mailbox", cascade="all, delete-orphan")


class MailboxFolder(Base):
    """Sync-Status pro Postfach UND Ordner (Posteingang + all seine Unterordner, beliebig
    verschachtelt) - Anhänge können in per Regel oder manuell sortierten Unterordnern des
    Posteingangs landen, deshalb reicht ein Sync-Status pro Postfach allein nicht aus. Jeder
    Ordner hat sein eigenes Delta-Token, da Graph-Delta-Queries pro Ordner laufen."""

    __tablename__ = "mailbox_folders"
    __table_args__ = (UniqueConstraint("mailbox_id", "graph_folder_id", name="uq_mailbox_folder"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    mailbox_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mailboxes.id", ondelete="CASCADE"), nullable=False)
    graph_folder_id: Mapped[str] = mapped_column(String(512), nullable=False)
    display_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    delta_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_delta_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[SyncStatus] = mapped_column(
        _pg_enum(SyncStatus, "sync_status"), nullable=False, default=SyncStatus.IDLE
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    mailbox: Mapped[Mailbox] = relationship(back_populates="folders")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Unterordner unter DOWNLOAD_ROOT, pro Job frei definierbar.
    target_subfolder: Mapped[str] = mapped_column(String(255), nullable=False)

    poll_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # auto_completed=True, wenn der Scheduler den Job wegen erreichtem date_to automatisch
    # pausiert hat (enabled wird dabei auf False gesetzt) - UI unterscheidet dies von
    # manuellem Deaktivieren.
    auto_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Ein Filter-Set kann von mehreren Jobs gemeinsam genutzt werden (siehe FilterSet unten) -
    # deshalb hier kein CASCADE-Delete: ein Filter-Set darf erst gelöscht werden, wenn ihn kein
    # Job mehr referenziert (siehe ondelete="RESTRICT" an FilterSet-Seite bzw. Prüfung im Router).
    filter_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("filter_sets.id"), nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="jobs")
    filter_set: Mapped["FilterSet"] = relationship(back_populates="jobs")
    mailbox_links: Mapped[list["JobMailbox"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobMailbox(Base):
    """Ein Postfach darf zu höchstens EINEM Job gehören (siehe unique=True auf mailbox_id) - sonst
    würden zwei Jobs mit überlappenden Filtern denselben Anhang matchen, aber das
    postfachweite Content-Dedup (siehe app/workers/dedup.py) sorgt dafür, dass nur der zuerst
    auswertende Job tatsächlich eine Datei in seinen eigenen Zielordner schreibt - der zweite Job
    bekäme nie eine eigene Kopie, was beim Anlegen mehrerer Jobs pro Postfach zu genau der
    Verwirrung führt, die diese Beschränkung verhindert."""

    __tablename__ = "job_mailboxes"

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    mailbox_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mailboxes.id", ondelete="CASCADE"), primary_key=True, unique=True
    )

    job: Mapped[Job] = relationship(back_populates="mailbox_links")
    mailbox: Mapped[Mailbox] = relationship()


class FilterSet(Base):
    """Eigenständiger, wiederverwendbarer Filter: Datumsbereich + Endungen + Ausschluss-Keywords.

    Wird in einem eigenen Menü (`/filters`) unabhängig von Jobs verwaltet; ein Job wählt genau
    ein FilterSet aus, statt seine eigenen Filter zu definieren - so muss derselbe Filter nicht
    für mehrere Jobs dupliziert werden. Mehrere Jobs können denselben FilterSet referenzieren.
    """

    __tablename__ = "filter_sets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    date_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    extensions: Mapped[list["FilterSetExtension"]] = relationship(
        back_populates="filter_set", cascade="all, delete-orphan"
    )
    keywords: Mapped[list["FilterSetKeyword"]] = relationship(
        back_populates="filter_set", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["Job"]] = relationship(back_populates="filter_set")


class FilterSetExtension(Base):
    __tablename__ = "filter_set_extensions"
    __table_args__ = (UniqueConstraint("filter_set_id", "extension", name="uq_filter_set_extension"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    filter_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("filter_sets.id", ondelete="CASCADE"), nullable=False)
    # lowercased, mit führendem Punkt, z.B. ".pdf"
    extension: Mapped[str] = mapped_column(String(32), nullable=False)

    filter_set: Mapped[FilterSet] = relationship(back_populates="extensions")


class FilterSetKeyword(Base):
    """Ausschluss-Keyword: E-Mails/Anhänge, deren Betreff oder Dateiname einen dieser Begriffe
    enthält, werden NICHT heruntergeladen (case-insensitive Teilstring-Suche)."""

    __tablename__ = "filter_set_keywords"
    __table_args__ = (UniqueConstraint("filter_set_id", "keyword_normalized", name="uq_filter_set_keyword"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    filter_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("filter_sets.id", ondelete="CASCADE"), nullable=False)
    keyword_normalized: Mapped[str] = mapped_column(String(255), nullable=False)  # lowercased
    keyword_display: Mapped[str] = mapped_column(String(255), nullable=False)  # Original-Schreibweise

    filter_set: Mapped[FilterSet] = relationship(back_populates="keywords")


class ProcessedEmail(Base):
    """Jede per Delta-Sync gesehene Nachricht eines Postfachs (unabhängig von Job-Treffern)."""

    __tablename__ = "processed_emails"
    __table_args__ = (UniqueConstraint("mailbox_id", "message_id", name="uq_processed_email"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    mailbox_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mailboxes.id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[str] = mapped_column(String(512), nullable=False)  # Graph message id
    internet_message_id: Mapped[str | None] = mapped_column(String(998), nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    has_attachments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobMessageEvaluation(Base):
    """Idempotenz-Anker: eine Nachricht wird pro Job nur einmal ausgewertet."""

    __tablename__ = "job_message_evaluations"
    __table_args__ = (UniqueConstraint("job_id", "processed_email_id", name="uq_job_message_eval"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    processed_email_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("processed_emails.id", ondelete="CASCADE"), nullable=False
    )
    matched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attachments_downloaded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AttachmentFile(Base):
    """Eine Zeile pro eindeutigem Anhangs-Inhalt (SHA-256) je Postfach - die eigentliche Datei auf Disk."""

    __tablename__ = "attachment_files"
    __table_args__ = (UniqueConstraint("mailbox_id", "sha256", name="uq_attachment_mailbox_hash"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    mailbox_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mailboxes.id", ondelete="CASCADE"), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Pfad relativ zu DOWNLOAD_ROOT, z.B. "job-a/2026_08_16_rechnung.pdf"
    stored_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AttachmentSighting(Base):
    """Jedes Vorkommen eines Anhangs (auch Wiederholungen, die dank Dedup nicht neu geschrieben wurden)."""

    __tablename__ = "attachment_sightings"
    __table_args__ = (
        # WICHTIG: job_id ist Teil des Unique-Keys, NICHT nur (attachment_file_id,
        # processed_email_id) - sonst kann bei zwei Jobs auf demselben Postfach mit
        # überlappenden Filtern nur der ZUERST auswertende Job jemals einen Sichtungs-Eintrag
        # bekommen; der zweite Job würde beim "ON CONFLICT DO NOTHING" (siehe
        # app/workers/dedup.py::record_sighting) stillschweigend leer ausgehen, obwohl er den
        # Anhang ebenfalls korrekt erkannt hat (nur eben nicht erneut herunterladen musste).
        UniqueConstraint(
            "job_id", "attachment_file_id", "processed_email_id", name="uq_sighting_job_file_email"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    attachment_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("attachment_files.id", ondelete="CASCADE"), nullable=False
    )
    processed_email_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("processed_emails.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    filename_on_email: Mapped[str] = mapped_column(String(1024), nullable=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_new_download: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AttachmentSkip(Base):
    """Ein Anhang, der wegen des Ausschluss-Filters (Endung oder Keyword) NICHT heruntergeladen
    wurde. Anders als AttachmentSighting gibt es hier keinen AttachmentFile-Bezug, da der Inhalt
    nie heruntergeladen und somit auch kein SHA-256-Hash gebildet wurde - übersprungene Anhänge
    werden rein anhand von E-Mail/Job identifiziert. Macht übersprungene E-Mails im Kalender
    sichtbar (siehe app/web/routers/calendar.py), statt spurlos zu verschwinden."""

    __tablename__ = "attachment_skips"
    __table_args__ = (
        UniqueConstraint("job_id", "processed_email_id", "filename_on_email", name="uq_attachment_skip"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    processed_email_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("processed_emails.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    filename_on_email: Mapped[str] = mapped_column(String(1024), nullable=False)
    reason: Mapped[SkipReason] = mapped_column(_pg_enum(SkipReason, "skip_reason"), nullable=False)
    matched_keyword: Mapped[str | None] = mapped_column(String(255), nullable=True)
    skipped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SystemEvent(Base):
    """Betriebs-Ereignisse, die in der Weboberfläche sichtbar sein sollen, statt nur in den
    Docker-Container-Logs zu verschwinden: Download-Ordner-Mount-Prüfung beim Start, sowie jeder
    Verbindungsversuch zu M365 (Sync-Läufe und manuelle "Verbindung testen"-Klicks)."""

    __tablename__ = "system_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    category: Mapped[EventCategory] = mapped_column(_pg_enum(EventCategory, "event_category"), nullable=False)
    level: Mapped[EventLevel] = mapped_column(_pg_enum(EventLevel, "event_level"), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True)
    mailbox_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mailboxes.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
