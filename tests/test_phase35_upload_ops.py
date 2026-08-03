# [변경사유]: Phase 3.5 — upload gate · file_missing · summary
"""upload 안정화 단위 테스트."""

from __future__ import annotations

from pathlib import Path

from kakao_import.upload import (
    build_upload_summary,
    classify_upload_requests,
    matched_messages_nonempty,
    write_upload_result_json,
)


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
        conn.execute(
            """
            INSERT INTO photo_file (rel_path, file_name, name_parse_ok)
            VALUES ('photos/missing.jpg', 'missing.jpg', 1)
            """
        )
        conn.commit()
        r = prune_missing_photo_files(conn, root)
        conn.commit()
        assert r["pruned"] == 1
        left = conn.execute("SELECT COUNT(*) c FROM photo_file").fetchone()["c"]
        assert left == 1
