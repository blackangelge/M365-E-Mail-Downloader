"""Baut pro Tenant einen `GraphServiceClient` (msgraph-sdk, async) auf Basis der in der DB
gespeicherten, verschlüsselten Zugangsdaten.

Unterstützt sowohl Client-Secret- als auch Zertifikat-basierte App-Registrierungen
(azure-identity `ClientSecretCredential` / `CertificateCredential`) - konfigurierbar pro Tenant.
Clients werden pro (tenant_id, credentials_version) gecached; eine Änderung der Zugangsdaten
im UI erhöht `credentials_version` und invalidiert damit automatisch den Cache-Eintrag.
"""
from __future__ import annotations

import asyncio

from azure.identity.aio import CertificateCredential, ClientSecretCredential
from msgraph import GraphServiceClient

from app.models import AuthType, Tenant
from app.security.crypto import decrypt_secret, decrypt_secret_str

_GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]

_client_cache: dict[tuple[str, int], GraphServiceClient] = {}
_cache_lock = asyncio.Lock()


def _build_credential(tenant: Tenant):
    if tenant.auth_type == AuthType.CLIENT_SECRET:
        if not tenant.client_secret_encrypted:
            raise ValueError(f"Tenant {tenant.name}: kein Client-Secret hinterlegt")
        secret = decrypt_secret_str(tenant.client_secret_encrypted)
        return ClientSecretCredential(
            tenant_id=tenant.azure_tenant_id,
            client_id=tenant.azure_client_id,
            client_secret=secret,
        )

    if tenant.auth_type == AuthType.CERTIFICATE:
        if not tenant.certificate_pem_encrypted:
            raise ValueError(f"Tenant {tenant.name}: kein Zertifikat hinterlegt")
        cert_data = decrypt_secret(tenant.certificate_pem_encrypted)
        password = (
            decrypt_secret_str(tenant.certificate_password_encrypted)
            if tenant.certificate_password_encrypted
            else None
        )
        return CertificateCredential(
            tenant_id=tenant.azure_tenant_id,
            client_id=tenant.azure_client_id,
            certificate_data=cert_data,
            password=password.encode("utf-8") if password else None,
        )

    raise ValueError(f"Unbekannter auth_type: {tenant.auth_type}")


async def get_graph_client(tenant: Tenant) -> GraphServiceClient:
    """Liefert einen (gecachten) GraphServiceClient für den gegebenen Tenant."""
    cache_key = (str(tenant.id), tenant.credentials_version)

    cached = _client_cache.get(cache_key)
    if cached is not None:
        return cached

    async with _cache_lock:
        cached = _client_cache.get(cache_key)
        if cached is not None:
            return cached

        credential = _build_credential(tenant)
        client = GraphServiceClient(credentials=credential, scopes=_GRAPH_SCOPES)

        # Alte Cache-Einträge desselben Tenants (frühere credentials_version) entfernen.
        stale_keys = [k for k in _client_cache if k[0] == cache_key[0] and k != cache_key]
        for key in stale_keys:
            _client_cache.pop(key, None)

        _client_cache[cache_key] = client
        return client


async def test_mailbox_access(tenant: Tenant, mailbox_address: str) -> tuple[bool, str | None]:
    """Verbindungstest für die 'Postfach testen'-Aktion im UI: versucht, das Postfach-Profil zu lesen."""
    from app.graph.errors import classify_and_wrap

    try:
        client = await get_graph_client(tenant)
        await client.users.by_user_id(mailbox_address).get()
        return True, None
    except Exception as exc:  # noqa: BLE001 - Verbindungstest soll nie hart fehlschlagen
        wrapped = classify_and_wrap(exc)
        return False, str(wrapped)
