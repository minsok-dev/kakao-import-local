# [변경사유]: Phase2 보완 — undo / manual decide 테스트
"""merge_ops undo · decide."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from kakao_import.db import init_schema
from kakao_import.merge_ops import decide_text_merge, undo_text_merge


def _conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "t.db"
    init_schema(db, reset=True)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # photo_file FK 최소 행
    conn.execute(
        """
        INSERT INTO photo_file (rel_path, file_name, name_parse_ok)
        VALUES ('photos/a.jpg', 'a.jpg', 1)
        """
    )
    return conn


def _insert_merge(
    conn: sqlite3.Connection,
    *,
    sha: str,
    decision: str,
    status: str = "active",
    merged: str | None = "merged body",
    review: int = 0,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO text_merge (
          sha256, mode, decision, review_required, merged_text, merged_norm,
          before_json, after_json, status, decision_source
        ) VALUES (?, 'balanced', ?, ?, ?, ?, ?, ?, ?, 'auto')
        """,
        (
            sha,
            decision,
            review,
            merged,
            merged,
            json.dumps([{"photo_id": 1, "text": "a"}, {"photo_id": 1, "text": "b"}]),
            json.dumps({"decision": decision, "merged_text": merged}),
            status,
        ),
    )
    mid = int(cur.lastrowid)
    conn.execute(
        """
        INSERT INTO import_batch (root_rel, started_at, status)
        VALUES ('t', datetime('now'), 'done')
        """
    )
    batch_id = int(conn.execute("SELECT id FROM import_batch ORDER BY id DESC LIMIT 1").fetchone()["id"])
    if review:
        conn.execute(
            """
            INSERT INTO review_item (batch_id, kind, ref_type, ref_id, reason, status)
            VALUES (?, 'text_merge', 'text_merge', ?, 'review', 'pending')
            """,
            (batch_id, mid),
        )
    return mid


def test_undo_restores_previous(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sha = "a" * 64
    old = _insert_merge(conn, sha=sha, decision="collapse", status="superseded", merged="old")
    new = _insert_merge(conn, sha=sha, decision="merged", status="active", merged="new")
    out = undo_text_merge(conn, merge_id=new)
    conn.commit()
    assert out["undone_id"] == new
    assert out["restored_id"] == old
    row = conn.execute("SELECT status, merged_text FROM text_merge WHERE id=?", (old,)).fetchone()
    assert row["status"] == "active"
    assert row["merged_text"] == "old"
    row2 = conn.execute("SELECT status FROM text_merge WHERE id=?", (new,)).fetchone()
    assert row2["status"] == "superseded"


def test_decide_accept_and_reject(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    sha = "b" * 64
    mid = _insert_merge(
        conn, sha=sha, decision="review", merged="keep me", review=1
    )
    out = decide_text_merge(conn, merge_id=mid, action="accept", note="ok")
    conn.commit()
    assert out["decision"] == "merged"
    row = conn.execute("SELECT decision, decision_source, review_required FROM text_merge WHERE id=?", (mid,)).fetchone()
    assert row["decision"] == "merged"
    assert row["decision_source"] == "manual"
    assert row["review_required"] == 0
    rev = conn.execute(
        "SELECT status FROM review_item WHERE ref_id=? AND kind='text_merge'", (mid,)
    ).fetchone()
    assert rev["status"] == "resolved"

    mid2 = _insert_merge(conn, sha="c" * 64, decision="review", merged="x", review=1)
    decide_text_merge(conn, merge_id=mid2, action="reject")
    conn.commit()
    row2 = conn.execute("SELECT decision FROM text_merge WHERE id=?", (mid2,)).fetchone()
    assert row2["decision"] == "skipped"


def test_decide_set_text(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    mid = _insert_merge(conn, sha="d" * 64, decision="review", merged=None, review=1)
    decide_text_merge(conn, merge_id=mid, action="set-text", text="manual final")
    conn.commit()
    row = conn.execute(
        "SELECT decision, merged_text, decision_source FROM text_merge WHERE id=?",
        (mid,),
    ).fetchone()
    assert row["decision"] == "merged"
    assert row["merged_text"] == "manual final"
    assert row["decision_source"] == "manual"
