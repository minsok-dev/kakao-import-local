# [변경사유]: 증분 업로드 상태 테이블/재평가 트리거/hold-report 검증
"""upload candidate state helpers."""

from __future__ import annotations

from pathlib import Path

from kakao_import.config import Settings
from kakao_import.db import connect, init_schema
from kakao_import.ledger import record_uploaded_sha
from kakao_import.pipeline import cmd_hold_report, cmd_similar_decide
from kakao_import.poster_label import cmd_poster_label
from kakao_import.similar_detect import ensure_similar_schema, rebuild_similar_groups
from kakao_import.upload_state import (
    acquire_run_lock,
    apply_candidate_classification,
    load_incremental_caption_plan,
    mark_candidates_needs_rebuild_by_sha,
    mark_candidate_retry,
    mark_candidate_uploaded,
    record_similar_policy_skips,
    release_run_lock,
    sync_upload_candidates,
    uploadable_candidate_keys,
)


def _settings(tmp: Path, root: Path) -> Settings:
    return Settings(
        export_root=root,
        db_path=tmp / "t.db",
        log_level="WARNING",
        match_tolerance_seconds=120,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
        merge_mode="balanced",
    )


def test_init_schema_creates_upload_candidate_tables(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    init_schema(db)
    with connect(db) as conn:
        tables = {
            str(r["name"])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "upload_candidate" in tables
    assert "upload_candidate_member" in tables
    assert "upload_run_lock" in tables


def test_mark_rebuild_by_sha_and_hold_report(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    root.mkdir()
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    sha = "a" * 64
    with connect(settings.db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO upload_candidate (
              candidate_key, media_fingerprint, state, similar_decision
            ) VALUES (?, ?, ?, ?)
            """,
            ("cand:1", sha, "hold_poster_uncertain", "deferred"),
        )
        candidate_id = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO upload_candidate_member (
              candidate_id, photo_id, sha256, member_role, sort_order, rel_path
            ) VALUES (?, ?, ?, 'main', 0, ?)
            """,
            (candidate_id, 101, sha, "photos/a.jpg"),
        )
        conn.commit()
    updated = mark_candidates_needs_rebuild_by_sha(
        settings.db_path,
        sha256=sha,
        reason="poster_label:poster",
    )
    assert updated == 1
    with connect(settings.db_path) as conn:
        row = conn.execute(
            "SELECT needs_rebuild, state, state_reason FROM upload_candidate WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        assert int(row["needs_rebuild"]) == 1
        assert row["state"] == "new"
        assert row["state_reason"] == "poster_label:poster"
    report = cmd_hold_report(settings)
    assert report["candidate_tables_exist"] == 1
    assert report["counts"]["hold_poster_uncertain"] == 0


def test_similar_decide_and_poster_label_mark_rebuild(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    photos = root / "photos"
    photos.mkdir(parents=True)
    (photos / "a.jpg").write_bytes(b"fake-a")
    (photos / "b.jpg").write_bytes(b"fake-b")
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    with connect(settings.db_path) as conn:
        ensure_similar_schema(conn)
        pids: list[int] = []
        for idx, (name, sha) in enumerate(
            [("photos/a.jpg", "a" * 64), ("photos/b.jpg", "b" * 64)],
            start=1,
        ):
            pid = conn.execute(
                "INSERT INTO photo_file (rel_path,file_name,name_parse_ok,sha256) VALUES (?,?,1,?)",
                (name, Path(name).name, sha),
            ).lastrowid
            pids.append(int(pid))
            conn.execute(
                "INSERT INTO photo_signature (photo_id,algo_version,dhash_hex,phash_hex,source_sha256) "
                "VALUES (?,'t','0000','0000',?)",
                (pid, sha),
            )
        rebuild_similar_groups(conn, max_distance=10)
        group_row = conn.execute(
            "SELECT id FROM similar_image_group ORDER BY id LIMIT 1"
        ).fetchone()
        gid = int(group_row["id"])
        cur = conn.execute(
            """
            INSERT INTO upload_candidate (
              candidate_key, media_fingerprint, state, similar_group_id, similar_decision
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("cand:group", "grp" * 21 + "g", "hold_similar_deferred", gid, "deferred"),
        )
        candidate_id = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO upload_candidate_member (
              candidate_id, photo_id, sha256, member_role, sort_order, rel_path
            ) VALUES (?, ?, ?, 'main', 0, ?)
            """,
            (candidate_id, pids[0], "a" * 64, "photos/a.jpg"),
        )
        conn.commit()
    out = cmd_similar_decide(
        settings,
        group_id=gid,
        decision="same_content",
        representative_photo_id=pids[0],
    )
    assert out["ok"] is True
    assert out["rebuild_marked"] == 1
    with connect(settings.db_path) as conn:
        row = conn.execute(
            "SELECT needs_rebuild, state_reason FROM upload_candidate WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        assert int(row["needs_rebuild"]) == 1
        assert row["state_reason"] == "similar_decide:same_content"
    poster_out = cmd_poster_label(
        settings,
        sha256="a" * 64,
        status="poster",
    )
    assert poster_out["ok"] is True
    assert poster_out["rebuild_marked"] >= 1


def test_sync_upload_candidates_skips_uploaded_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    root.mkdir()
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    source_sha = "a" * 64
    media_fp = "b" * 64
    caption_fp = "c" * 64
    record_uploaded_sha(
        settings.db_path,
        source_sha256=source_sha,
        request_idx=1,
        next_val="ocr_idle",
        media_fingerprint=media_fp,
        caption_fingerprint=caption_fp,
    )
    request = {
        "candidate_key": f"media:{media_fp}",
        "media_fingerprint": media_fp,
        "caption_fingerprint": caption_fp,
        "file_rel": "photos/a.jpg",
        "item": {
            "local_item_id": "photo:1:test",
            "sha256": source_sha,
            "rel_path": "photos/a.jpg",
            "matched_messages": [{"text": "caption-a"}],
            "sub_images": [],
        },
    }
    out = sync_upload_candidates(settings.db_path, requests=[request])
    assert out["candidate_tables_exist"] == 1
    assert out["skipped_uploaded_count"] == 1
    assert out["actionable_count"] == 0
    with connect(settings.db_path) as conn:
        row = conn.execute(
            "SELECT state, state_reason FROM upload_candidate WHERE candidate_key = ?",
            (f"media:{media_fp}",),
        ).fetchone()
        assert row["state"] == "uploaded"
        assert row["state_reason"] == "ledger_match"


def test_sync_skips_when_caption_changes_but_media_on_ledger(tmp_path: Path) -> None:
    """[변경사유]: I1 — 캡션 지문만 달라도 동일 미디어면 HTTP 재전송 안 함."""
    root = tmp_path / "raw"
    root.mkdir()
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    media_fp = "1" * 64
    record_uploaded_sha(
        settings.db_path,
        source_sha256=media_fp,
        request_idx=10,
        next_val="sns_appended",
        media_fingerprint=media_fp,
        caption_fingerprint="2" * 64,
    )
    # 1차 sync — uploaded
    sync_upload_candidates(
        settings.db_path,
        requests=[
            {
                "candidate_key": f"media:{media_fp}",
                "media_fingerprint": media_fp,
                "caption_fingerprint": "2" * 64,
                "file_rel": "photos/x.jpg",
                "item": {
                    "sha256": media_fp,
                    "matched_messages": [{"text": "old"}],
                    "sub_images": [],
                },
            }
        ],
    )
    # 2차 — 캡션만 변경
    out = sync_upload_candidates(
        settings.db_path,
        requests=[
            {
                "candidate_key": f"media:{media_fp}",
                "media_fingerprint": media_fp,
                "caption_fingerprint": "3" * 64,
                "file_rel": "photos/x.jpg",
                "item": {
                    "sha256": media_fp,
                    "matched_messages": [{"text": "new caption"}],
                    "sub_images": [],
                },
            }
        ],
    )
    assert out["actionable_count"] == 0
    assert out["skipped_uploaded_count"] == 1
    with connect(settings.db_path) as conn:
        row = conn.execute(
            "SELECT state, state_reason FROM upload_candidate WHERE candidate_key = ?",
            (f"media:{media_fp}",),
        ).fetchone()
    assert row["state"] == "uploaded"
    assert row["state_reason"] in ("ledger_media_match", "keep_state:uploaded", "ledger_match")


def test_caption_plan_skips_ledger_source_sha(tmp_path: Path) -> None:
    """장부 source_sha256 은 skip_shas 에 들어가 caption 재계산을 생략한다."""
    root = tmp_path / "raw"
    root.mkdir()
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    sha = "9" * 64
    record_uploaded_sha(
        settings.db_path,
        source_sha256=sha,
        request_idx=1,
        next_val="ocr_queued",
        media_fingerprint=sha,
        caption_fingerprint="8" * 64,
    )
    plan = load_incremental_caption_plan(settings.db_path)
    assert sha in plan["skip_shas"]


def test_acquire_run_lock_blocks_second_owner(tmp_path: Path) -> None:
    """[변경사유]: I1 — upload_run_lock 중복 실행 차단."""
    db = tmp_path / "lock.db"
    init_schema(db)
    first = acquire_run_lock(db, lock_name="upload", owner="owner-a", ttl_sec=600)
    assert first["acquired"] is True
    second = acquire_run_lock(db, lock_name="upload", owner="owner-b", ttl_sec=600)
    assert second["acquired"] is False
    assert second.get("blocked_by") == "owner-a"
    release_run_lock(db, lock_name="upload", owner="owner-a")
    third = acquire_run_lock(db, lock_name="upload", owner="owner-b", ttl_sec=600)
    assert third["acquired"] is True
    release_run_lock(db, lock_name="upload", owner="owner-b")


def test_apply_candidate_classification_marks_missing_and_poster_states(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    root.mkdir()
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    sha = "d" * 64
    req = {
        "candidate_key": f"media:{sha}",
        "media_fingerprint": sha,
        "caption_fingerprint": "e" * 64,
        "file_rel": "photos/d.jpg",
        "item": {
            "local_item_id": "photo:9:test",
            "sha256": sha,
            "rel_path": "photos/d.jpg",
            "matched_messages": [{"text": "caption-d"}],
            "sub_images": [],
        },
    }
    sync_upload_candidates(settings.db_path, requests=[req])
    with connect(settings.db_path) as conn:
        conn.execute(
            "INSERT INTO photo_file (rel_path, file_name, name_parse_ok, sha256) VALUES (?, ?, 1, ?)",
            ("photos/d.jpg", "d.jpg", sha),
        )
        conn.commit()
    cmd_poster_label(settings, sha256=sha, status="non_poster")
    classified = {
        "ready": [{"candidate_key": f"media:{sha}"}],
        "empty_adjacent": [],
        "file_missing": [],
    }
    out = apply_candidate_classification(settings.db_path, classified=classified)
    assert out["excluded_non_poster"] == 1
    with connect(settings.db_path) as conn:
        row = conn.execute(
            "SELECT state FROM upload_candidate WHERE candidate_key = ?",
            (f"media:{sha}",),
        ).fetchone()
        assert row["state"] == "excluded_non_poster"

    missing_sha = "f" * 64
    sync_upload_candidates(
        settings.db_path,
        requests=[
            {
                "candidate_key": f"media:{missing_sha}",
                "media_fingerprint": missing_sha,
                "caption_fingerprint": "1" * 64,
                "file_rel": "photos/missing.jpg",
                "item": {
                    "local_item_id": "photo:10:test",
                    "sha256": missing_sha,
                    "rel_path": "photos/missing.jpg",
                    "matched_messages": [{"text": "caption-missing"}],
                    "sub_images": [],
                },
            }
        ],
    )
    missing_out = apply_candidate_classification(
        settings.db_path,
        classified={
            "ready": [],
            "empty_adjacent": [],
            "file_missing": [{"candidate_key": f"media:{missing_sha}"}],
        },
    )
    assert missing_out["hold_missing_file"] == 1


def test_retry_and_uploaded_transitions(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    root.mkdir()
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    sha = "9" * 64
    key = f"media:{sha}"
    sync_upload_candidates(
        settings.db_path,
        requests=[
            {
                "candidate_key": key,
                "media_fingerprint": sha,
                "caption_fingerprint": "8" * 64,
                "file_rel": "photos/r.jpg",
                "item": {
                    "local_item_id": "photo:11:test",
                    "sha256": sha,
                    "rel_path": "photos/r.jpg",
                    "matched_messages": [{"text": "caption-r"}],
                    "sub_images": [],
                },
            }
        ],
    )
    retry = mark_candidate_retry(
        settings.db_path,
        candidate_key=key,
        error_code="TimeoutError",
        error_message="temporary",
    )
    assert retry["state"] == "retry_wait"
    allowed = uploadable_candidate_keys(settings.db_path, candidate_keys=[key])
    assert key not in allowed
    mark_candidate_uploaded(
        settings.db_path,
        candidate_key=key,
        request_idx=123,
        ocr_idx=456,
    )
    with connect(settings.db_path) as conn:
        row = conn.execute(
            "SELECT state, server_receipt_id, server_ocr_id FROM upload_candidate WHERE candidate_key = ?",
            (key,),
        ).fetchone()
        assert row["state"] == "uploaded"
        assert row["server_receipt_id"] == "123"
        assert row["server_ocr_id"] == 456


def test_incremental_caption_plan_skips_uploaded_and_caches(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    root.mkdir()
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    uploaded_sha = "1" * 64
    cached_sha = "2" * 64
    with connect(settings.db_path) as conn:
        for sha, state, caption in (
            (uploaded_sha, "uploaded", "old"),
            (cached_sha, "new", "cached-caption"),
        ):
            cur = conn.execute(
                """
                INSERT INTO upload_candidate (
                  candidate_key, media_fingerprint, state, caption_text_cached, needs_rebuild
                ) VALUES (?, ?, ?, ?, 0)
                """,
                (f"media:{sha}", sha, state, caption),
            )
            cid = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO upload_candidate_member (
                  candidate_id, photo_id, sha256, member_role, sort_order
                ) VALUES (?, ?, ?, 'main', 0)
                """,
                (cid, abs(hash(sha)) % 100000, sha),
            )
        conn.commit()
    plan = load_incremental_caption_plan(settings.db_path)
    assert uploaded_sha in plan["skip_shas"]
    assert cached_sha not in plan["skip_shas"]
    assert plan["caption_cache"][cached_sha] == "cached-caption"


def test_record_similar_policy_skips_persists_hold(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    root.mkdir()
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    sha = "3" * 64
    with connect(settings.db_path) as conn:
        pid = conn.execute(
            "INSERT INTO photo_file (rel_path,file_name,name_parse_ok,sha256) VALUES (?,?,1,?)",
            ("photos/x.jpg", "x.jpg", sha),
        ).lastrowid
        conn.commit()
    out = record_similar_policy_skips(
        settings.db_path,
        skipped=[
            {
                "photo_id": int(pid),
                "rel": "photos/x.jpg",
                "reason": "similar_deferred",
                "group_id": 9,
                "decision": "deferred",
            }
        ],
        deferred_groups=[{"group_id": 9}],
    )
    assert out["hold_similar_deferred"] == 1
    plan = load_incremental_caption_plan(settings.db_path)
    assert sha in plan["skip_shas"]
