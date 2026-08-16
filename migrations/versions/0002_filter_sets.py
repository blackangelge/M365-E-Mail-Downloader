"""filter sets: standalone reusable filters, replacing per-job filter tables

Revision ID: 0002_filter_sets
Revises: 0001_initial
Create Date: 2026-08-16

"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_filter_sets"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "filter_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("date_from", sa.Date(), nullable=True),
        sa.Column("date_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "filter_set_extensions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "filter_set_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("filter_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("extension", sa.String(32), nullable=False),
        sa.UniqueConstraint("filter_set_id", "extension", name="uq_filter_set_extension"),
    )
    op.create_table(
        "filter_set_keywords",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "filter_set_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("filter_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("keyword_normalized", sa.String(255), nullable=False),
        sa.Column("keyword_display", sa.String(255), nullable=False),
        sa.UniqueConstraint("filter_set_id", "keyword_normalized", name="uq_filter_set_keyword"),
    )

    # --- Datenmigration: pro bestehendem Job ein FilterSet aus dessen bisherigen Filtern
    #     erzeugen, damit bereits konfigurierte Jobs beim Upgrade nichts verlieren. ---
    jobs = bind.execute(sa.text("SELECT id, name FROM jobs")).fetchall()
    job_to_filter_set: dict = {}

    for job_id, job_name in jobs:
        filter_set_id = uuid.uuid4()
        job_to_filter_set[job_id] = filter_set_id

        job_filter = bind.execute(
            sa.text("SELECT date_from, date_to FROM job_filters WHERE job_id = :job_id"),
            {"job_id": job_id},
        ).fetchone()
        date_from, date_to = (job_filter.date_from, job_filter.date_to) if job_filter else (None, None)

        bind.execute(
            sa.text(
                "INSERT INTO filter_sets (id, name, date_from, date_to) "
                "VALUES (:id, :name, :date_from, :date_to)"
            ),
            {"id": filter_set_id, "name": f"{job_name} (migriert)", "date_from": date_from, "date_to": date_to},
        )

        for row in bind.execute(
            sa.text("SELECT extension FROM job_filter_extensions WHERE job_id = :job_id"), {"job_id": job_id}
        ):
            bind.execute(
                sa.text(
                    "INSERT INTO filter_set_extensions (id, filter_set_id, extension) "
                    "VALUES (:id, :filter_set_id, :extension)"
                ),
                {"id": uuid.uuid4(), "filter_set_id": filter_set_id, "extension": row.extension},
            )

        for row in bind.execute(
            sa.text(
                "SELECT keyword_normalized, keyword_display FROM job_filter_keywords WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        ):
            bind.execute(
                sa.text(
                    "INSERT INTO filter_set_keywords (id, filter_set_id, keyword_normalized, keyword_display) "
                    "VALUES (:id, :filter_set_id, :keyword_normalized, :keyword_display)"
                ),
                {
                    "id": uuid.uuid4(),
                    "filter_set_id": filter_set_id,
                    "keyword_normalized": row.keyword_normalized,
                    "keyword_display": row.keyword_display,
                },
            )

    op.add_column("jobs", sa.Column("filter_set_id", postgresql.UUID(as_uuid=True), nullable=True))

    for job_id, filter_set_id in job_to_filter_set.items():
        bind.execute(
            sa.text("UPDATE jobs SET filter_set_id = :fs_id WHERE id = :job_id"),
            {"fs_id": filter_set_id, "job_id": job_id},
        )

    op.alter_column("jobs", "filter_set_id", nullable=False)
    op.create_foreign_key("fk_jobs_filter_set", "jobs", "filter_sets", ["filter_set_id"], ["id"])

    op.drop_table("job_filter_keywords")
    op.drop_table("job_filter_extensions")
    op.drop_table("job_filters")


def downgrade() -> None:
    # Bewusst nicht unterstützt (verlustbehaftete Datenmigration zurück auf Pro-Job-Filter) -
    # im Zweifel ein DB-Backup vor dem Upgrade verwenden.
    raise NotImplementedError("Downgrade von 0002_filter_sets wird nicht unterstützt.")
