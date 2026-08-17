"""attachment skips: per Ausschluss-Filter übersprungene Anhänge, sichtbar im Kalender

Revision ID: 0004_attachment_skips
Revises: 0003_system_events
Create Date: 2026-08-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_attachment_skips"
down_revision: Union[str, None] = "0003_system_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

skip_reason_enum = postgresql.ENUM("extension", "keyword", name="skip_reason", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM("extension", "keyword", name="skip_reason").create(bind, checkfirst=True)

    op.create_table(
        "attachment_skips",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "processed_email_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("processed_emails.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename_on_email", sa.String(1024), nullable=False),
        sa.Column("reason", skip_reason_enum, nullable=False),
        sa.Column("matched_keyword", sa.String(255), nullable=True),
        sa.Column("skipped_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("job_id", "processed_email_id", "filename_on_email", name="uq_attachment_skip"),
    )
    op.create_index("ix_attachment_skips_skipped_at", "attachment_skips", ["skipped_at"])


def downgrade() -> None:
    op.drop_table("attachment_skips")
    bind = op.get_bind()
    skip_reason_enum.drop(bind, checkfirst=True)
