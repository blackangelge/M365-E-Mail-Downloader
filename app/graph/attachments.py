"""Anhänge einer E-Mail auflisten und deren Inhalt herunterladen."""
from __future__ import annotations

import base64
from dataclasses import dataclass

from msgraph import GraphServiceClient

from app.graph.errors import classify_and_wrap


@dataclass(slots=True)
class GraphAttachment:
    attachment_id: str
    name: str
    content_type: str | None
    size_bytes: int
    content: bytes


async def list_and_download_attachments(
    client: GraphServiceClient, mailbox_address: str, message_id: str
) -> list[GraphAttachment]:
    """Lädt alle Datei-Anhänge (fileAttachment) einer Nachricht inkl. Inhalt.

    Nicht-Datei-Anhänge (z.B. eingebettete Kalendereinladungen/itemAttachment) werden übersprungen,
    da nur Dateianhänge für die Endungs-/Namensfilter relevant sind.
    """
    try:
        message_attachments = client.users.by_user_id(mailbox_address).messages.by_message_id(message_id).attachments
        response = await message_attachments.get()

        results: list[GraphAttachment] = []
        for attachment in response.value or []:
            odata_type = getattr(attachment, "odata_type", "") or ""
            if "fileAttachment" not in odata_type:
                continue  # itemAttachment/referenceAttachment ignorieren

            content_bytes = getattr(attachment, "content_bytes", None)
            if content_bytes is None:
                continue
            # msgraph-sdk dekodiert content_bytes bereits zu `bytes`; base64-Fallback für
            # Rohdaten, falls eine ältere SDK-Version einen str zurückgibt.
            content = content_bytes if isinstance(content_bytes, bytes) else base64.b64decode(content_bytes)

            results.append(
                GraphAttachment(
                    attachment_id=attachment.id,
                    name=attachment.name or "unbenannt",
                    content_type=getattr(attachment, "content_type", None),
                    size_bytes=len(content),
                    content=content,
                )
            )
        return results
    except Exception as exc:  # noqa: BLE001
        raise classify_and_wrap(exc) from exc


async def download_single_attachment(
    client: GraphServiceClient, mailbox_address: str, message_id: str, attachment_id: str
) -> GraphAttachment:
    """Lädt genau einen Anhang erneut per ID.

    Wird von der `download_attachment`-Task verwendet statt den bereits in `evaluate_job_for_message`
    geladenen Anhangsinhalt als Task-Argument mitzugeben - Procrastinate-Task-Argumente landen als
    JSON in `procrastinate_jobs`, und dort binäre PDF-Inhalte abzulegen wäre unnötig aufwändig und
    würde die Queue-Tabelle aufblähen. Der doppelte Graph-Aufruf ist der bewusst gewählte Trade-off.
    """
    try:
        attachment = (
            await client.users.by_user_id(mailbox_address)
            .messages.by_message_id(message_id)
            .attachments.by_attachment_id(attachment_id)
            .get()
        )
        content_bytes = getattr(attachment, "content_bytes", None)
        if content_bytes is None:
            raise ValueError(f"Anhang {attachment_id} hat keinen Datei-Inhalt (kein fileAttachment?)")
        content = content_bytes if isinstance(content_bytes, bytes) else base64.b64decode(content_bytes)
        return GraphAttachment(
            attachment_id=attachment.id,
            name=attachment.name or "unbenannt",
            content_type=getattr(attachment, "content_type", None),
            size_bytes=len(content),
            content=content,
        )
    except Exception as exc:  # noqa: BLE001
        raise classify_and_wrap(exc) from exc
