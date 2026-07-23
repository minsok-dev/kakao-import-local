# [변경사유]: Phase1 — SQLite 연결·스키마·멱등 upsert
"""로컬 DB."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from kakao_import.config import PROJECT_ROOT
from kakao_import.logging_util import get_logger

log = get_logger(__name__)
SCHEMA_SQL = PROJECT_ROOT / "sql" / "001_init_schema.sql"
SCHEMA_SQL_P2 = PROJECT_ROOT / "sql" / "002_phase2_text_merge.sql"
SCHEMA_SQL_P2B = PROJECT_ROOT / "sql" / "003_phase2_manual_undo.sql"


def connect(db_path: Path) -> sqlite3.Connection:
    """연결."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("sqlite connect db=%s", db_path.name)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _apply_phase2b_columns(conn: sqlite3.Connection) -> None:
    """003: decision_source / manual_note (이미 있으면 skip)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(text_merge)").fetchall()}
    if not cols:
        return
    if "decision_source" not in cols:
        conn.execute(
            "ALTER TABLE text_merge ADD COLUMN decision_source TEXT NOT NULL DEFAULT 'auto'"
        )
        log.info("schema add column decision_source")
    if "manual_note" not in cols:
        conn.execute("ALTER TABLE text_merge ADD COLUMN manual_note TEXT")
        log.info("schema add column manual_note")
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '4')"
    )


def init_schema(db_path: Path, *, reset: bool = False) -> None:
    """스키마 적용 (001 + Phase2 002/003). reset=True면 파일 삭제 후 재생성."""
    if reset and db_path.exists():
        db_path.unlink()
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    log.info("init_schema sql=%s", SCHEMA_SQL.name)
    with connect(db_path) as conn:
        conn.executescript(sql)
        if SCHEMA_SQL_P2.is_file():
            log.info("init_schema sql=%s", SCHEMA_SQL_P2.name)
            conn.executescript(SCHEMA_SQL_P2.read_text(encoding="utf-8"))
        # [변경사유]: 003은 ALTER라 executescript 전체 실패 가능 — 컬럼 가드로 적용
        if SCHEMA_SQL_P2B.is_file():
            log.info("init_schema sql=%s (guarded)", SCHEMA_SQL_P2B.name)
            _apply_phase2b_columns(conn)
        conn.commit()


def start_batch(conn: sqlite3.Connection, root_rel: str) -> int:
    """배치 시작."""
    cur = conn.execute(
        "INSERT INTO import_batch (root_rel, started_at, status) VALUES (?, datetime('now'), 'running')",
        (root_rel,),
    )
    return int(cur.lastrowid)


def finish_batch(conn: sqlite3.Connection, batch_id: int, summary: dict[str, Any]) -> None:
    """배치 종료."""
    conn.execute(
        """
        UPDATE import_batch
        SET finished_at = datetime('now'), status = 'done', summary_json = ?
        WHERE id = ?
        """,
        (json.dumps(summary, ensure_ascii=False), batch_id),
    )


def upsert_chat_source(
    conn: sqlite3.Connection,
    *,
    rel_path: str,
    room_title: str | None,
    file_size: int,
    mtime_ns: int,
    content_sha256: str,
    encoding: str,
    batch_id: int,
) -> int:
    """채팅 소스 upsert → id."""
    conn.execute(
        """
        INSERT INTO chat_source (
          rel_path, room_title, file_size, mtime_ns, content_sha256, encoding, last_batch_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(rel_path) DO UPDATE SET
          room_title = excluded.room_title,
          file_size = excluded.file_size,
          mtime_ns = excluded.mtime_ns,
          content_sha256 = excluded.content_sha256,
          encoding = excluded.encoding,
          last_batch_id = excluded.last_batch_id,
          updated_at = datetime('now')
        """,
        (rel_path, room_title, file_size, mtime_ns, content_sha256, encoding, batch_id),
    )
    row = conn.execute("SELECT id FROM chat_source WHERE rel_path = ?", (rel_path,)).fetchone()
    return int(row["id"])


def replace_messages(
    conn: sqlite3.Connection, chat_id: int, messages: list[dict[str, Any]]
) -> None:
    """해당 chat 메시지 전량 교체 (멱등)."""
    conn.execute("DELETE FROM parsed_message WHERE chat_id = ?", (chat_id,))
    for m in messages:
        conn.execute(
            """
            INSERT INTO parsed_message (
              chat_id, seq, msg_kind, sender, abs_time, body_raw, body_norm, photo_count, line_no
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                m["seq"],
                m["msg_kind"],
                m.get("sender"),
                m.get("abs_time"),
                m["body_raw"],
                m.get("body_norm"),
                m.get("photo_count"),
                m.get("line_no"),
            ),
        )


def upsert_photo(
    conn: sqlite3.Connection,
    *,
    rel_path: str,
    file_name: str,
    ext: str | None,
    byte_size: int,
    mtime_ns: int,
    name_time: str | None,
    name_parse_ok: int,
    batch_id: int,
) -> int:
    """사진 upsert."""
    conn.execute(
        """
        INSERT INTO photo_file (
          rel_path, file_name, ext, byte_size, mtime_ns, name_time, name_parse_ok, last_batch_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(rel_path) DO UPDATE SET
          file_name = excluded.file_name,
          ext = excluded.ext,
          byte_size = excluded.byte_size,
          mtime_ns = excluded.mtime_ns,
          name_time = excluded.name_time,
          name_parse_ok = excluded.name_parse_ok,
          last_batch_id = excluded.last_batch_id,
          updated_at = datetime('now')
        """,
        (rel_path, file_name, ext, byte_size, mtime_ns, name_time, name_parse_ok, batch_id),
    )
    row = conn.execute("SELECT id FROM photo_file WHERE rel_path = ?", (rel_path,)).fetchone()
    return int(row["id"])


def update_photo_sha(conn: sqlite3.Connection, photo_id: int, sha256: str) -> None:
    """SHA 갱신."""
    conn.execute("UPDATE photo_file SET sha256 = ? WHERE id = ?", (sha256, photo_id))


def clear_batch_match_data(conn: sqlite3.Connection, batch_id: int) -> None:
    """배치 매칭 결과 삭제 후 재기록용."""
    conn.execute("DELETE FROM group_text WHERE group_id IN (SELECT id FROM image_group WHERE batch_id=?)", (batch_id,))
    conn.execute("DELETE FROM photo_message_assignment WHERE batch_id = ?", (batch_id,))
    conn.execute("DELETE FROM image_group WHERE batch_id = ?", (batch_id,))
    conn.execute("DELETE FROM review_item WHERE batch_id = ?", (batch_id,))
    conn.execute("DELETE FROM parse_error WHERE batch_id = ?", (batch_id,))


def insert_parse_error(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    source_rel: str | None,
    line_no: int | None,
    raw_excerpt: str | None,
    error_code: str,
    detail: str | None,
) -> None:
    """오류 upsert."""
    conn.execute(
        """
        INSERT INTO parse_error (batch_id, source_rel, line_no, raw_excerpt, error_code, detail)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(batch_id, source_rel, line_no, error_code) DO UPDATE SET
          raw_excerpt = excluded.raw_excerpt,
          detail = excluded.detail
        """,
        (batch_id, source_rel, line_no, raw_excerpt, error_code, detail),
    )


def rebuild_exact_groups(conn: sqlite3.Connection) -> dict[str, int]:
    """전체 photo_file SHA로 exact 그룹 재구성."""
    conn.execute("DELETE FROM exact_sha_member")
    conn.execute("DELETE FROM exact_sha_group")
    rows = conn.execute(
        "SELECT id, sha256 FROM photo_file WHERE sha256 IS NOT NULL AND sha256 != ''"
    ).fetchall()
    by_sha: dict[str, list[int]] = {}
    for r in rows:
        by_sha.setdefault(r["sha256"], []).append(int(r["id"]))
    groups = 0
    excluded = 0
    for sha, ids in by_sha.items():
        if len(ids) < 1:
            continue
        ids_sorted = sorted(ids)
        rep = ids_sorted[0]
        cur = conn.execute(
            "INSERT INTO exact_sha_group (sha256, representative_photo_id, member_count) VALUES (?, ?, ?)",
            (sha, rep, len(ids_sorted)),
        )
        gid = int(cur.lastrowid)
        groups += 1
        for i, pid in enumerate(ids_sorted):
            excl = 0 if i == 0 else 1
            if excl:
                excluded += 1
            conn.execute(
                """
                INSERT INTO exact_sha_member (group_id, photo_id, is_representative, excluded_from_upload)
                VALUES (?, ?, ?, ?)
                """,
                (gid, pid, 1 if i == 0 else 0, excl),
            )
    return {"exact_groups": groups, "excluded_from_upload": excluded}


def supersede_text_merges(conn: sqlite3.Connection, sha256: str | None = None) -> None:
    """active merge를 superseded로 (되돌리기 이력 보존)."""
    if sha256:
        conn.execute(
            "UPDATE text_merge SET status='superseded' WHERE sha256=? AND status='active'",
            (sha256,),
        )
    else:
        conn.execute("UPDATE text_merge SET status='superseded' WHERE status='active'")


def status_counts(db_path: Path) -> dict[str, int]:
    """건수."""
    if not db_path.is_file():
        return {"db_exists": 0}
    with connect(db_path) as conn:
        out: dict[str, int] = {"db_exists": 1}
        tables = [
            "import_batch",
            "chat_source",
            "parsed_message",
            "photo_file",
            "image_group",
            "photo_message_assignment",
            "exact_sha_group",
            "review_item",
            "parse_error",
        ]
        # Phase2 테이블은 있을 때만
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='text_merge'"
        ).fetchone()
        if row:
            tables.extend(["text_merge", "text_merge_source", "text_merge_conflict"])
        for t in tables:
            r = conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()
            out[t] = int(r["c"])
            log.info("status table=%s count=%s", t, out[t])
        return out
