"""job_mailboxes: ein Postfach darf nur zu einem Job gehören

Mehrere Jobs auf demselben Postfach mit überlappenden Filtern führen wegen des postfachweiten
Content-Dedups (siehe app/workers/dedup.py) dazu, dass nur der zuerst auswertende Job tatsächlich
eine Datei in seinen eigenen Zielordner schreibt - der zweite Job bekommt nie eine eigene Kopie.
Das war die Ursache für den vom Nutzer live beobachteten Fall ("Test 2 hat keine Datei
heruntergeladen"). App-seitig wird das jetzt bereits beim Speichern verhindert (siehe
app/web/routers/jobs.py::_mailbox_conflict_error); diese DB-Constraint ist das Sicherheitsnetz.

ACHTUNG - setzt voraus, dass KEIN Postfach aktuell mehreren Jobs zugeordnet ist. Falls doch
(siehe Kommentar unten), bricht diese Migration mit einer klaren Postgres-Fehlermeldung ab -
der Konflikt muss vorher manuell aufgelöst werden (z.B. einen der betroffenen Jobs bearbeiten
und das doppelt zugeordnete Postfach entfernen).

Revision ID: 0007_one_job_per_mailbox
Revises: 0006_sighting_job_id
Create Date: 2026-08-17

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007_one_job_per_mailbox"
down_revision: Union[str, None] = "0006_sighting_job_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint("uq_job_mailbox_mailbox", "job_mailboxes", ["mailbox_id"])


def downgrade() -> None:
    op.drop_constraint("uq_job_mailbox_mailbox", "job_mailboxes", type_="unique")
