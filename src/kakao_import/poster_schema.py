# [변경사유]: 007 포스터 분류 스키마 — exact_sha_member 와 분리
"""포스터 분류 SQLite."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from kakao_import.config import PROJECT_ROOT
from kakao_import.logging_util import get_logger

log = get_logger(__name__)
SCHEMA_SQL_POSTER = PROJECT_ROOT / "sql" / "007_poster_classify.sql"
# [변경사유]: I6/B2 — dataset sync 이력
SCHEMA_SQL_POSTER_SYNC = PROJECT_ROOT / "sql" / "010_poster_sync_log.sql"


def poster_tables_exist(conn: sqlite3.Connection) -> bool:
    """poster_classify 테이블 여부."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='poster_classify'"
    ).fetchone()
    return row is not None


def ensure_poster_schema(conn: sqlite3.Connection) -> None:
    """007 적용 (이미 있으면 IF NOT EXISTS) + 010 sync_log."""
    if not SCHEMA_SQL_POSTER.is_file():
        log.warning("poster schema missing path=%s", SCHEMA_SQL_POSTER)
        return
    sql = SCHEMA_SQL_POSTER.read_text(encoding="utf-8")
    conn.executescript(sql)
    log.info("init_schema sql=%s", SCHEMA_SQL_POSTER.name)
    # [변경사유]: B2 — sync 이력 테이블 (IF NOT EXISTS)
    if SCHEMA_SQL_POSTER_SYNC.is_file():
        conn.executescript(SCHEMA_SQL_POSTER_SYNC.read_text(encoding="utf-8"))
        log.info("init_schema sql=%s", SCHEMA_SQL_POSTER_SYNC.name)


def excluded_poster_shas(conn: sqlite3.Connection) -> set[str]:
    """업로드에서 뺄 sha256 (human poster 가 있으면 그 sha 는 빼지 않음)."""
    if not poster_tables_exist(conn):
        return set()
    # [변경사유]: 사람 포스터 확정이 모델 제외보다 우선
    rows = conn.execute(
        """
        SELECT lower(sha256) AS sha
        FROM poster_classify
        WHERE IFNULL(excluded_from_upload, 0) = 1
          AND lower(sha256) NOT IN (
            SELECT lower(sha256) FROM poster_classify
            WHERE source = 'human' AND status = 'poster'
          )
        """
    ).fetchall()
    return {str(r["sha"]) for r in rows if r["sha"]}


def excluded_poster_photo_ids(conn: sqlite3.Connection) -> set[int]:
    """similar-detect 에서 빼 둘 photo_id."""
    if not poster_tables_exist(conn):
        return set()
    shas = excluded_poster_shas(conn)
    if not shas:
        return set()
    ids: set[int] = set()
    for sha in shas:
        for r in conn.execute(
            "SELECT id FROM photo_file WHERE lower(sha256) = ?", (sha,)
        ).fetchall():
            ids.add(int(r["id"]))
    log.info("poster similar-skip photos=%s shas=%s", len(ids), len(shas))
    return ids


def has_any_classify_for_sha(conn: sqlite3.Connection, sha256: str) -> bool:
    """해당 sha에 poster_classify 행이 하나라도 있으면 True."""
    if not poster_tables_exist(conn):
        return False
    row = conn.execute(
        "SELECT 1 FROM poster_classify WHERE lower(sha256) = ? LIMIT 1",
        (sha256.lower(),),
    ).fetchone()
    return row is not None


def get_classify_row(
    conn: sqlite3.Connection, room_id: str, sha256: str
) -> dict[str, Any] | None:
    """단건 조회."""
    if not poster_tables_exist(conn):
        return None
    row = conn.execute(
        """
        SELECT * FROM poster_classify
        WHERE room_id = ? AND lower(sha256) = ?
        """,
        (room_id, sha256.lower()),
    ).fetchone()
    return dict(row) if row else None


def list_classify_rows(
    conn: sqlite3.Connection,
    *,
    room_id: str | None = None,
    status: str | None = None,
    source: str | None = None,
    excluded_only: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """포스터 분류 목록 조회 (브라우저 리뷰 UI용)."""
    if not poster_tables_exist(conn):
        return []
    where: list[str] = []
    params: list[Any] = []
    if room_id:
        where.append("pc.room_id = ?")
        params.append(room_id)
    if status:
        where.append("pc.status = ?")
        params.append(status)
    if source:
        where.append("pc.source = ?")
        params.append(source)
    if excluded_only:
        where.append("IFNULL(pc.excluded_from_upload, 0) = 1")
    sql = """
        SELECT
          pc.id,
          pc.room_id,
          pc.photo_id,
          pc.sha256,
          pc.rel_path,
          pc.file_name,
          pc.model_version,
          pc.poster_score,
          pc.status,
          pc.source,
          pc.excluded_from_upload,
          pc.classified_at
        FROM poster_classify pc
    """
    if where:
        sql += "\nWHERE " + " AND ".join(where)
    sql += "\nORDER BY pc.classified_at DESC, pc.id DESC"
    if limit is not None and limit > 0:
        sql += "\nLIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]
