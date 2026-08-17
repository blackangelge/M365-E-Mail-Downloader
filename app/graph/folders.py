"""Rekursive Ermittlung aller Unterordner des Posteingangs über Microsoft Graph.

Nutzer legen Rechnungen/Belege häufig in (per Regel oder manuell sortierten) Unterordnern des
Posteingangs ab. Ohne diese Enumeration würde nur der Posteingang selbst per Delta-Sync gescannt
und alles darunter unbemerkt übersehen (siehe app/workers/tasks.py::sync_mailbox_folders)."""
from __future__ import annotations

from dataclasses import dataclass

from msgraph import GraphServiceClient

from app.graph.errors import classify_and_wrap


@dataclass(slots=True)
class MailFolderInfo:
    graph_folder_id: str
    display_path: str  # z.B. "Posteingang/Rechnungen/2026" - nur zur Anzeige in der UI


async def list_inbox_and_subfolders(client: GraphServiceClient, mailbox_address: str) -> list[MailFolderInfo]:
    """Posteingang selbst + alle Unterordner rekursiv (beliebige Verschachtelungstiefe)."""
    try:
        mailbox = client.users.by_user_id(mailbox_address)
        inbox = await mailbox.mail_folders.by_mail_folder_id("inbox").get()
        inbox_name = inbox.display_name or "Posteingang"

        # "inbox" (Graph-Alias-Name) bleibt bewusst die stabile ID für den Posteingang selbst -
        # Graph akzeptiert diesen Alias direkt in URLs; ein Wechsel auf die echte GUID würde nur
        # unnötige Komplexität bei bestehenden delta_links einführen, ohne Vorteil.
        results = [MailFolderInfo(graph_folder_id="inbox", display_path=inbox_name)]
        await _collect_children(mailbox, inbox.id, inbox_name, results)
        return results
    except Exception as exc:  # noqa: BLE001
        raise classify_and_wrap(exc) from exc


async def _collect_children(mailbox, folder_id: str, path_prefix: str, results: list[MailFolderInfo]) -> None:
    children_builder = mailbox.mail_folders.by_mail_folder_id(folder_id).child_folders
    response = await children_builder.get()

    while response is not None:
        for child in response.value or []:
            child_path = f"{path_prefix}/{child.display_name}"
            results.append(MailFolderInfo(graph_folder_id=child.id, display_path=child_path))
            if (child.child_folder_count or 0) > 0:
                await _collect_children(mailbox, child.id, child_path, results)

        next_link = getattr(response, "odata_next_link", None)
        response = await children_builder.with_url(next_link).get() if next_link else None
