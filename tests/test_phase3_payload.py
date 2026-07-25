# [변경사유]: Phase3 payload 빌더 단위 테스트
"""payload / idempotency."""

from __future__ import annotations

import hashlib
from pathlib import Path

from kakao_import.payload import (
    LEGACY_HARD_SKIP_BYTES,
    MAX_UPLOAD_FILE_BYTES,
    _fingerprint_message,
    _idempotency_key,
    ensure_client_instance_id,
    exceeds_ingress_limit,
)


def test_idempotency_key_stable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "kakao_import.payload.INSTANCE_FILE",
        tmp_path / "client_instance_id.txt",
    )
    cid = ensure_client_instance_id()
    assert ensure_client_instance_id() == cid
    k1 = _idempotency_key(cid, "a" * 64, "photo:1")
    k2 = _idempotency_key(cid, "a" * 64, "photo:1")
    assert k1 == k2
    assert len(k1) == 64
    assert k1 == hashlib.sha256(f"{cid}|{'a' * 64}|photo:1".encode()).hexdigest()


def test_message_fingerprint() -> None:
    a = _fingerprint_message("2026-01-01T00:00:00", "hello")
    b = _fingerprint_message("2026-01-01T00:00:00", "hello")
    c = _fingerprint_message("2026-01-01T00:00:00", "hello2")
    assert a == b
    assert a != c


def test_ingress_allows_over_legacy_15mib() -> None:
    """예전 15MiB 하드 스킵 제거 — 15MiB+1 은 허용."""
    assert MAX_UPLOAD_FILE_BYTES == 50 * 1024 * 1024
    assert LEGACY_HARD_SKIP_BYTES == 15 * 1024 * 1024
    assert exceeds_ingress_limit(LEGACY_HARD_SKIP_BYTES + 1) is False
    assert exceeds_ingress_limit(40 * 1024 * 1024) is False
    assert exceeds_ingress_limit(MAX_UPLOAD_FILE_BYTES) is False


def test_ingress_rejects_over_50mib() -> None:
    assert exceeds_ingress_limit(MAX_UPLOAD_FILE_BYTES + 1) is True
    assert exceeds_ingress_limit(60 * 1024 * 1024) is True
