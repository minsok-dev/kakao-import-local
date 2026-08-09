# [변경사유]: caption-only ledger — 이미 올린 source SHA 기록·조회
"""로컬 uploaded_sha_ledger (서버 exact SSOT, 장부는 대역폭 힌트만)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from kakao_import.db import connect
from kakao_import.logging_util import get_logger

log = get_logger(__name__)


def _has_ledger(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = 'uploaded_sha_ledger'"
    ).fetchone()
    return bool(row)


def normalize_sha(sha: str | None) -> str:
    return str(sha or "").strip().lower()


def is_uploaded_sha(db_path: Path, source_sha256: str) -> bool:
    """장부에 있으면 caption-only 후보."""
    sha = normalize_sha(source_sha256)
    if len(sha) != 64:
        return False
    with connect(db_path) as conn:
        if not _has_ledger(conn):
            return False
        row = conn.execute(
            "SELECT 1 FROM uploaded_sha_ledger WHERE source_sha256 = ? LIMIT 1",
            (sha,),
        ).fetchone()
        return bool(row)


def record_uploaded_sha(
    db_path: Path,
    *,
    source_sha256: str,
    request_idx: int | None = None,
    next_val: str | None = None,
    final_sha_prefix: str | None = None,
) -> None:
    """성공 업로드 후 source SHA 기록 (upsert)."""
    sha = normalize_sha(source_sha256)
    if len(sha) != 64:
        return
    with connect(db_path) as conn:
        if not _has_ledger(conn):
            log.warning("uploaded_sha_ledger missing — run init_schema")
            return
        conn.execute(
            """
            INSERT INTO uploaded_sha_ledger (
              source_sha256, request_idx, next, final_sha_prefix, uploaded_at
            ) VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(source_sha256) DO UPDATE SET
              request_idx = excluded.request_idx,
              next = excluded.next,
              final_sha_prefix = excluded.final_sha_prefix,
              uploaded_at = datetime('now')
            """,
            (sha, request_idx, next_val, final_sha_prefix),
        )
        conn.commit()
    log.info(
        "ledger record sha_prefix=%s request_idx=%s next=%s",
        sha[:12],
        request_idx,
        next_val,
    )


def forget_uploaded_sha(db_path: Path, source_sha256: str) -> None:
    """caption-only 실패 시 장부 삭제 → 다음엔 파일 재전송."""
    sha = normalize_sha(source_sha256)
    if len(sha) != 64:
        return
    with connect(db_path) as conn:
        if not _has_ledger(conn):
            return
        conn.execute(
            "DELETE FROM uploaded_sha_ledger WHERE source_sha256 = ?",
            (sha,),
        )
        conn.commit()
    log.info("ledger forget sha_prefix=%s", sha[:12])


def response_ledger_fields(response: dict[str, Any] | None) -> dict[str, Any]:
    """서버 Import 응답에서 장부 필드 추출."""
    if not isinstance(response, dict):
        return {}
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    if not isinstance(data, dict):
        return {}
    req = data.get("request_idx")
    try:
        request_idx = int(req) if req is not None else None
    except (TypeError, ValueError):
        request_idx = None
    return {
        "request_idx": request_idx,
        "next_val": str(data.get("next") or "") or None,
        "final_sha_prefix": str(data.get("final_sha256_prefix") or "") or None,
        "source_sha256": normalize_sha(str(data.get("source_sha256") or "")),
    }
