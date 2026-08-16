import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch):
    """Setzt einen frischen Fernet-Key pro Test und leert den lru_cache von _fernet()."""
    from app import config
    from app.security import crypto

    config.get_settings.cache_clear()
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", Fernet.generate_key().decode())
    crypto._fernet.cache_clear()
    yield
    crypto._fernet.cache_clear()
    config.get_settings.cache_clear()


def test_encrypt_decrypt_round_trip_str():
    from app.security.crypto import decrypt_secret_str, encrypt_secret

    ciphertext = encrypt_secret("super-secret-value")
    assert decrypt_secret_str(ciphertext) == "super-secret-value"


def test_encrypt_decrypt_round_trip_bytes():
    from app.security.crypto import decrypt_secret, encrypt_secret

    raw = b"-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----"
    ciphertext = encrypt_secret(raw)
    assert decrypt_secret(ciphertext) == raw


def test_ciphertext_differs_from_plaintext():
    from app.security.crypto import encrypt_secret

    ciphertext = encrypt_secret("secret")
    assert b"secret" not in ciphertext


def test_decrypt_with_wrong_key_fails():
    from app.security import crypto
    from app.security.crypto import decrypt_secret, encrypt_secret

    ciphertext = encrypt_secret("secret")

    crypto._fernet.cache_clear()
    import os

    os.environ["MASTER_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    crypto.get_settings.cache_clear()

    with pytest.raises(ValueError):
        decrypt_secret(ciphertext)
