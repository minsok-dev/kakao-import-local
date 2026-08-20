# [변경사유]: 사람 확정 > 모델. 재학습해도 human 행 유지
"""포스터 사람 라벨."""

from __future__ import annotations

from typing import Any

from kakao_import.db import connect
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
    """human 확정. 파일은 건드리지 않음."""
    sha = sha256.strip().lower()
    if len(sha) != 64:
        return {"ok": False, "error": "sha256 must be 64 hex"}
    if status not in ("poster", "uncertain", "non_poster"):
        return {"ok": False, "error": f"bad status={status}"}
    if not settings.db_path.exists():
        return {"ok": False, "error": "db missing"}
    with connect(settings.db_path) as conn:
        ensure_poster_schema(conn)
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
            return {"ok": False, "error": "photo_file sha not found"}
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
        conn.commit()
        check = get_classify_row(conn, room_id or "", sha) if room_id else None
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
