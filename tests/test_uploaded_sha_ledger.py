# [변경사유]: caption-only uploaded_sha_ledger 단위 테스트
"""ledger record / lookup / forget."""

from __future__ import annotations

from pathlib import Path

from kakao_import.db import connect, init_schema
from kakao_import.ledger import (
    forget_uploaded_sha,
    is_uploaded_sha,
    record_uploaded_sha,
    response_ledger_fields,
)


def test_ledger_record_and_forget(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    init_schema(db)
    sha = "a" * 64
    assert is_uploaded_sha(db, sha) is False
    record_uploaded_sha(
        db,
        source_sha256=sha,
        request_idx=12,
        next_val="sns_appended",
        final_sha_prefix="abc",
        ocr_idx=938,
        rejected=False,
        media_fingerprint="m" * 64,
        caption_fingerprint="c" * 64,
    )
    assert is_uploaded_sha(db, sha) is True
    with connect(db) as conn:
        row = conn.execute(
            "SELECT ocr_idx, rejected, media_fingerprint, caption_fingerprint "
            "FROM uploaded_sha_ledger WHERE source_sha256 = ?",
            (sha,),
        ).fetchone()
        assert row["ocr_idx"] == 938
        assert int(row["rejected"]) == 0
        assert row["media_fingerprint"] == "m" * 64
        assert row["caption_fingerprint"] == "c" * 64
    record_uploaded_sha(
        db,
        source_sha256=sha,
        request_idx=13,
        next_val="already_rejected",
        rejected=True,
    )
    assert is_uploaded_sha(db, sha) is True
    forget_uploaded_sha(db, sha)
    assert is_uploaded_sha(db, sha) is False


def test_response_ledger_fields() -> None:
    f = response_ledger_fields(
        {
            "success": True,
            "request_idx": 7,
            "next": "sns_appended",
            "final_sha256_prefix": "deadbeef",
            "source_sha256": "B" * 64,
            "ocr_idx": 938,
        }
    )
    assert f["request_idx"] == 7
    assert f["next_val"] == "sns_appended"
    assert f["source_sha256"] == "b" * 64
    assert f["ocr_idx"] == 938
