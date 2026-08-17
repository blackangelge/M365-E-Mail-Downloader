"""Delta-Sync von Nachrichten EINES Ordners eines Postfachs über die Microsoft Graph API.

Nutzt den Message-Delta-Endpoint (`/users/{id}/mailFolders/{folderId}/messages/delta`), damit
nach dem ersten vollen Durchlauf nur noch neue/geänderte Nachrichten abgefragt werden - nicht die
gesamte Ordner-Historie bei jedem Poll. `mail_folder` kann sowohl der Alias "inbox" als auch eine
echte Graph-Ordner-ID sein (siehe app/graph/folders.py für die Ermittlung aller Unterordner).
`$filter`/`$select` dürfen laut Graph-Doku nur beim allerersten Request (ohne `$deltatoken`)
gesetzt werden; Folge-Requests verwenden ausschließlich den zurückgegebenen
`@odata.nextLink`/`@odata.deltaLink`.

Jede Ergebnisseite wird sofort an den Aufrufer übergeben, damit `delta_link` erst NACH dem
durably committeten Upsert der Seite in `processed_emails` aktualisiert wird (siehe
app/workers/tasks.py::_sync_single_folder) - so wird bei einem Absturz mitten im Sync ab der
letzten committeten Seite fortgesetzt statt neu gescannt.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph import GraphServiceClient
from msgraph.generated.users.item.mail_folders.item.messages.delta.delta_request_builder import (
    DeltaRequestBuilder,
)

from app.graph.errors import classify_and_wrap

_SELECT_FIELDS = [
    "id",
    "internetMessageId",
    "subject",
    "receivedDateTime",
    "hasAttachments",
    "from",
]


@dataclass(slots=True)
class ScannedMessage:
    message_id: str
    internet_message_id: str | None
    subject: str | None
    from_address: str | None
    received_at: datetime | None
    has_attachments: bool


@dataclass(slots=True)
class DeltaPage:
    messages: list[ScannedMessage]
    next_delta_link: str | None  # gesetzt, sobald dies die letzte Seite ist


def _extract_from_address(message) -> str | None:
    sender = getattr(message, "from_", None) or getattr(message, "from_escaped", None)
    if sender is None:
        return None
    email_address = getattr(sender, "email_address", None)
    return getattr(email_address, "address", None) if email_address else None


def _to_scanned_message(message) -> ScannedMessage:
    return ScannedMessage(
        message_id=message.id,
        internet_message_id=getattr(message, "internet_message_id", None),
        subject=getattr(message, "subject", None),
        from_address=_extract_from_address(message),
        received_at=getattr(message, "received_date_time", None),
        has_attachments=bool(getattr(message, "has_attachments", False)),
    )


async def scan_mailbox_delta(
    client: GraphServiceClient,
    mailbox_address: str,
    delta_link: str | None,
    mail_folder: str = "inbox",
) -> AsyncIterator[DeltaPage]:
    """Liefert Delta-Seiten für ein Postfach. Bei `delta_link=None` läuft ein initialer Voll-Sync,
    sonst wird ab dem gespeicherten Delta-Token fortgesetzt.

    Der Delta-Endpoint unterstützt laut Graph-API ausschließlich `$filter=receivedDateTime ge ...`
    - ein `$filter=hasAttachments eq true` wird mit `400 ErrorInvalidUrlQuery` abgelehnt
    ("only a value of 'receivedDateTime ge value' is supported"). Die Anhang-Filterung passiert
    deshalb NICHT hier, sondern client-seitig über `processed_emails.has_attachments` beim
    Auswerten der Jobs (siehe app/workers/tasks.py::match_job_against_mailbox).
    """
    try:
        messages_builder = (
            client.users.by_user_id(mailbox_address).mail_folders.by_mail_folder_id(mail_folder).messages
        )

        if delta_link:
            response = await messages_builder.delta.with_url(delta_link).get()
        else:
            query_params = DeltaRequestBuilder.DeltaRequestBuilderGetQueryParameters(select=_SELECT_FIELDS)
            request_config = RequestConfiguration(query_parameters=query_params)
            response = await messages_builder.delta.get(request_configuration=request_config)

        while response is not None:
            page_messages = [_to_scanned_message(m) for m in (response.value or [])]
            next_link = getattr(response, "odata_next_link", None)
            new_delta_link = getattr(response, "odata_delta_link", None)

            yield DeltaPage(messages=page_messages, next_delta_link=new_delta_link)

            if next_link:
                response = await messages_builder.delta.with_url(next_link).get()
            else:
                response = None
    except Exception as exc:  # noqa: BLE001 - in klassifizierte Task-Exceptions umwandeln
        raise classify_and_wrap(exc) from exc
