"""event_category: neuer Wert "download_error"

Live-Vorfall: eine E-Mail im überwachten Postfach enthielt einen Anhang mit Schadsoftware
(Trojan.GenericKD...) - die lokale Antiviren-Software hat die temporäre Datei blockiert, BEVOR
`write_once()` sie atomar an ihren Zielort verschieben konnte (kein infiziertes/unvollständiges
File landet dadurch je im Download-Ordner - siehe app/workers/storage.py). Bisher war dieser
Fehler nur in den rohen Container-Logs sichtbar; ab jetzt wird er als SystemEvent protokolliert
und ist auf der /system-Seite sichtbar (siehe app/workers/tasks.py::download_attachment).

Revision ID: 0008_download_error_event
Revises: 0007_one_job_per_mailbox
Create Date: 2026-08-17

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0008_download_error_event"
down_revision: Union[str, None] = "0007_one_job_per_mailbox"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE darf in Postgres nicht in derselben Transaktion verwendet werden,
    # in der es hinzugefügt wurde - Alembic führt Migrationen standardmäßig transaktional aus,
    # daher hier explizit außerhalb der laufenden Transaktion committen.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE event_category ADD VALUE IF NOT EXISTS 'download_error'")


def downgrade() -> None:
    # Postgres unterstützt kein Entfernen einzelner Enum-Werte - Downgrade bewusst nicht
    # unterstützt (harmlos: ein ungenutzter Enum-Wert stört nicht).
    raise NotImplementedError("Downgrade von 0008_download_error_event wird nicht unterstützt.")
