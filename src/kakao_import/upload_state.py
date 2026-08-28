# [변경사유]: 증분 업로드/스케줄러 — 후보 상태/보류 목록/재평가 트리거 관리
"""upload candidate state helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from kakao_import.db import connect
from kakao_import.ledger import (
    has_uploaded_fingerprint,
    is_uploaded_media_fingerprint,
)
from kakao_import.logging_util import get_logger

log = get_logger(__name__)

HOLD_STATES = (
    "hold_poster_uncertain",
    "hold_similar_deferred",
    "hold_missing_file",
    "hold_manual_review",
)

TERMINAL_SKIP_STATES = (
    "uploaded",
    "excluded_non_poster",
    "excluded_group_member",
    "hold_poster_uncertain",
    "hold_similar_deferred",
    "hold_missing_file",
    "hold_manual_review",
    "failed_terminal",
)


def _candidate_tables_exist(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='upload_candidate'"
    ).fetchone()
    return row is not None


def mark_candidates_needs_rebuild_by_sha(
    db_path: Path,
    *,
    sha256: str,
    reason: str,
) -> int:
    """특정 SHA를 멤버로 가진 후보를 재평가 대상으로 표시."""
    sha = str(sha256 or "").strip().lower()
    if len(sha) != 64:
        return 0
    with connect(db_path) as conn:
        if not _candidate_tables_exist(conn):
            return 0
        cur = conn.execute(
            """
            UPDATE upload_candidate
            SET needs_rebuild = 1,
                state = CASE
                  WHEN state IN ('uploaded', 'excluded_non_poster', 'excluded_group_member',
                                 'hold_poster_uncertain', 'hold_similar_deferred',
                                 'hold_missing_file', 'hold_manual_review',
                                 'retry_wait', 'failed_terminal')
                  THEN 'new'
                  ELSE state
                END,
                state_reason = ?,
                updated_at = datetime('now')
            WHERE id IN (
              SELECT DISTINCT candidate_id
              FROM upload_candidate_member
              WHERE lower(IFNULL(sha256, '')) = ?
            )
            """,
            (reason, sha),
        )
        conn.commit()
        updated = int(cur.rowcount or 0)
    log.info(
        "upload-state mark rebuild by sha sha_prefix=%s updated=%s reason=%s",
        sha[:12],
        updated,
        reason,
    )
    return updated


def mark_candidates_needs_rebuild_by_similar_group(
    db_path: Path,
    *,
    group_id: int,
    reason: str,
) -> int:
    """특정 similar group과 연결된 후보를 재평가 대상으로 표시."""
    gid = int(group_id or 0)
    if gid <= 0:
        return 0
    with connect(db_path) as conn:
        if not _candidate_tables_exist(conn):
            return 0
        cur = conn.execute(
            """
            UPDATE upload_candidate
            SET needs_rebuild = 1,
                state = CASE
                  WHEN state IN ('uploaded', 'excluded_non_poster', 'excluded_group_member',
                                 'hold_poster_uncertain', 'hold_similar_deferred',
                                 'hold_missing_file', 'hold_manual_review',
                                 'retry_wait', 'failed_terminal')
                  THEN 'new'
                  ELSE state
                END,
                state_reason = ?,
                updated_at = datetime('now')
            WHERE similar_group_id = ?
            """,
            (reason, gid),
        )
        conn.commit()
        updated = int(cur.rowcount or 0)
    log.info(
        "upload-state mark rebuild by similar group group_id=%s updated=%s reason=%s",
        gid,
        updated,
        reason,
    )
    return updated


def mark_hold_candidates_caption_stale(db_path: Path, *, reason: str) -> int:
    """
    보류(hold) 후보의 캡션만 다음 실행에서 다시 조립하게 표시.

    [변경사유]: 재매칭으로 group_text 가 바뀌어도 hold 상태 sha 는 skip_shas 에 걸려
      옛 캡션이 그대로 남던 문제. state 는 건드리지 않으므로 보류가 풀리지 않는다.
      (uploaded 는 제외 — 재업로드 유발 금지)
    """
    if not db_path.is_file():
        return 0
    placeholders = ",".join("?" for _ in HOLD_STATES)
    with connect(db_path) as conn:
        if not _candidate_tables_exist(conn):
            return 0
        cur = conn.execute(
            f"""
            UPDATE upload_candidate
            SET needs_rebuild = 1,
                updated_at = datetime('now')
            WHERE state IN ({placeholders})
              AND IFNULL(needs_rebuild, 0) = 0
            """,
            tuple(HOLD_STATES),
        )
        conn.commit()
        updated = int(cur.rowcount or 0)
    log.info(
        "upload-state mark hold caption stale updated=%s reason=%s",
        updated,
        reason,
    )
    return updated


def hold_report(db_path: Path) -> dict[str, Any]:
    """현재 hold / retry / failed 상태 요약."""
    out: dict[str, Any] = {
        "db_exists": 1 if db_path.is_file() else 0,
        "candidate_tables_exist": 0,
        "counts": {},
        "items": [],
    }
    if not db_path.is_file():
        return out
    with connect(db_path) as conn:
        if not _candidate_tables_exist(conn):
            return out
        out["candidate_tables_exist"] = 1
        states = list(HOLD_STATES) + ["retry_wait", "failed_terminal"]
        counts: dict[str, int] = {}
        for state in states:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM upload_candidate WHERE state = ?",
                (state,),
            ).fetchone()
            counts[state] = int(row["c"] or 0)
        rows = conn.execute(
            """
            SELECT
              c.id,
              c.candidate_key,
              c.media_fingerprint,
              c.state,
              c.state_reason,
              c.similar_group_id,
              c.similar_decision,
              c.poster_status,
              c.attempt_count,
              c.next_retry_at,
              c.updated_at
            FROM upload_candidate c
            WHERE c.state IN (
              'hold_poster_uncertain',
              'hold_similar_deferred',
              'hold_missing_file',
              'hold_manual_review',
              'retry_wait',
              'failed_terminal'
            )
            ORDER BY c.updated_at DESC, c.id DESC
            LIMIT 200
            """
        ).fetchall()
        out["counts"] = counts
        out["items"] = [dict(r) for r in rows]
    return out


def _evaluation_fingerprint(media_fingerprint: str, caption_fingerprint: str | None) -> str:
    media_fp = str(media_fingerprint or "").strip().lower()
    caption_fp = str(caption_fingerprint or "").strip().lower()
    return f"{media_fp}:{caption_fp}"


def load_incremental_caption_plan(db_path: Path) -> dict[str, Any]:
    """manifest caption 재계산 대상 판별.

    - skip_shas: 완전 스킵 (uploaded/hold/excluded 등, needs_rebuild=0)
    - caption_cache: needs_rebuild=0 이고 캐시 본문이 있으면 SQL 재조립 없이 재사용
    """
    out: dict[str, Any] = {
        "candidate_tables_exist": 0,
        "skip_shas": set(),
        "caption_cache": {},
        "skip_count": 0,
        "cache_count": 0,
    }
    if not db_path.is_file():
        return out
    with connect(db_path) as conn:
        if not _candidate_tables_exist(conn):
            return out
        out["candidate_tables_exist"] = 1
        rows = conn.execute(
            """
            SELECT
              lower(IFNULL(m.sha256, '')) AS sha,
              c.state,
              IFNULL(c.needs_rebuild, 0) AS needs_rebuild,
              c.caption_text_cached
            FROM upload_candidate_member m
            JOIN upload_candidate c ON c.id = m.candidate_id
            WHERE m.sha256 IS NOT NULL
              AND length(m.sha256) = 64
            """
        ).fetchall()
        skip_shas: set[str] = set()
        caption_cache: dict[str, str] = {}
        for row in rows:
            sha = str(row["sha"] or "").strip().lower()
            if len(sha) != 64:
                continue
            needs_rebuild = int(row["needs_rebuild"] or 0) == 1
            if needs_rebuild:
                continue
            state = str(row["state"] or "")
            cached = row["caption_text_cached"]
            if state in TERMINAL_SKIP_STATES:
                skip_shas.add(sha)
                continue
            if cached is not None and state in (
                "new",
                "retry_wait",
                "ready",
                "evaluating",
            ):
                # [변경사유]: 미업로드라도 캐시가 있으면 caption SQL 재조립 생략
                caption_cache[sha] = str(cached)
        out["skip_shas"] = skip_shas
        out["caption_cache"] = caption_cache
        # [변경사유]: I1 — 장부 source_sha256 기준 skip (bundle media_fp 와 exact sha 불일치 방지)
        try:
            ledger_rows = conn.execute(
                """
                SELECT lower(source_sha256) AS sha
                FROM uploaded_sha_ledger
                WHERE IFNULL(rejected, 0) = 0
                  AND length(IFNULL(source_sha256, '')) = 64
                """
            ).fetchall()
            for row in ledger_rows:
                sha = str(row["sha"] or "").strip().lower()
                if len(sha) == 64 and sha not in caption_cache:
                    skip_shas.add(sha)
        except Exception:  # noqa: BLE001 — 구 스키마 무시
            pass
        out["skip_shas"] = skip_shas
        out["skip_count"] = len(skip_shas)
        out["cache_count"] = len(caption_cache)
    log.info(
        "upload-state caption plan skip=%s cache=%s",
        out["skip_count"],
        out["cache_count"],
    )
    return out


def record_similar_policy_skips(
    db_path: Path,
    *,
    skipped: list[dict[str, Any]],
    deferred_groups: list[dict[str, Any]],
) -> dict[str, int]:
    """similar 정책으로 제외/보류된 후보를 상태 테이블에 남겨 다음 caption 재계산을 생략."""
    out = {"excluded_group_member": 0, "hold_similar_deferred": 0}
    if not skipped and not deferred_groups:
        return out
    with connect(db_path) as conn:
        if not _candidate_tables_exist(conn):
            return out
        deferred_ids = {
            int(g.get("group_id") or 0)
            for g in deferred_groups
            if int(g.get("group_id") or 0) > 0
        }
        for item in skipped:
            photo_id = int(item.get("photo_id") or 0)
            reason = str(item.get("reason") or "")
            group_id = int(item.get("group_id") or 0)
            sha_row = conn.execute(
                "SELECT lower(IFNULL(sha256, '')) AS sha FROM photo_file WHERE id = ?",
                (photo_id,),
            ).fetchone()
            sha = str((sha_row["sha"] if sha_row else "") or "").strip().lower()
            if len(sha) != 64:
                continue
            if reason == "similar_deferred" or group_id in deferred_ids:
                state = "hold_similar_deferred"
                state_reason = "similar_deferred"
                out["hold_similar_deferred"] += 1
            else:
                state = "excluded_group_member"
                state_reason = reason or "similar_non_representative"
                out["excluded_group_member"] += 1
            candidate_key = f"media:{sha}"
            conn.execute(
                """
                INSERT INTO upload_candidate (
                  candidate_key, media_fingerprint, evaluation_fingerprint,
                  state, state_reason, similar_group_id, similar_decision,
                  needs_rebuild, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, datetime('now'))
                ON CONFLICT(candidate_key) DO UPDATE SET
                  state = excluded.state,
                  state_reason = excluded.state_reason,
                  similar_group_id = excluded.similar_group_id,
                  similar_decision = excluded.similar_decision,
                  needs_rebuild = 0,
                  updated_at = datetime('now')
                """,
                (
                    candidate_key,
                    sha,
                    f"{sha}:",
                    state,
                    state_reason,
                    group_id or None,
                    str(item.get("decision") or "") or None,
                ),
            )
            candidate_row = conn.execute(
                "SELECT id FROM upload_candidate WHERE candidate_key = ?",
                (candidate_key,),
            ).fetchone()
            candidate_id = int(candidate_row["id"])
            conn.execute(
                "DELETE FROM upload_candidate_member WHERE candidate_id = ?",
                (candidate_id,),
            )
            conn.execute(
                """
                INSERT INTO upload_candidate_member (
                  candidate_id, photo_id, sha256, member_role, sort_order, rel_path
                ) VALUES (?, ?, ?, 'main', 0, ?)
                """,
                (
                    candidate_id,
                    photo_id,
                    sha,
                    str(item.get("rel") or "").replace("\\", "/") or None,
                ),
            )
        conn.commit()
    log.info(
        "upload-state record similar skips excluded=%s deferred=%s",
        out["excluded_group_member"],
        out["hold_similar_deferred"],
    )
    return out


def sync_upload_candidates(
    db_path: Path,
    *,
    requests: list[dict[str, Any]],
) -> dict[str, Any]:
    """manifest requests 를 후보 상태 테이블과 동기화하고 업로드 대상만 반환."""
    out: dict[str, Any] = {
        "candidate_tables_exist": 0,
        "total_requests": len(requests),
        "synced": 0,
        "actionable_count": 0,
        "skipped_uploaded_count": 0,
        "actionable_requests": [],
    }
    if not requests:
        return out
    with connect(db_path) as conn:
        if not _candidate_tables_exist(conn):
            out["actionable_requests"] = requests
            out["actionable_count"] = len(requests)
            return out
        out["candidate_tables_exist"] = 1
        actionable: list[dict[str, Any]] = []
        for req in requests:
            item = req.get("item") if isinstance(req.get("item"), dict) else {}
            media_fp = str(req.get("media_fingerprint") or "").strip().lower()
            caption_fp = str(req.get("caption_fingerprint") or "").strip().lower()
            candidate_key = str(req.get("candidate_key") or media_fp or item.get("local_item_id") or "").strip()
            if not candidate_key:
                actionable.append(req)
                continue
            eval_fp = _evaluation_fingerprint(media_fp, caption_fp)
            caption_text = "\n".join(
                str(m.get("text") or "")
                for m in (item.get("matched_messages") or [])
                if isinstance(m, dict)
            ) or None
            row = conn.execute(
                """
                SELECT id, state, evaluation_fingerprint, needs_rebuild, next_retry_at
                FROM upload_candidate
                WHERE candidate_key = ?
                """,
                (candidate_key,),
            ).fetchone()
            ledger_uploaded = has_uploaded_fingerprint(
                db_path,
                media_fingerprint=media_fp,
                caption_fingerprint=caption_fp or None,
            )
            # [변경사유]: I1 — 미디어만 장부에 있어도 full HTTP 재전송 금지
            media_uploaded = is_uploaded_media_fingerprint(db_path, media_fp)
            state = "new"
            state_reason = "new_candidate"
            if row:
                current_state = str(row["state"] or "")
                needs_rebuild = int(row["needs_rebuild"] or 0) == 1
                same_eval = str(row["evaluation_fingerprint"] or "") == eval_fp
                if not needs_rebuild and same_eval and current_state in TERMINAL_SKIP_STATES:
                    state = current_state
                    state_reason = f"keep_state:{current_state}"
                elif not needs_rebuild and same_eval and current_state == "retry_wait":
                    state = "retry_wait"
                    state_reason = "keep_state:retry_wait"
                elif not needs_rebuild and (
                    ledger_uploaded
                    or media_uploaded
                    or current_state == "uploaded"
                ):
                    # [변경사유]: 캡션 지문이 달라도 동일 미디어면 uploaded 유지 (재업로드 HTTP 폭주 방지)
                    state = "uploaded"
                    state_reason = (
                        "ledger_match"
                        if ledger_uploaded
                        else (
                            "ledger_media_match"
                            if media_uploaded
                            else "keep_state:uploaded"
                        )
                    )
                else:
                    state = "new"
                    state_reason = "candidate_changed"
            elif ledger_uploaded or media_uploaded:
                state = "uploaded"
                state_reason = "ledger_match" if ledger_uploaded else "ledger_media_match"

            conn.execute(
                """
                INSERT INTO upload_candidate (
                  candidate_key, media_fingerprint, caption_fingerprint,
                  evaluation_fingerprint, state, state_reason, caption_text_cached,
                  needs_rebuild, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, datetime('now'))
                ON CONFLICT(candidate_key) DO UPDATE SET
                  media_fingerprint = excluded.media_fingerprint,
                  caption_fingerprint = excluded.caption_fingerprint,
                  evaluation_fingerprint = excluded.evaluation_fingerprint,
                  state = excluded.state,
                  state_reason = excluded.state_reason,
                  caption_text_cached = excluded.caption_text_cached,
                  needs_rebuild = 0,
                  updated_at = datetime('now')
                """,
                (
                    candidate_key,
                    media_fp,
                    caption_fp or None,
                    eval_fp,
                    state,
                    state_reason,
                    caption_text,
                ),
            )
            candidate_row = conn.execute(
                "SELECT id FROM upload_candidate WHERE candidate_key = ?",
                (candidate_key,),
            ).fetchone()
            candidate_id = int(candidate_row["id"])
            conn.execute(
                "DELETE FROM upload_candidate_member WHERE candidate_id = ?",
                (candidate_id,),
            )
            members = [item]
            members.extend(
                s for s in (item.get("sub_images") or []) if isinstance(s, dict)
            )
            for sort_order, member in enumerate(members):
                sha = str(member.get("sha256") or "").strip().lower() or None
                photo_id = int(member.get("photo_id") or 0)
                if photo_id <= 0:
                    photo_id = -1 * (sort_order + 1)
                rel_path = str(member.get("rel_path") or "").replace("\\", "/") or None
                conn.execute(
                    """
                    INSERT INTO upload_candidate_member (
                      candidate_id, photo_id, sha256, member_role, sort_order, rel_path
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate_id,
                        photo_id,
                        sha,
                        "main" if sort_order == 0 else "sub",
                        sort_order,
                        rel_path,
                    ),
                )
            out["synced"] = int(out["synced"]) + 1
            if state == "uploaded":
                out["skipped_uploaded_count"] = int(out["skipped_uploaded_count"]) + 1
                continue
            if state in HOLD_STATES or state == "failed_terminal":
                continue
            if state == "retry_wait":
                retry_row = conn.execute(
                    """
                    SELECT 1
                    FROM upload_candidate
                    WHERE id = ?
                      AND (next_retry_at IS NULL OR next_retry_at <= datetime('now'))
                    """,
                    (candidate_id,),
                ).fetchone()
                if not retry_row:
                    continue
            actionable.append(req)
        conn.commit()
        out["actionable_requests"] = actionable
        out["actionable_count"] = len(actionable)
    log.info(
        "upload-state sync total=%s synced=%s actionable=%s skipped_uploaded=%s",
        out["total_requests"],
        out["synced"],
        out["actionable_count"],
        out["skipped_uploaded_count"],
    )
    return out


def _state_priority(state: str) -> int:
    order = {
        "excluded_non_poster": 50,
        "hold_poster_uncertain": 40,
        "hold_missing_file": 30,
        "hold_similar_deferred": 25,
        "retry_wait": 20,
        "failed_terminal": 10,
        "uploaded": 5,
        "new": 0,
    }
    return order.get(state, 0)


def _resolve_member_policy(
    conn: sqlite3.Connection,
    *,
    sha256_values: list[str],
) -> tuple[str | None, str | None]:
    final_state: str | None = None
    final_reason: str | None = None
    for sha in sha256_values:
        if len(sha) != 64:
            continue
        rows = conn.execute(
            """
            SELECT status, source, excluded_from_upload, classified_at
            FROM poster_classify
            WHERE lower(sha256) = ?
            ORDER BY classified_at DESC, id DESC
            """,
            (sha,),
        ).fetchall()
        for row in rows:
            status = str(row["status"] or "")
            excluded = int(row["excluded_from_upload"] or 0)
            if excluded == 1 or status == "non_poster":
                cand_state = "excluded_non_poster"
                cand_reason = f"poster:{status}"
            elif status == "uncertain":
                cand_state = "hold_poster_uncertain"
                cand_reason = "poster:uncertain"
            else:
                continue
            if final_state is None or _state_priority(cand_state) > _state_priority(final_state):
                final_state = cand_state
                final_reason = cand_reason
            break
    return final_state, final_reason


def apply_candidate_classification(
    db_path: Path,
    *,
    classified: dict[str, Any],
) -> dict[str, int]:
    """분류 결과(file_missing/poster 등)를 후보 상태에 반영."""
    out = {
        "hold_missing_file": 0,
        "hold_poster_uncertain": 0,
        "excluded_non_poster": 0,
    }
    with connect(db_path) as conn:
        if not _candidate_tables_exist(conn):
            return out
        handled_keys: set[str] = set()
        for group in ("ready", "empty_adjacent", "file_missing"):
            for entry in classified.get(group) or []:
                candidate_key = str(entry.get("candidate_key") or "").strip()
                if not candidate_key or candidate_key in handled_keys:
                    continue
                handled_keys.add(candidate_key)
                row = conn.execute(
                    """
                    SELECT id, state
                    FROM upload_candidate
                    WHERE candidate_key = ?
                    """,
                    (candidate_key,),
                ).fetchone()
                if not row:
                    continue
                candidate_id = int(row["id"])
                target_state: str | None = None
                target_reason: str | None = None
                if group == "file_missing":
                    target_state = "hold_missing_file"
                    target_reason = "file_missing"
                else:
                    sha_rows = conn.execute(
                        """
                        SELECT lower(IFNULL(sha256, '')) AS sha
                        FROM upload_candidate_member
                        WHERE candidate_id = ?
                        ORDER BY sort_order
                        """,
                        (candidate_id,),
                    ).fetchall()
                    member_shas = [str(r["sha"]) for r in sha_rows if str(r["sha"] or "")]
                    policy_state, policy_reason = _resolve_member_policy(
                        conn,
                        sha256_values=member_shas,
                    )
                    target_state = policy_state
                    target_reason = policy_reason
                if not target_state:
                    continue
                conn.execute(
                    """
                    UPDATE upload_candidate
                    SET state = ?, state_reason = ?, updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (target_state, target_reason, candidate_id),
                )
                out[target_state] = int(out.get(target_state, 0)) + 1
        conn.commit()
    return out


def mark_candidate_uploaded(
    db_path: Path,
    *,
    candidate_key: str,
    request_idx: int | None,
    ocr_idx: int | None,
) -> None:
    """업로드 성공 상태 반영."""
    with connect(db_path) as conn:
        if not _candidate_tables_exist(conn):
            return
        conn.execute(
            """
            UPDATE upload_candidate
            SET state = 'uploaded',
                state_reason = 'upload_success',
                server_receipt_id = COALESCE(?, server_receipt_id),
                server_ocr_id = COALESCE(?, server_ocr_id),
                uploaded_at = datetime('now'),
                attempt_count = 0,
                next_retry_at = NULL,
                last_error_code = NULL,
                last_error = NULL,
                updated_at = datetime('now')
            WHERE candidate_key = ?
            """,
            (
                str(request_idx) if request_idx is not None else None,
                ocr_idx,
                candidate_key,
            ),
        )
        conn.commit()


def mark_candidate_retry(
    db_path: Path,
    *,
    candidate_key: str,
    error_code: str,
    error_message: str,
    terminal: bool = False,
) -> dict[str, Any]:
    """업로드 실패 시 retry_wait 또는 failed_terminal 반영."""
    with connect(db_path) as conn:
        if not _candidate_tables_exist(conn):
            return {"state": "missing_table", "attempt_count": 0}
        row = conn.execute(
            """
            SELECT id, attempt_count
            FROM upload_candidate
            WHERE candidate_key = ?
            """,
            (candidate_key,),
        ).fetchone()
        if not row:
            return {"state": "missing_candidate", "attempt_count": 0}
        attempt_count = int(row["attempt_count"] or 0) + 1
        next_state = "failed_terminal" if terminal or attempt_count >= 5 else "retry_wait"
        if next_state == "retry_wait":
            backoff_sec = min(3600, 60 * (2 ** max(0, attempt_count - 1)))
            conn.execute(
                """
                UPDATE upload_candidate
                SET state = 'retry_wait',
                    state_reason = 'upload_retry',
                    attempt_count = ?,
                    next_retry_at = datetime('now', '+' || ? || ' seconds'),
                    last_error_code = ?,
                    last_error = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (attempt_count, backoff_sec, error_code, error_message[:500], int(row["id"])),
            )
            conn.commit()
            return {
                "state": next_state,
                "attempt_count": attempt_count,
                "next_retry_in_sec": backoff_sec,
            }
        conn.execute(
            """
            UPDATE upload_candidate
            SET state = 'failed_terminal',
                state_reason = 'upload_terminal_error',
                attempt_count = ?,
                next_retry_at = NULL,
                last_error_code = ?,
                last_error = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (attempt_count, error_code, error_message[:500], int(row["id"])),
        )
        conn.commit()
        return {"state": next_state, "attempt_count": attempt_count}


def uploadable_candidate_keys(
    db_path: Path,
    *,
    candidate_keys: list[str],
) -> set[str]:
    """현재 업로드 큐에 올릴 수 있는 candidate_key 집합."""
    keys = [str(k).strip() for k in candidate_keys if str(k).strip()]
    if not keys:
        return set()
    with connect(db_path) as conn:
        if not _candidate_tables_exist(conn):
            return set(keys)
        placeholders = ",".join("?" for _ in keys)
        rows = conn.execute(
            f"""
            SELECT candidate_key, state, next_retry_at
            FROM upload_candidate
            WHERE candidate_key IN ({placeholders})
            """,
            tuple(keys),
        ).fetchall()
        out: set[str] = set()
        for row in rows:
            state = str(row["state"] or "")
            key = str(row["candidate_key"] or "")
            if state in (
                "uploaded",
                "excluded_non_poster",
                "hold_poster_uncertain",
                "hold_missing_file",
                "hold_similar_deferred",
                "hold_manual_review",
                "failed_terminal",
            ):
                continue
            if state == "retry_wait":
                gate = conn.execute(
                    """
                    SELECT 1
                    WHERE ? IS NULL OR ? <= datetime('now')
                    """,
                    (row["next_retry_at"], row["next_retry_at"]),
                ).fetchone()
                if not gate:
                    continue
            out.add(key)
    return out


def acquire_run_lock(
    db_path: Path,
    *,
    lock_name: str = "upload",
    owner: str,
    ttl_sec: int = 7200,
) -> dict[str, Any]:
    """
    프로세스 실행 lock.
    [변경사유]: I1 — 스케줄러 중복 upload 인스턴스 방지 (upload_run_lock)
    """
    out: dict[str, Any] = {
        "acquired": False,
        "owner": owner,
        "lock_name": lock_name,
    }
    with connect(db_path) as conn:
        if not _candidate_tables_exist(conn):
            out["acquired"] = True
            out["skipped_no_table"] = True
            return out
        row = conn.execute(
            """
            SELECT owner, lease_expires_at
            FROM upload_run_lock
            WHERE lock_name = ?
            """,
            (lock_name,),
        ).fetchone()
        if row and row["lease_expires_at"]:
            alive = conn.execute(
                "SELECT 1 WHERE ? > datetime('now')",
                (str(row["lease_expires_at"]),),
            ).fetchone()
            if alive and str(row["owner"] or "") != owner:
                out["blocked_by"] = str(row["owner"] or "")
                out["expires_at"] = str(row["lease_expires_at"])
                log.warning(
                    "upload-run-lock busy name=%s owner=%s expires=%s",
                    lock_name,
                    out["blocked_by"],
                    out["expires_at"],
                )
                return out
        conn.execute(
            """
            INSERT INTO upload_run_lock (lock_name, owner, acquired_at, lease_expires_at)
            VALUES (?, ?, datetime('now'), datetime('now', '+' || ? || ' seconds'))
            ON CONFLICT(lock_name) DO UPDATE SET
              owner = excluded.owner,
              acquired_at = datetime('now'),
              lease_expires_at = excluded.lease_expires_at
            """,
            (lock_name, owner, int(ttl_sec)),
        )
        conn.commit()
        out["acquired"] = True
        log.info(
            "upload-run-lock acquired name=%s owner=%s ttl=%s",
            lock_name,
            owner,
            ttl_sec,
        )
    return out


def release_run_lock(
    db_path: Path,
    *,
    lock_name: str = "upload",
    owner: str,
) -> None:
    """실행 lock 해제 (본인 owner 만)."""
    with connect(db_path) as conn:
        if not _candidate_tables_exist(conn):
            return
        conn.execute(
            """
            DELETE FROM upload_run_lock
            WHERE lock_name = ? AND owner = ?
            """,
            (lock_name, owner),
        )
        conn.commit()
        log.info("upload-run-lock released name=%s owner=%s", lock_name, owner)


def acquire_candidate_lease(
    db_path: Path,
    *,
    candidate_key: str,
    owner: str,
    ttl_sec: int = 900,
) -> bool:
    """
    후보 lease.
    [변경사유]: I1 — 동일 candidate 동시 전송 방지
    """
    key = str(candidate_key or "").strip()
    if not key:
        return True
    with connect(db_path) as conn:
        if not _candidate_tables_exist(conn):
            return True
        row = conn.execute(
            """
            SELECT lease_owner, lease_expires_at
            FROM upload_candidate
            WHERE candidate_key = ?
            """,
            (key,),
        ).fetchone()
        if not row:
            return True
        if row["lease_expires_at"] and str(row["lease_owner"] or "") != owner:
            alive = conn.execute(
                "SELECT 1 WHERE ? > datetime('now')",
                (str(row["lease_expires_at"]),),
            ).fetchone()
            if alive:
                log.info(
                    "upload-candidate-lease busy key=%s owner=%s",
                    key[:48],
                    row["lease_owner"],
                )
                return False
        conn.execute(
            """
            UPDATE upload_candidate
            SET lease_owner = ?,
                lease_expires_at = datetime('now', '+' || ? || ' seconds'),
                updated_at = datetime('now')
            WHERE candidate_key = ?
            """,
            (owner, int(ttl_sec), key),
        )
        conn.commit()
        return True


def release_candidate_lease(
    db_path: Path,
    *,
    candidate_key: str,
    owner: str,
) -> None:
    """후보 lease 해제."""
    key = str(candidate_key or "").strip()
    if not key:
        return
    with connect(db_path) as conn:
        if not _candidate_tables_exist(conn):
            return
        conn.execute(
            """
            UPDATE upload_candidate
            SET lease_owner = NULL,
                lease_expires_at = NULL,
                updated_at = datetime('now')
            WHERE candidate_key = ? AND IFNULL(lease_owner, '') = ?
            """,
            (key, owner),
        )
        conn.commit()
