# [변경사유]: non_poster 폴더 분리 — 이동·복귀·덮어쓰기 금지·스캔 제외
"""포스터 파일 위치."""

from __future__ import annotations

from pathlib import Path

from kakao_import.config import Settings
from kakao_import.db import connect, init_schema, prune_missing_photo_files
from kakao_import.pipeline import cmd_scan
from kakao_import.poster_label import cmd_poster_label
from kakao_import.poster_place import place_classified_photo
from kakao_import.poster_schema import ensure_poster_schema


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


def _insert_photo(conn, rel: str, name: str, sha: str) -> int:
    conn.execute(
        "INSERT INTO photo_file (rel_path, file_name, name_parse_ok, sha256) VALUES (?, ?, 1, ?)",
        (rel, name, sha),
    )
    return int(conn.execute("SELECT id FROM photo_file WHERE rel_path = ?", (rel,)).fetchone()["id"])


def test_non_poster_moves_and_poster_returns(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    photos = root / "gangnam" / "photos"
    photos.mkdir(parents=True)
    name = "a.jpg"
    src = photos / name
    src.write_bytes(b"poster-bytes")
    rel = f"gangnam/photos/{name}"
    sha = "ab" * 32
    db = tmp_path / "t.db"
    init_schema(db)
    with connect(db) as conn:
        ensure_poster_schema(conn)
        pid = _insert_photo(conn, rel, name, sha)
        conn.execute(
            """
            INSERT INTO poster_classify (
              room_id, photo_id, sha256, rel_path, file_name, status, source, excluded_from_upload
            ) VALUES ('gangnam', ?, ?, ?, ?, 'non_poster', 'model', 1)
            """,
            (pid, sha, rel, name),
        )
        new_rel = place_classified_photo(conn, root, pid, rel, "non_poster")
        conn.commit()
        assert new_rel == f"gangnam/non_posters/{name}"
        assert not src.exists()
        assert (root / new_rel).read_bytes() == b"poster-bytes"
        row = conn.execute("SELECT rel_path FROM photo_file WHERE id = ?", (pid,)).fetchone()
        assert row["rel_path"] == new_rel
        classify = conn.execute(
            "SELECT rel_path FROM poster_classify WHERE photo_id = ?",
            (pid,),
        ).fetchone()
        assert classify["rel_path"] == new_rel
        kept = prune_missing_photo_files(conn, root)
        assert kept["pruned"] == 0
        back = place_classified_photo(conn, root, pid, new_rel, "uncertain")
        conn.commit()
        assert back == rel
        assert src.read_bytes() == b"poster-bytes"
        assert not (root / new_rel).exists()


def test_dest_exists_does_not_overwrite(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    photos = root / "gangnam" / "photos"
    parked = root / "gangnam" / "non_posters"
    photos.mkdir(parents=True)
    parked.mkdir()
    name = "a.jpg"
    (photos / name).write_bytes(b"src")
    (parked / name).write_bytes(b"keep")
    rel = f"gangnam/photos/{name}"
    db = tmp_path / "t.db"
    init_schema(db)
    with connect(db) as conn:
        pid = _insert_photo(conn, rel, name, "cd" * 32)
        stayed = place_classified_photo(conn, root, pid, rel, "non_poster")
        assert stayed == rel
        assert (photos / name).read_bytes() == b"src"
        assert (parked / name).read_bytes() == b"keep"


def test_legacy_non_poster_folder(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    photos = root / "photos"
    photos.mkdir(parents=True)
    (photos / "a.jpg").write_bytes(b"x")
    db = tmp_path / "t.db"
    init_schema(db)
    with connect(db) as conn:
        pid = _insert_photo(conn, "photos/a.jpg", "a.jpg", "ef" * 32)
        new_rel = place_classified_photo(conn, root, pid, "photos/a.jpg", "non_poster")
        assert new_rel == "non_posters/a.jpg"
        assert (root / "non_posters" / "a.jpg").is_file()


def test_scan_does_not_reindex_non_posters(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    photos = root / "gangnam" / "photos"
    photos.mkdir(parents=True)
    name = "KakaoTalk_20260801_120000000.jpg"
    (photos / name).write_bytes(b"abc")
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    rel = f"gangnam/photos/{name}"
    with connect(settings.db_path) as conn:
        pid = _insert_photo(conn, rel, name, "11" * 32)
        conn.commit()
        new_rel = place_classified_photo(conn, root, pid, rel, "non_poster")
        conn.commit()
    scanned = cmd_scan(settings, root)
    assert scanned["photos"] == 0
    with connect(settings.db_path) as conn:
        rows = conn.execute("SELECT rel_path FROM photo_file").fetchall()
        assert [r["rel_path"] for r in rows] == [new_rel]


def test_human_label_moves_file(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    photos = root / "gangnam" / "photos"
    photos.mkdir(parents=True)
    name = "b.jpg"
    (photos / name).write_bytes(b"human")
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    sha = "22" * 32
    rel = f"gangnam/photos/{name}"
    with connect(settings.db_path) as conn:
        _insert_photo(conn, rel, name, sha)
        conn.commit()
    out = cmd_poster_label(settings, sha256=sha, status="non_poster", room_id="gangnam")
    assert out["ok"] is True
    assert not (photos / name).exists()
    assert (root / "gangnam" / "non_posters" / name).read_bytes() == b"human"
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT rel_path FROM photo_file").fetchone()
        assert row["rel_path"] == f"gangnam/non_posters/{name}"
