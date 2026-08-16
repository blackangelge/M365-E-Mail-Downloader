"""Verschlüsselung von Tenant-Zugangsdaten (Client-Secret, Zertifikat, Zertifikats-Passwort) at rest.

Nutzt Fernet (symmetrisch, aus dem offiziellen `cryptography`-Paket) mit einem Schlüssel aus
der Umgebungsvariable MASTER_ENCRYPTION_KEY. Secrets werden ausschließlich verschlüsselt in der
DB gespeichert und nur just-in-time beim Aufbau eines Graph-Clients entschlüsselt - siehe
app/graph/client_factory.py. Sie werden nie geloggt und nie in API-/UI-Antworten zurückgegeben.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class EncryptionNotConfiguredError(RuntimeError):
    """MASTER_ENCRYPTION_KEY ist nicht gesetzt - Zugangsdaten können nicht verarbeitet werden."""


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().master_encryption_key
    if not key:
        raise EncryptionNotConfiguredError(
            "MASTER_ENCRYPTION_KEY ist nicht gesetzt. Erzeugen mit: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext: str | bytes) -> bytes:
    if plaintext is None:
        raise ValueError("plaintext darf nicht None sein")
    data = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
    return _fernet().encrypt(data)


def decrypt_secret(ciphertext: bytes) -> bytes:
    try:
        return _fernet().decrypt(ciphertext)
    except InvalidToken as exc:
        raise ValueError("Zugangsdaten konnten nicht entschlüsselt werden (falscher Schlüssel?)") from exc


def decrypt_secret_str(ciphertext: bytes) -> str:
    return decrypt_secret(ciphertext).decode("utf-8")
