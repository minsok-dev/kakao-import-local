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
    caption_fingerprint,
    caption_fingerprint_without_sender_time,
    ensure_client_instance_id,
    exceeds_ingress_limit,
    media_fingerprint,
)


def test_idempotency_key_stable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "kakao_import.payload.INSTANCE_FILE",
        tmp_path / "client_instance_id.txt",
    )
    cid = ensure_client_instance_id()
    assert ensure_client_instance_id() == cid
    media = "a" * 64
    cap = caption_fingerprint([{"text": "hello"}])
    k1 = _idempotency_key(media, cap)
    k2 = _idempotency_key(media, cap)
    assert k1 == k2
    assert len(k1) == 64
    assert k1 == hashlib.sha256(f"kakao:upload:v1:{media}:{cap}".encode()).hexdigest()
    # [변경사유]: photo_id 가 키에 들어가면 안 됨
    assert k1 != _idempotency_key(media, caption_fingerprint([{"text": "hello2"}]))


def test_sender_time_does_not_change_legacy_caption_fingerprint() -> None:
    # [변경사유]: 대화명 옆 시각만 붙은 문장은 예전 지문과 같아야 다시 보내지 않는다
    legacy = "[대화명: 시남 필댄스]\n이번주 정모는 쉬어갑니당"
    stamped = "[대화명: 시남 필댄스] [2026-09-26 19:10]\n이번주 정모는 쉬어갑니당"
    assert caption_fingerprint([{"text": legacy}]) == caption_fingerprint_without_sender_time(
        [{"text": stamped}]
    )
    newer = "[대화명: 시남 필댄스] [2026-09-26 19:10]\n이번주 정모는 쉬어갑니당\n추가 안내"
    assert caption_fingerprint_without_sender_time(
        [{"text": newer}]
    ) != caption_fingerprint([{"text": legacy}])


def test_media_fingerprint_bundle_order_independent() -> None:
    a, b = "a" * 64, "b" * 64
    assert media_fingerprint(a, [b, a]) == media_fingerprint(b, [a, b])


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
