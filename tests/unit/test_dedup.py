import uuid

from app.workers.dedup import _advisory_lock_key, compute_sha256


def test_compute_sha256_is_deterministic():
    content = b"%PDF-1.4 fake pdf content"
    assert compute_sha256(content) == compute_sha256(content)
    assert len(compute_sha256(content)) == 64


def test_compute_sha256_differs_for_different_content():
    assert compute_sha256(b"content-a") != compute_sha256(b"content-b")


def test_advisory_lock_key_deterministic_and_bounded():
    mailbox_id = uuid.uuid4()
    sha = compute_sha256(b"same content")

    key_a = _advisory_lock_key(mailbox_id, sha)
    key_b = _advisory_lock_key(mailbox_id, sha)
    assert key_a == key_b
    assert -(2**63) <= key_a < 2**63  # muss in Postgres bigint passen


def test_advisory_lock_key_differs_per_mailbox():
    sha = compute_sha256(b"same content")
    key_a = _advisory_lock_key(uuid.uuid4(), sha)
    key_b = _advisory_lock_key(uuid.uuid4(), sha)
    assert key_a != key_b
