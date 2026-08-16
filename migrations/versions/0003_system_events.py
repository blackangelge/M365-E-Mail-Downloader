"""system events: Ordner-Check + M365-Verbindungs-Historie, sichtbar in der Weboberfläche

Revision ID: 0003_system_events
Revises: 0002_filter_sets
Create Date: 2026-08-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_system_events"
down_revision: Union[str, None] = "0002_filter_sets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

event_category_enum = postgresql.ENUM("startup", "graph_connection", name="event_category", create_type=False)
event_level_enum = postgresql.ENUM("info", "error", name="event_level", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM("startup", "graph_connection", name="event_category").create(bind, checkfirst=True)
    postgresql.ENUM("info", "error", name="event_level").create(bind, checkfirst=True)

    op.create_table(
        "system_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("category", event_category_enum, nullable=False),
        sa.Column("level", event_level_enum, nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "mailbox_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mailboxes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_system_events_created_at", "system_events", ["created_at"])
    op.create_index("ix_system_events_category_created_at", "system_events", ["category", "created_at"])


def downgrade() -> None:
    op.drop_table("system_events")
    bind = op.get_bind()
    event_level_enum.drop(bind, checkfirst=True)
    event_category_enum.drop(bind, checkfirst=True)
