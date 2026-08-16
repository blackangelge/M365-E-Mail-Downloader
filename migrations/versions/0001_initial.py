"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False: die Typen werden unten explizit VOR den create_table-Aufrufen angelegt.
# Ohne dieses Flag versucht SQLAlchemy beim Erzeugen der Spalte den Typ ein zweites Mal
# anzulegen (CREATE TYPE ... - DuplicateObject), da checkfirst dabei nicht greift.
auth_type_enum = postgresql.ENUM("client_secret", "certificate", name="auth_type", create_type=False)
sync_status_enum = postgresql.ENUM("idle", "running", "error", name="sync_status", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM("client_secret", "certificate", name="auth_type").create(bind, checkfirst=True)
    postgresql.ENUM("idle", "running", "error", name="sync_status").create(bind, checkfirst=True)

    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("azure_tenant_id", sa.String(255), nullable=False),
        sa.Column("azure_client_id", sa.String(255), nullable=False),
        sa.Column("auth_type", auth_type_enum, nullable=False),
        sa.Column("client_secret_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("certificate_pem_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("certificate_password_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("certificate_thumbprint", sa.String(255), nullable=True),
        sa.Column("credentials_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "mailboxes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email_address", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "email_address", name="uq_mailbox_tenant_email"),
    )

    op.create_table(
        "mailbox_sync_state",
        sa.Column("mailbox_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("mailboxes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("delta_link", sa.Text(), nullable=True),
        sa.Column("last_delta_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sync_status_enum, nullable=False, server_default="idle"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_subfolder", sa.String(255), nullable=False),
        sa.Column("poll_interval_minutes", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "job_mailboxes",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("mailbox_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("mailboxes.id", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "job_filters",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("date_from", sa.Date(), nullable=True),
        sa.Column("date_to", sa.Date(), nullable=True),
    )

    op.create_table(
        "job_filter_extensions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("extension", sa.String(32), nullable=False),
        sa.UniqueConstraint("job_id", "extension", name="uq_job_extension"),
    )

    op.create_table(
        "job_filter_keywords",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("keyword_normalized", sa.String(255), nullable=False),
        sa.Column("keyword_display", sa.String(255), nullable=False),
        sa.UniqueConstraint("job_id", "keyword_normalized", name="uq_job_keyword"),
    )

    op.create_table(
        "processed_emails",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("mailbox_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("mailboxes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.String(512), nullable=False),
        sa.Column("internet_message_id", sa.String(998), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("from_address", sa.String(320), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("has_attachments", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("mailbox_id", "message_id", name="uq_processed_email"),
    )
    op.create_index("ix_processed_emails_mailbox_received", "processed_emails", ["mailbox_id", "received_at"])

    op.create_table(
        "job_message_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("processed_email_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("processed_emails.id", ondelete="CASCADE"), nullable=False),
        sa.Column("matched", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attachments_downloaded_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("job_id", "processed_email_id", name="uq_job_message_eval"),
    )

    op.create_table(
        "attachment_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("mailbox_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("mailboxes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("original_filename", sa.String(1024), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("stored_path", sa.String(2048), nullable=False),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("mailbox_id", "sha256", name="uq_attachment_mailbox_hash"),
    )
    op.create_index("ix_attachment_files_downloaded_at", "attachment_files", ["downloaded_at"])

    op.create_table(
        "attachment_sightings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("attachment_file_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("attachment_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("processed_email_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("processed_emails.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename_on_email", sa.String(1024), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("is_new_download", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("attachment_file_id", "processed_email_id", name="uq_sighting_file_email"),
    )
    op.create_index("ix_attachment_sightings_seen_at", "attachment_sightings", ["seen_at"])
    op.create_index("ix_attachment_sightings_job_seen", "attachment_sightings", ["job_id", "seen_at"])


def downgrade() -> None:
    op.drop_table("attachment_sightings")
    op.drop_table("attachment_files")
    op.drop_table("job_message_evaluations")
    op.drop_table("processed_emails")
    op.drop_table("job_filter_keywords")
    op.drop_table("job_filter_extensions")
    op.drop_table("job_filters")
    op.drop_table("job_mailboxes")
    op.drop_table("jobs")
    op.drop_table("mailbox_sync_state")
    op.drop_table("mailboxes")
    op.drop_table("tenants")

    bind = op.get_bind()
    sync_status_enum.drop(bind, checkfirst=True)
    auth_type_enum.drop(bind, checkfirst=True)
