# [변경사유]: Phase 3.5 — upload gate · file_missing · summary
"""upload 안정화 단위 테스트."""

from __future__ import annotations

from pathlib import Path

from kakao_import.config import Settings
from kakao_import.upload import (
    build_upload_summary,
    classify_upload_requests,
    cmd_upload,
    idle_after_upload_sec,
    matched_messages_nonempty,
    resolve_upload_pace_sec,
    write_upload_result_json,
)


def test_upload_pace_defaults_and_ocr_extra() -> None:
    base, extra = resolve_upload_pace_sec(sleep_sec=5.0, ocr_extra_sec=5.0)
    assert base == 5.0 and extra == 5.0
    assert idle_after_upload_sec(
        base_sleep_sec=5.0, ocr_extra_sec=5.0, response={"next": "sns_appended"}
    ) == 5.0
    assert idle_after_upload_sec(
        base_sleep_sec=5.0, ocr_extra_sec=5.0, response={"next": "ocr_queued"}
    ) == 10.0
    assert idle_after_upload_sec(
        base_sleep_sec=0.0, ocr_extra_sec=5.0, response={"next": "ocr_queued"}
    ) == 5.0


def test_matched_messages_nonempty() -> None:
    assert matched_messages_nonempty({"item": {"matched_messages": []}}) is False
    assert (
        matched_messages_nonempty(
            {"item": {"matched_messages": [{"text": "  "}]}}
        )
        is False
    )
    assert (
        matched_messages_nonempty(
            {"item": {"matched_messages": [{"text": "공연"}]}}
        )
        is True
    )
    # [변경사유]: '사진' 마커만 있으면 EMPTY
    assert (
        matched_messages_nonempty(
            {"item": {"matched_messages": [{"text": "사진"}]}}
        )
        is False
    )


def test_classify_empty_and_missing(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    good = photos / "a.jpg"
    good.write_bytes(b"x")
    requests = [
        {
            "file_rel": "photos/a.jpg",
            "item": {
                "local_item_id": "photo:1",
                "sha256": "a" * 64,
                "matched_messages": [{"text": "hi"}],
            },
        },
        {
            "file_rel": "photos/a.jpg",
            "item": {
                "local_item_id": "photo:2",
                "sha256": "b" * 64,
                "matched_messages": [],
            },
        },
        {
            "file_rel": "photos/gone.jpg",
            "item": {
                "local_item_id": "photo:3",
                "sha256": "c" * 64,
                "matched_messages": [{"text": "x"}],
            },
        },
    ]
    c = classify_upload_requests(requests, root=tmp_path)
    assert c["ready_count"] == 1
    assert c["empty_adjacent_count"] == 1
    assert c["file_missing_count"] == 1


def test_summary_and_utf8_result(tmp_path: Path) -> None:
    classified = {
        "empty_adjacent_count": 2,
        "file_missing_count": 1,
        "ready_count": 3,
    }
    summary = build_upload_summary(
        dry_run=True, classified=classified, allow_empty_caption=False
    )
    assert summary["EMPTY_CONTEXT"] == 2
    assert summary["FILE_MISSING"] == 1
    assert summary["READY"] == 3
    out = tmp_path / "upload-result.json"
    write_upload_result_json(
        out, {"summary": summary, "note": "emoji \U0001fadf ok"}
    )
    raw = out.read_text(encoding="utf-8")
    assert "EMPTY_CONTEXT" in raw
    assert "\U0001fadf" in raw
    assert "SIMILAR_DEFERRED_BLOCKED" in raw


def test_prune_missing(tmp_path: Path) -> None:
    from kakao_import.db import connect, init_schema, prune_missing_photo_files

    db = tmp_path / "t.db"
    init_schema(db)
    root = tmp_path / "raw"
    photos = root / "photos"
    photos.mkdir(parents=True)
    (photos / "ok.jpg").write_bytes(b"1")
    with connect(db) as conn:
        # [변경사유]: batch FK 없이 최소 photo_file 행 (test_phase2_merge_ops 패턴)
        conn.execute(
            """
            INSERT INTO photo_file (rel_path, file_name, name_parse_ok)
            VALUES ('photos/ok.jpg', 'ok.jpg', 1)
            """
        )
        miss_id = conn.execute(
            """
            INSERT INTO photo_file (rel_path, file_name, name_parse_ok, sha256)
            VALUES ('photos/missing.jpg', 'missing.jpg', 1, 'deadbeef')
            """
        ).lastrowid
        # [변경사유]: exact 대표 FK가 prune IntegrityError 유발하던 재현
        eg = conn.execute(
            """
            INSERT INTO exact_sha_group (sha256, representative_photo_id, member_count)
            VALUES ('deadbeef', ?, 1)
            """,
            (miss_id,),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO exact_sha_member
              (group_id, photo_id, is_representative, excluded_from_upload)
            VALUES (?, ?, 1, 0)
            """,
            (eg, miss_id),
        )
        conn.execute(
            """
            INSERT INTO photo_signature
              (photo_id, algo_version, dhash_hex, phash_hex, source_sha256)
            VALUES (?, 't', '00', '00', 'deadbeef')
            """,
            (miss_id,),
        )
        conn.commit()
        r = prune_missing_photo_files(conn, root)
        conn.commit()
        assert r["pruned"] == 1
        left = conn.execute("SELECT COUNT(*) c FROM photo_file").fetchone()["c"]
        assert left == 1
        assert (
            conn.execute(
                "SELECT COUNT(*) c FROM exact_sha_member WHERE photo_id = ?",
                (miss_id,),
            ).fetchone()["c"]
            == 0
        )


def test_cmd_upload_blocks_deferred_similar_groups(tmp_path: Path) -> None:
    from kakao_import.db import connect, init_schema
    from kakao_import.similar_detect import ensure_similar_schema, rebuild_similar_groups

    db = tmp_path / "upload.db"
    init_schema(db)
    root = tmp_path / "raw"
    photos = root / "photos"
    photos.mkdir(parents=True)
    (photos / "a.jpg").write_bytes(b"a")
    (photos / "b.jpg").write_bytes(b"b")
    settings = Settings(
        export_root=root,
        db_path=db,
        log_level="INFO",
        match_tolerance_seconds=60,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
        merge_mode="balanced",
        similar_max_distance=10,
    )
    with connect(db) as conn:
        ensure_similar_schema(conn)
        p1 = conn.execute(
            """
            INSERT INTO photo_file (rel_path, file_name, name_parse_ok, sha256)
            VALUES ('photos/a.jpg', 'a.jpg', 1, ?)
            """,
            ("a" * 64,),
        ).lastrowid
        p2 = conn.execute(
            """
            INSERT INTO photo_file (rel_path, file_name, name_parse_ok, sha256)
            VALUES ('photos/b.jpg', 'b.jpg', 1, ?)
            """,
            ("b" * 64,),
        ).lastrowid
        for pid, sha in ((p1, "a" * 64), (p2, "b" * 64)):
            gid = conn.execute(
                """
                INSERT INTO exact_sha_group (sha256, representative_photo_id, member_count)
                VALUES (?, ?, 1)
                """,
                (sha, pid),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO exact_sha_member (group_id, photo_id, is_representative, excluded_from_upload)
                VALUES (?, ?, 1, 0)
                """,
                (gid, pid),
            )
        conn.execute(
            """
            INSERT INTO photo_signature (photo_id, algo_version, dhash_hex, phash_hex, source_sha256)
            VALUES (?, 't', '0000000000000000', '0000000000000000', ?)
            """,
            (p1, "a" * 64),
        )
        conn.execute(
            """
            INSERT INTO photo_signature (photo_id, algo_version, dhash_hex, phash_hex, source_sha256)
            VALUES (?, 't', '0000000000000001', '0000000000000000', ?)
            """,
            (p2, "b" * 64),
        )
        rebuild_similar_groups(conn, max_distance=10)
        conn.commit()

    dry = cmd_upload(settings, root=root, dry_run=True)
    assert dry["blocked"] is True
    assert dry["SIMILAR_DEFERRED_BLOCKED"] == 1
    assert dry["READY"] == 0

    blocked = cmd_upload(settings, root=root, dry_run=False)
    assert blocked["blocked"] is True
    assert blocked["error"] == "SIMILAR_DEFERRED_BLOCKED"
