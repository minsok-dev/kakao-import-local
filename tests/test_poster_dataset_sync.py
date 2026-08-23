# [변경사유]: human 라벨 → dataset sync 단위 테스트 (원본 불변·반대 폴더 정리)
"""poster-dataset-sync."""

from __future__ import annotations

from pathlib import Path

from kakao_import.config import Settings
from kakao_import.db import connect, init_schema
from kakao_import.poster_const import SOURCE_HUMAN
from kakao_import.poster_dataset_sync import cmd_poster_dataset_sync
from kakao_import.poster_schema import ensure_poster_schema


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


def test_poster_dataset_sync_copies_human_labels(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    room = root / "gangnam" / "photos"
    room.mkdir(parents=True)
    img = room / "KakaoTalk_20260801_120000000.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 100)

    sha = "a" * 64
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    with connect(settings.db_path) as conn:
        ensure_poster_schema(conn)
        pid = conn.execute(
            """
            INSERT INTO photo_file (rel_path, file_name, name_parse_ok, sha256)
            VALUES (?, ?, 1, ?)
            """,
            ("gangnam/photos/KakaoTalk_20260801_120000000.jpg", img.name, sha),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO poster_classify (
              room_id, photo_id, sha256, rel_path, file_name,
              status, source, excluded_from_upload, classified_at
            ) VALUES (?, ?, ?, ?, ?, 'poster', ?, 0, datetime('now'))
            """,
            (
                "gangnam",
                pid,
                sha,
                "gangnam/photos/KakaoTalk_20260801_120000000.jpg",
                img.name,
                SOURCE_HUMAN,
            ),
        )
        conn.commit()

    poster_dir = tmp_path / "dataset" / "poster"
    non_dir = tmp_path / "dataset" / "non_poster"
    out = cmd_poster_dataset_sync(
        settings, poster_dir=poster_dir, non_poster_dir=non_dir
    )
    assert out["ok"] is True
    assert out["copied"] == 1
    assert out["missing"] == 0
    files = list(poster_dir.iterdir())
    assert len(files) == 1
    assert sha[:12] in files[0].name
    # 원본 유지
    assert img.is_file()


def test_poster_dataset_sync_skips_uncertain(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    room = root / "r" / "photos"
    room.mkdir(parents=True)
    img = room / "x.jpg"
    img.write_bytes(b"abc")
    sha = "b" * 64
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    with connect(settings.db_path) as conn:
        ensure_poster_schema(conn)
        pid = conn.execute(
            """
            INSERT INTO photo_file (rel_path, file_name, name_parse_ok, sha256)
            VALUES (?, ?, 1, ?)
            """,
            ("r/photos/x.jpg", "x.jpg", sha),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO poster_classify (
              room_id, photo_id, sha256, rel_path, file_name,
              status, source, excluded_from_upload, classified_at
            ) VALUES (?, ?, ?, ?, ?, 'uncertain', ?, 0, datetime('now'))
            """,
            ("r", pid, sha, "r/photos/x.jpg", "x.jpg", SOURCE_HUMAN),
        )
        conn.commit()

    poster_dir = tmp_path / "ds" / "poster"
    non_dir = tmp_path / "ds" / "non_poster"
    out = cmd_poster_dataset_sync(
        settings, poster_dir=poster_dir, non_poster_dir=non_dir
    )
    assert out["copied"] == 0
    assert not list(poster_dir.iterdir()) if poster_dir.is_dir() else True
