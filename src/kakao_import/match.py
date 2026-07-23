# [변경사유]: Phase1 match stub — 로컬 내 동일 SHA 그룹 + peer 카탈로그 훅
"""SHA exact 매칭."""

from __future__ import annotations

from pathlib import Path

from kakao_import.db import connect
from kakao_import.logging_util import get_logger

log = get_logger(__name__)


def match_sha_only(db_path: Path) -> dict[str, int]:
    """
    local_media SHA 기준 exact 매칭.
    - 동일 SHA가 2건 이상 → match_kind=exact, peer_ref=local_dup
    - 단독 → match_kind=none (서버 카탈로그 연동은 Phase 3)
    """
    exact = 0
    none = 0
    with connect(db_path) as conn:
        # [변경사유]: 쿼리 로그
        q = """
        SELECT sha256, COUNT(*) AS c
        FROM local_media
        WHERE sha256 IS NOT NULL AND sha256 != ''
        GROUP BY sha256
        """
        log.info("match query=%s", " ".join(q.split()))
        groups = conn.execute(q).fetchall()
        for g in groups:
            sha = g["sha256"]
            count = int(g["c"])
            rows = conn.execute(
                "SELECT id FROM local_media WHERE sha256 = ?", (sha,)
            ).fetchall()
            if count >= 2:
                peer = ",".join(str(r["id"]) for r in rows)
                for r in rows:
                    conn.execute(
                        """
                        INSERT INTO match_result (
                          media_id, match_kind, peer_sha256, peer_ref, confidence, decision_hint
                        ) VALUES (?, 'exact', ?, ?, 1.0, 'local_dup')
                        ON CONFLICT(media_id) DO UPDATE SET
                          match_kind = excluded.match_kind,
                          peer_sha256 = excluded.peer_sha256,
                          peer_ref = excluded.peer_ref,
                          confidence = excluded.confidence,
                          decision_hint = excluded.decision_hint
                        """,
                        (int(r["id"]), sha, f"local_dup:{peer}"),
                    )
                    exact += 1
                log.info("match exact sha=%s count=%s", sha[:12], count)
            else:
                mid = int(rows[0]["id"])
                conn.execute(
                    """
                    INSERT INTO match_result (
                      media_id, match_kind, peer_sha256, peer_ref, confidence, decision_hint
                    ) VALUES (?, 'none', ?, NULL, 0.0, 'unmatched')
                    ON CONFLICT(media_id) DO UPDATE SET
                      match_kind = excluded.match_kind,
                      peer_sha256 = excluded.peer_sha256,
                      peer_ref = excluded.peer_ref,
                      confidence = excluded.confidence,
                      decision_hint = excluded.decision_hint
                    """,
                    (mid, sha),
                )
                none += 1
        conn.commit()
    summary = {"exact": exact, "none": none}
    log.info("match done %s", summary)
    return summary
