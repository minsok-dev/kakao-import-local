# [변경사유]: 해시 스킵·SHA 단위 exact 갱신이 다른 그룹을 지우지 않는지
"""hash incremental."""

from __future__ import annotations

from pathlib import Path

from kakao_import.config import Settings
from kakao_import.db import connect, init_schema, refresh_exact_groups_for_shas
from kakao_import.pipeline import cmd_hash, cmd_scan


def _settings(tmp_path: Path, root: Path) -> Settings:
    return Settings(
        export_root=root,
        db_path=tmp_path / "t.db",
        log_level="WARNING",
        match_tolerance_seconds=60,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
        merge_mode="balanced",
    )


def test_second_hash_skips_unchanged_file(tmp_path: Path) -> None:
    room = tmp_path / "raw" / "room_a" / "photos"
    room.mkdir(parents=True)
    photo = room / "KakaoTalk_20261020_010000000.jpg"
    photo.write_bytes(b"same-bytes")
    root = tmp_path / "raw"
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path, reset=True)
    cmd_scan(settings, root)
    first = cmd_hash(settings, root)
    assert first["updated"] == 1
    assert first["skipped_unchanged"] == 0
    second = cmd_hash(settings, root)
    assert second["updated"] == 0
    assert second["skipped_unchanged"] == 1
    assert second["exact_groups"] == 1


def test_refresh_one_sha_keeps_other_group(tmp_path: Path) -> None:
    settings = _settings(tmp_path, tmp_path)
    init_schema(settings.db_path, reset=True)
    with connect(settings.db_path) as conn:
        conn.execute(
            """
            INSERT INTO photo_file (
              rel_path, file_name, ext, byte_size, mtime_ns, name_parse_ok, sha256
            ) VALUES
              ('room_a/photos/a.jpg', 'a.jpg', 'jpg', 1, 1, 1, 'aaa'),
              ('room_a/photos/b.jpg', 'b.jpg', 'jpg', 1, 1, 1, 'aaa'),
              ('room_b/photos/c.jpg', 'c.jpg', 'jpg', 1, 1, 1, 'ccc')
            """
        )
        conn.commit()
        refresh_exact_groups_for_shas(conn, {"aaa", "ccc"})
        conn.execute("UPDATE photo_file SET sha256 = 'ddd' WHERE rel_path LIKE '%/b.jpg'")
        totals = refresh_exact_groups_for_shas(conn, {"aaa", "ddd"})
        conn.commit()
        ccc = conn.execute(
            "SELECT member_count FROM exact_sha_group WHERE sha256 = 'ccc'"
        ).fetchone()
        aaa = conn.execute(
            "SELECT member_count FROM exact_sha_group WHERE sha256 = 'aaa'"
        ).fetchone()
        ddd = conn.execute(
            """
            SELECT m.excluded_from_upload
            FROM exact_sha_member m
            JOIN exact_sha_group g ON g.id = m.group_id
            WHERE g.sha256 = 'ddd'
            """
        ).fetchone()
    assert int(ccc["member_count"]) == 1
    assert int(aaa["member_count"]) == 1
    assert int(ddd["excluded_from_upload"]) == 0
    assert totals["exact_groups"] == 3
