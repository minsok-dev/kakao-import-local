# [변경사유]: 사람 확정 > 모델. 재학습해도 human 행 유지. non_poster 는 폴더를 분리한다
"""포스터 사람 라벨."""

from __future__ import annotations

import sqlite3
from typing import Any

from kakao_import.db import session
from kakao_import.logging_util import get_logger
from kakao_import.poster_const import SOURCE_HUMAN
from kakao_import.poster_decision import excluded_flag
from kakao_import.poster_schema import ensure_poster_schema, get_classify_row
from kakao_import.upload_state import mark_candidates_needs_rebuild_by_sha

log = get_logger(__name__)


def cmd_poster_label(
    settings,
    *,
    sha256: str,
    status: str,
    room_id: str | None = None,
) -> dict[str, Any]:
    """human 확정. non_poster 는 non_posters 로, 그 외는 photos 로 파일을 맞춘다."""
    sha = sha256.strip().lower()
    if len(sha) != 64:
        return {"ok": False, "error": "sha256 must be 64 hex"}
    if status not in ("poster", "uncertain", "non_poster"):
        return {"ok": False, "error": f"bad status={status}"}
    if not settings.db_path.exists():
        return {"ok": False, "error": "db missing"}
    # [변경사유]: 잠금 오류는 트레이스백 대신 ok=false 로 돌려 리뷰 UI가 멈추지 않게 한다
    try:
        updated, check = _write_human_label(settings, sha=sha, status=status, room_id=room_id)
    except sqlite3.OperationalError as exc:
        log.error("poster-label sqlite error sha=%s err=%s", sha[:12], exc)
        return {"ok": False, "error": str(exc)}
    if updated < 0:
        return {"ok": False, "error": "photo_file sha not found"}
    rebuild_count = mark_candidates_needs_rebuild_by_sha(
        settings.db_path,
        sha256=sha,
        reason=f"poster_label:{status}",
    )
    return {
        "ok": True,
        "updated": updated,
        "sha256": sha,
        "status": status,
        "row": check,
        "rebuild_marked": rebuild_count,
    }


def _write_human_label(
    settings,
    *,
    sha: str,
    status: str,
    room_id: str | None,
) -> tuple[int, dict[str, Any] | None]:
    """human 행을 저장하고 파일을 옮긴다. 사진이 없으면 updated=-1."""
    with session(settings.db_path) as conn:
        # [변경사유]: 스키마가 있으면 DDL 을 다시 실행하지 않는다 (ensure 내부에서 가드)
        ensure_poster_schema(conn)
        log.info("poster-label query photo_file sha=%s", sha[:12])
        row = conn.execute(
            """
            SELECT id, rel_path, file_name, sha256
            FROM photo_file
            WHERE lower(sha256) = ?
            ORDER BY id
            """,
            (sha,),
        ).fetchall()
        if not row:
            return -1, None
        updated = 0
        for r in row:
            rel = str(r["rel_path"] or "").replace("\\", "/")
            from kakao_import.pipeline import room_id_from_rel

            rid = room_id or room_id_from_rel(rel)
            if room_id and rid != room_id:
                continue
            name = str(r["file_name"] or "")
            excl = excluded_flag(status)
            conn.execute(
                """
                INSERT INTO poster_classify (
                  room_id, photo_id, sha256, rel_path, file_name,
                  model_version, poster_score, status, source, excluded_from_upload, classified_at
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, datetime('now'))
                ON CONFLICT(room_id, sha256) DO UPDATE SET
                  photo_id = excluded.photo_id,
                  rel_path = excluded.rel_path,
                  file_name = excluded.file_name,
                  status = excluded.status,
                  source = 'human',
                  excluded_from_upload = excluded.excluded_from_upload,
                  classified_at = datetime('now')
                """,
                (rid, int(r["id"]), sha, rel, name, status, SOURCE_HUMAN, excl),
            )
            updated += 1
            log.info(
                "poster-label human room=%s sha=%s status=%s excl=%s",
                rid,
                sha[:12],
                status,
                excl,
            )
            # [변경사유]: 사람 라벨 직후 실제 파일을 상태 폴더로 옮긴다
            root = settings.export_root
            if root is not None:
                from kakao_import.poster_place import place_classified_photo

                place_classified_photo(conn, root, int(r["id"]), rel, status)
        conn.commit()
        check = get_classify_row(conn, room_id or "", sha) if room_id else None
    return updated, check
