# [변경사유]: Phase2 보완 — 되돌리기·수동 결정 (계획서 보존 항목)
"""text_merge undo / manual decide."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal

from kakao_import.logging_util import get_logger
from kakao_import.normalize import normalize_for_compare

log = get_logger(__name__)

DecideAction = Literal["accept", "set-text", "reject"]


def _resolve_review_items(
    conn: sqlite3.Connection, merge_id: int, status: str
) -> int:
    """text_merge 관련 review_item 상태 갱신."""
    cur = conn.execute(
        """
        UPDATE review_item
        SET status = ?
        WHERE kind = 'text_merge' AND ref_type = 'text_merge' AND ref_id = ?
          AND status = 'pending'
        """,
        (status, merge_id),
    )
    return int(cur.rowcount or 0)


def undo_text_merge(
    conn: sqlite3.Connection,
    *,
    merge_id: int | None = None,
    sha256: str | None = None,
) -> dict[str, Any]:
    """
    active merge를 되돌림.
    - 직전 superseded 가 있으면 그것을 다시 active
    - 없으면 active 만 superseded (병합 전 상태로 비움)
    """
    if merge_id is not None:
        row = conn.execute(
            "SELECT id, sha256, status FROM text_merge WHERE id = ?",
            (merge_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"merge_id={merge_id} 없음")
        if row["status"] != "active":
            raise ValueError(f"merge_id={merge_id} 가 active 아님 (status={row['status']})")
        sha = str(row["sha256"])
        active_id = int(row["id"])
    elif sha256:
        sha = sha256.strip().lower()
        row = conn.execute(
            """
            SELECT id, sha256, status FROM text_merge
            WHERE sha256 = ? AND status = 'active'
            ORDER BY id DESC LIMIT 1
            """,
            (sha,),
        ).fetchone()
        if not row:
            raise ValueError(f"sha256={sha[:12]}… active merge 없음")
        active_id = int(row["id"])
    else:
        raise ValueError("merge_id 또는 sha256 필요")

    # 직전 superseded
    prev = conn.execute(
        """
        SELECT id FROM text_merge
        WHERE sha256 = ? AND status = 'superseded' AND id < ?
        ORDER BY id DESC LIMIT 1
        """,
        (sha, active_id),
    ).fetchone()

    conn.execute(
        "UPDATE text_merge SET status = 'superseded' WHERE id = ?",
        (active_id,),
    )
    _resolve_review_items(conn, active_id, "cancelled")

    restored_id: int | None = None
    if prev:
        restored_id = int(prev["id"])
        conn.execute(
            "UPDATE text_merge SET status = 'active' WHERE id = ?",
            (restored_id,),
        )
        log.info(
            "undo merge active=%s restored=%s sha=%s",
            active_id,
            restored_id,
            sha[:12],
        )
    else:
        log.info("undo merge active=%s no_prev sha=%s", active_id, sha[:12])

    return {
        "undone_id": active_id,
        "restored_id": restored_id,
        "sha256": sha,
    }


def decide_text_merge(
    conn: sqlite3.Connection,
    *,
    merge_id: int,
    action: DecideAction,
    text: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """
    review(또는 active) merge에 대한 수동 결정.
    - accept: review→merged (기존 merged_text 사용, 충돌 있어도 관리자 승인)
    - set-text: 사용자가 지정한 텍스트로 merged
    - reject: skipped (업로드 병합 텍스트 미사용)
    """
    row = conn.execute(
        "SELECT * FROM text_merge WHERE id = ?", (merge_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"merge_id={merge_id} 없음")
    if row["status"] != "active":
        raise ValueError(f"merge_id={merge_id} 가 active 아님")

    after: dict[str, Any] = {}
    if row["after_json"]:
        try:
            after = json.loads(row["after_json"]) or {}
        except json.JSONDecodeError:
            after = {}

    if action == "accept":
        merged_text = row["merged_text"]
        if not merged_text:
            # review(safe)에서 merged_text 없을 수 있음 → before 합치기
            try:
                before = json.loads(row["before_json"] or "[]")
            except json.JSONDecodeError:
                before = []
            parts = [str(b.get("text") or "") for b in before if b.get("text")]
            merged_text = "\n".join(dict.fromkeys(parts))  # 순서 유지 중복 제거
        if not merged_text:
            raise ValueError("accept 할 merged_text 가 비어 있음 — set-text 사용")
        decision = "merged"
        review_required = 0
        review_status = "resolved"
    elif action == "set-text":
        if text is None or not str(text).strip():
            raise ValueError("set-text 는 --text 필요")
        merged_text = str(text).strip()
        decision = "merged"
        review_required = 0
        review_status = "resolved"
    elif action == "reject":
        merged_text = None
        decision = "skipped"
        review_required = 0
        review_status = "rejected"
    else:
        raise ValueError(f"unknown action={action}")

    after.update(
        {
            "decision": decision,
            "decision_source": "manual",
            "manual_action": action,
            "merged_text": merged_text,
        }
    )
    if note:
        after["manual_note"] = note

    # decision_source / manual_note 컬럼 존재 여부
    cols = {
        r[1]
        for r in conn.execute("PRAGMA table_info(text_merge)").fetchall()
    }
    if "decision_source" in cols and "manual_note" in cols:
        conn.execute(
            """
            UPDATE text_merge
            SET decision = ?,
                review_required = ?,
                merged_text = ?,
                merged_norm = ?,
                after_json = ?,
                decision_source = 'manual',
                manual_note = ?
            WHERE id = ?
            """,
            (
                decision,
                review_required,
                merged_text,
                normalize_for_compare(merged_text) if merged_text else None,
                json.dumps(after, ensure_ascii=False),
                note,
                merge_id,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE text_merge
            SET decision = ?,
                review_required = ?,
                merged_text = ?,
                merged_norm = ?,
                after_json = ?
            WHERE id = ?
            """,
            (
                decision,
                review_required,
                merged_text,
                normalize_for_compare(merged_text) if merged_text else None,
                json.dumps(after, ensure_ascii=False),
                merge_id,
            ),
        )

    n = _resolve_review_items(conn, merge_id, review_status)
    log.info(
        "decide merge_id=%s action=%s decision=%s reviews=%s",
        merge_id,
        action,
        decision,
        n,
    )
    return {
        "merge_id": merge_id,
        "action": action,
        "decision": decision,
        "review_items_updated": n,
        "merged_text_len": len(merged_text or ""),
    }
