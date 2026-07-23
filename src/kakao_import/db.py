# [변경사유]: SQLite 연결·스키마 적용
"""로컬 DB."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from kakao_import.config import PROJECT_ROOT
from kakao_import.logging_util import get_logger

log = get_logger(__name__)

SCHEMA_SQL = PROJECT_ROOT / "sql" / "001_init_schema.sql"


def connect(db_path: Path) -> sqlite3.Connection:
    """DB 연결 (row_factory=Row)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # [변경사유]: 쿼리 로그용 — 경로만 (시크릿 없음)
    log.info("sqlite connect path=%s", db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(db_path: Path) -> None:
    """001_init_schema.sql 적용."""
    if not SCHEMA_SQL.is_file():
        raise FileNotFoundError(f"schema missing: {SCHEMA_SQL}")
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    log.info("init_schema sql=%s db=%s", SCHEMA_SQL, db_path)
    with connect(db_path) as conn:
        conn.executescript(sql)
        conn.commit()
    log.info("init_schema done")


def status_counts(db_path: Path) -> dict[str, int]:
    """주요 테이블 건수."""
    if not db_path.is_file():
        return {"db_exists": 0}
    with connect(db_path) as conn:
        out: dict[str, int] = {"db_exists": 1}
        for table in (
            "source_room",
            "local_message",
            "local_media",
            "match_result",
            "review_queue",
        ):
            # [변경사유]: 고정 테이블명만 — SQL injection 없음
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
            out[table] = int(row["c"])
            log.info("status count table=%s count=%s", table, out[table])
        return out
