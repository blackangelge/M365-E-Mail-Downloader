"""attachment_sightings: job_id in den Unique-Key aufnehmen

Der bisherige Unique-Key (attachment_file_id, processed_email_id) fehlte job_id - dadurch konnte
bei zwei Jobs auf demselben Postfach mit überlappenden Filtern nur der ZUERST auswertende Job
jemals einen Sichtungs-Eintrag für einen gegebenen Anhang/eine gegebene E-Mail bekommen
(ON CONFLICT DO NOTHING griff fälschlich bereits beim zweiten Job). Live nachgewiesen: Job
"Test 2" hat denselben Anhang wie Job "Test 1" korrekt erkannt und "heruntergeladen"
(Procrastinate-Task erfolgreich), aber keinen eigenen Sichtungs-Eintrag bekommen.

Rein additiv/sicher: die neue Spaltenkombination ist eine ECHTE Erweiterung des bisherigen Keys,
es kann also keine bestehende Zeile verletzen.

Revision ID: 0006_sighting_job_id
Revises: 0005_mailbox_folders
Create Date: 2026-08-17

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006_sighting_job_id"
down_revision: Union[str, None] = "0005_mailbox_folders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_sighting_file_email", "attachment_sightings", type_="unique")
    op.create_unique_constraint(
        "uq_sighting_job_file_email",
        "attachment_sightings",
        ["job_id", "attachment_file_id", "processed_email_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_sighting_job_file_email", "attachment_sightings", type_="unique")
    op.create_unique_constraint(
        "uq_sighting_file_email", "attachment_sightings", ["attachment_file_id", "processed_email_id"]
    )
