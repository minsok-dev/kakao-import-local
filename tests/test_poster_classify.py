# [변경사유]: 포스터 분류 C0~C2 — torch 없이 스키마·split·판정·upload 필터
"""poster classify unit tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from kakao_import.config import Settings
from kakao_import.db import connect, init_schema
from kakao_import.payload import build_upload_items
from kakao_import.poster_decision import decide_status, excluded_flag, pick_thresholds
from kakao_import.poster_schema import ensure_poster_schema, get_classify_row
from kakao_import.poster_split import LabeledImage, assign_groups, split_train_test
from kakao_import.similar_detect import ensure_similar_schema, rebuild_similar_groups


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        export_root=tmp_path / "raw",
        db_path=tmp_path / "t.db",
        log_level="WARNING",
        match_tolerance_seconds=60,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
        merge_mode="balanced",
    )


def _sample(
    name: str,
    sha: str,
    label: int,
    *,
    t: datetime | None = None,
    ahash: str | None = None,
) -> LabeledImage:
    stem = None
    if name.startswith("KakaoTalk_"):
        from kakao_import.photo_name import kakao_album_stem_and_seq

        stem, _ = kakao_album_stem_and_seq(name)
    return LabeledImage(
        path=Path(name),
        sha256=sha,
        label=label,
        album_stem=stem,
        name_time=t,
        ahash_hex=ahash,
    )


def test_decide_status_band() -> None:
    assert decide_status(0.05, exclude_threshold=0.10, poster_threshold=0.50) == "non_poster"
    assert decide_status(0.20, exclude_threshold=0.10, poster_threshold=0.50) == "uncertain"
    assert decide_status(0.80, exclude_threshold=0.10, poster_threshold=0.50) == "poster"
    assert excluded_flag("non_poster") == 1
    assert excluded_flag("poster") == 0
    assert excluded_flag("uncertain") == 0


def test_pick_thresholds_no_false_exclude() -> None:
    excl, poster_t, meta = pick_thresholds([0.40, 0.90, 0.70])
    assert excl == 0.40
    assert poster_t > excl
    assert meta["min_test_poster_score"] == 0.40
    # 최솟값 자체는 제외되지 않음 (score < threshold)
    assert decide_status(0.40, exclude_threshold=excl, poster_threshold=poster_t) != "non_poster"


def test_split_same_sha_not_both_sets() -> None:
    t0 = datetime(2026, 7, 1)
    t1 = datetime(2026, 8, 1)
    samples = [
        _sample("KakaoTalk_20260701_120000000.jpg", "a" * 64, 1, t=t0),
        _sample("copy.jpg", "a" * 64, 1, t=t0),
        _sample("KakaoTalk_20260801_120000000.jpg", "b" * 64, 0, t=t1),
        _sample("KakaoTalk_20260801_130000000.jpg", "c" * 64, 0, t=t1),
        _sample("KakaoTalk_20260702_120000000.jpg", "d" * 64, 1, t=datetime(2026, 7, 2)),
        _sample("KakaoTalk_20260703_120000000.jpg", "e" * 64, 0, t=datetime(2026, 7, 3)),
    ]
    groups = assign_groups(samples)
    assert groups[0] == groups[1]
    train, test = split_train_test(samples)
    sha_a_train = any(samples[i].sha256 == "a" * 64 for i in train)
    sha_a_test = any(samples[i].sha256 == "a" * 64 for i in test)
    assert not (sha_a_train and sha_a_test)


def test_split_album_stem_together() -> None:
    t = datetime(2026, 8, 4, 16, 19, 21)
    samples = [
        _sample("KakaoTalk_20260804_161921080.png", "1" * 64, 1, t=t),
        _sample("KakaoTalk_20260804_161921080_01.png", "2" * 64, 1, t=t),
        _sample("KakaoTalk_20260701_010101000.jpg", "3" * 64, 0, t=datetime(2026, 7, 1)),
        _sample("KakaoTalk_20260702_010101000.jpg", "4" * 64, 0, t=datetime(2026, 7, 2)),
        _sample("KakaoTalk_20260703_010101000.jpg", "5" * 64, 1, t=datetime(2026, 7, 3)),
    ]
    groups = assign_groups(samples)
    assert groups[0] == groups[1]
    train, test = split_train_test(samples)
    in_train = {0, 1} <= set(train)
    in_test = {0, 1} <= set(test)
    assert in_train ^ in_test


def test_schema_and_human_not_overwritten(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    init_schema(db)
    photos = tmp_path / "raw" / "room1" / "photos"
    photos.mkdir(parents=True)
    img = photos / "KakaoTalk_20260801_120000000.jpg"
    img.write_bytes(b"abc")
    with connect(db) as conn:
        ensure_poster_schema(conn)
        conn.execute(
            "INSERT INTO photo_file (rel_path, file_name, name_parse_ok, sha256) "
            "VALUES (?, ?, 1, ?)",
            ("room1/photos/KakaoTalk_20260801_120000000.jpg", img.name, "ab" * 32),
        )
        pid = int(conn.execute("SELECT id FROM photo_file").fetchone()["id"])
        conn.execute(
            """
            INSERT INTO poster_classify (
              room_id, photo_id, sha256, rel_path, file_name,
              status, source, excluded_from_upload
            ) VALUES ('room1', ?, ?, ?, ?, 'poster', 'human', 0)
            """,
            (pid, "ab" * 32, "room1/photos/" + img.name, img.name),
        )
        conn.commit()
        from kakao_import.poster_classify import _upsert_classify

        action = _upsert_classify(
            conn,
            room_id="room1",
            photo_id=pid,
            sha256="ab" * 32,
            rel_path="room1/photos/" + img.name,
            file_name=img.name,
            model_version="poster-clip-v1",
            poster_score=0.01,
            status="non_poster",
            source="model",
        )
        assert action == "skipped_human"
        row = get_classify_row(conn, "room1", "ab" * 32)
        assert row and row["source"] == "human" and row["status"] == "poster"
        assert img.is_file()


def test_upload_skips_poster_non_poster_not_exact(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    raw = s.export_root
    photos = raw / "r1" / "photos"
    photos.mkdir(parents=True)
    f = photos / "a.jpg"
    f.write_bytes(b"xx")
    init_schema(s.db_path)
    sha = "11" * 32
    with connect(s.db_path) as conn:
        conn.execute(
            "INSERT INTO photo_file (rel_path, file_name, name_parse_ok, sha256, byte_size) "
            "VALUES (?, 'a.jpg', 1, ?, 2)",
            ("r1/photos/a.jpg", sha),
        )
        pid = int(conn.execute("SELECT id FROM photo_file").fetchone()["id"])
        conn.execute(
            "INSERT INTO exact_sha_group (sha256, representative_photo_id, member_count) "
            "VALUES (?, ?, 1)",
            (sha, pid),
        )
        gid = int(conn.execute("SELECT id FROM exact_sha_group").fetchone()["id"])
        conn.execute(
            "INSERT INTO exact_sha_member (group_id, photo_id, is_representative, excluded_from_upload) "
            "VALUES (?, ?, 1, 0)",
            (gid, pid),
        )
        conn.execute(
            """
            INSERT INTO poster_classify (
              room_id, photo_id, sha256, rel_path, file_name,
              status, source, excluded_from_upload
            ) VALUES ('r1', ?, ?, 'r1/photos/a.jpg', 'a.jpg', 'non_poster', 'model', 1)
            """,
            (pid, sha),
        )
        conn.commit()
    items = build_upload_items(s)
    assert items == []


def test_similar_skips_poster_non_poster(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    init_schema(db)
    with connect(db) as conn:
        ensure_similar_schema(conn)
        conn.execute(
            "INSERT INTO photo_file (rel_path, file_name, name_parse_ok, sha256) "
            "VALUES ('r/photos/a.jpg', 'a.jpg', 1, ?)",
            ("aa" * 32,),
        )
        conn.execute(
            "INSERT INTO photo_file (rel_path, file_name, name_parse_ok, sha256) "
            "VALUES ('r/photos/b.jpg', 'b.jpg', 1, ?)",
            ("bb" * 32,),
        )
        p1 = int(conn.execute("SELECT id FROM photo_file WHERE file_name='a.jpg'").fetchone()["id"])
        p2 = int(conn.execute("SELECT id FROM photo_file WHERE file_name='b.jpg'").fetchone()["id"])
        conn.execute(
            "INSERT INTO photo_signature (photo_id, algo_version, dhash_hex, phash_hex, source_sha256) "
            "VALUES (?, 't', '0000000000000000', '0000000000000000', ?)",
            (p1, "aa" * 32),
        )
        conn.execute(
            "INSERT INTO photo_signature (photo_id, algo_version, dhash_hex, phash_hex, source_sha256) "
            "VALUES (?, 't', '0000000000000001', '0000000000000000', ?)",
            (p2, "bb" * 32),
        )
        conn.execute(
            """
            INSERT INTO poster_classify (
              room_id, photo_id, sha256, rel_path, file_name,
              status, source, excluded_from_upload
            ) VALUES ('r', ?, ?, 'r/photos/a.jpg', 'a.jpg', 'non_poster', 'model', 1)
            """,
            (p1, "aa" * 32),
        )
        stats = rebuild_similar_groups(conn, max_distance=10)
        assert stats["skipped_poster"] == 1
        assert stats["groups"] == 0
        conn.commit()


def test_classify_noop_without_active(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    init_schema(s.db_path)
    from kakao_import.poster_classify import cmd_poster_classify

    out = cmd_poster_classify(s, tmp_path, models_dir=tmp_path / "models")
    assert out.get("ok") is True
    assert out.get("skipped") in ("no_active_model", "no_extra")
