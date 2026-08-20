# [변경사유]: caption-only uploaded_sha_ledger — 이미 올린 source SHA 기록·조회
# [변경사유]: media/caption 지문·ocr_idx·거부 캐시 (ingest-dedup-reject-plan)
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


def _ledger_cols(conn: sqlite3.Connection) -> set[str]:
    return {str(r[1]) for r in conn.execute("PRAGMA table_info(uploaded_sha_ledger)")}


def normalize_sha(sha: str | None) -> str:
    return str(sha or "").strip().lower()


def is_uploaded_sha(db_path: Path, source_sha256: str) -> bool:
    """장부에 있으면 caption-only 후보 (거부 캐시여도 SHA 조회는 됨)."""
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


def has_uploaded_fingerprint(
    db_path: Path,
    *,
    media_fingerprint: str,
    caption_fingerprint: str | None = None,
) -> bool:
    """동일 media/caption 지문이 이미 성공 업로드 되었는지."""
    media_fp = normalize_sha(media_fingerprint)
    caption_fp = normalize_sha(caption_fingerprint) if caption_fingerprint else ""
    if len(media_fp) != 64:
        return False
    with connect(db_path) as conn:
        if not _has_ledger(conn):
            return False
        row = conn.execute(
            """
            SELECT 1
            FROM uploaded_sha_ledger
            WHERE lower(IFNULL(media_fingerprint, '')) = ?
              AND lower(IFNULL(caption_fingerprint, '')) = ?
              AND IFNULL(rejected, 0) = 0
            LIMIT 1
            """,
            (media_fp, caption_fp),
        ).fetchone()
        return bool(row)


def record_uploaded_sha(
    db_path: Path,
    *,
    source_sha256: str,
    request_idx: int | None = None,
    next_val: str | None = None,
    final_sha_prefix: str | None = None,
    ocr_idx: int | None = None,
    caption_fingerprint: str | None = None,
    media_fingerprint: str | None = None,
    rejected: bool = False,
) -> None:
    """성공 업로드 후 source SHA 기록 (upsert)."""
    sha = normalize_sha(source_sha256)
    if len(sha) != 64:
        return
    with connect(db_path) as conn:
        if not _has_ledger(conn):
            log.warning("uploaded_sha_ledger missing — run init_schema")
            return
        cols = _ledger_cols(conn)
        media_fp = (media_fingerprint or sha).strip().lower() or sha
        cap_fp = (caption_fingerprint or "").strip().lower() or None
        if "ocr_idx" in cols:
            conn.execute(
                """
                INSERT INTO uploaded_sha_ledger (
                  source_sha256, request_idx, next, final_sha_prefix, uploaded_at,
                  media_fingerprint, caption_fingerprint, ocr_idx, result_type,
                  rejected, rejected_at
                ) VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?, ?, ?,
                  CASE WHEN ? THEN datetime('now') ELSE NULL END)
                ON CONFLICT(source_sha256) DO UPDATE SET
                  request_idx = excluded.request_idx,
                  next = excluded.next,
                  final_sha_prefix = excluded.final_sha_prefix,
                  uploaded_at = datetime('now'),
                  media_fingerprint = excluded.media_fingerprint,
                  caption_fingerprint = excluded.caption_fingerprint,
                  ocr_idx = excluded.ocr_idx,
                  result_type = excluded.result_type,
                  rejected = excluded.rejected,
                  rejected_at = excluded.rejected_at
                """,
                (
                    sha,
                    request_idx,
                    next_val,
                    final_sha_prefix,
                    media_fp,
                    cap_fp,
                    ocr_idx,
                    next_val,
                    1 if rejected else 0,
                    1 if rejected else 0,
                ),
            )
        else:
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
        "ledger record sha_prefix=%s request_idx=%s next=%s ocr_idx=%s rejected=%s",
        sha[:12],
        request_idx,
        next_val,
        ocr_idx,
        rejected,
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
    ocr_raw = data.get("ocr_idx")
    try:
        ocr_idx = int(ocr_raw) if ocr_raw is not None else None
    except (TypeError, ValueError):
        ocr_idx = None
    return {
        "request_idx": request_idx,
        "next_val": str(data.get("next") or "") or None,
        "final_sha_prefix": str(data.get("final_sha256_prefix") or "") or None,
        "source_sha256": normalize_sha(str(data.get("source_sha256") or "")),
        "ocr_idx": ocr_idx,
    }
