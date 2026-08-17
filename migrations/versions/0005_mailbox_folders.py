"""mailbox_folders: Sync-Status pro Postfach UND Ordner statt nur pro Postfach

Ersetzt mailbox_sync_state (1 Zeile je Postfach, nur Posteingang) durch mailbox_folders
(1 Zeile je Postfach+Ordner) - Grundlage dafür, auch Unterordner des Posteingangs zu
verarbeiten, nicht nur den Posteingang selbst.

Revision ID: 0005_mailbox_folders
Revises: 0004_attachment_skips
Create Date: 2026-08-17

"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_mailbox_folders"
down_revision: Union[str, None] = "0004_attachment_skips"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "mailbox_folders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "mailbox_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("mailboxes.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("graph_folder_id", sa.String(512), nullable=False),
        sa.Column("display_path", sa.String(1024), nullable=False),
        sa.Column("delta_link", sa.Text(), nullable=True),
        sa.Column("last_delta_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM("idle", "running", "error", name="sync_status", create_type=False),
            nullable=False,
            server_default="idle",
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("mailbox_id", "graph_folder_id", name="uq_mailbox_folder"),
    )

    # --- Datenmigration: je Zeile in mailbox_sync_state (Posteingang, alter 1-Ordner-Stand) ---
    #     eine mailbox_folders-Zeile mit graph_folder_id="inbox" anlegen, damit der bisherige
    #     Delta-Fortschritt für den Posteingang nicht verloren geht.
    rows = bind.execute(
        sa.text(
            "SELECT mailbox_id, delta_link, last_delta_run_at, status, last_error FROM mailbox_sync_state"
        )
    ).fetchall()

    for mailbox_id, delta_link, last_delta_run_at, status, last_error in rows:
        bind.execute(
            sa.text(
                "INSERT INTO mailbox_folders "
                "(id, mailbox_id, graph_folder_id, display_path, delta_link, last_delta_run_at, status, last_error) "
                "VALUES (:id, :mailbox_id, 'inbox', 'Posteingang', :delta_link, :last_delta_run_at, :status, :last_error)"
            ),
            {
                "id": uuid.uuid4(),
                "mailbox_id": mailbox_id,
                "delta_link": delta_link,
                "last_delta_run_at": last_delta_run_at,
                "status": status,
                "last_error": last_error,
            },
        )

    op.drop_table("mailbox_sync_state")


def downgrade() -> None:
    # Bewusst nicht unterstützt (verlustbehaftete Migration zurück auf 1 Ordner pro Postfach) -
    # im Zweifel ein DB-Backup vor dem Upgrade verwenden.
    raise NotImplementedError("Downgrade von 0005_mailbox_folders wird nicht unterstützt.")
