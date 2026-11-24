# [변경사유]: poster-review UI 핸들러 스모크 — 목록 조회 + human 라벨 저장
"""로컬 포스터 리뷰 UI: HTML·API가 분류 조회와 라벨 저장을 다루는지."""

from __future__ import annotations

import json

import pytest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import sleep

from kakao_import.config import Settings
from kakao_import.db import connect, init_schema
from kakao_import.poster_review import _page_html, create_handler
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


def test_page_html_mentions_human_label() -> None:
    html = _page_html()
    assert "Poster 분류 리뷰" in html
    assert "human" in html.lower()
    assert "non_poster" in html
    # [변경사유]: 목록은 썸네일·lazy·20장 페이지. 저장 후 전체 load() 금지
    assert 'src="/thumb/${item.photo_id}"' in html
    assert 'loading="lazy"' in html
    assert "PAGE_SIZE = 20" in html
    assert "if (out.ok) await load()" not in html
    assert 'src = "/photo/" + pid' in html


def test_review_api_items_and_label(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    room = root / "info_latin_korea" / "photos"
    room.mkdir(parents=True)
    image_path = room / "KakaoTalk_20260818_010203000.jpg"
    image_path.write_bytes(b"fake-image")
    sha = "a" * 64
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    with connect(settings.db_path) as conn:
        ensure_poster_schema(conn)
        pid = conn.execute(
            "INSERT INTO photo_file (rel_path,file_name,name_parse_ok,sha256) VALUES (?,?,1,?)",
            ("info_latin_korea/photos/" + image_path.name, image_path.name, sha),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO poster_classify (
              room_id, photo_id, sha256, rel_path, file_name,
              model_version, poster_score, status, source, excluded_from_upload, classified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                "info_latin_korea",
                pid,
                sha,
                "info_latin_korea/photos/" + image_path.name,
                image_path.name,
                "poster-clip-v1",
                0.1234,
                "non_poster",
                "model",
                1,
            ),
        )
        conn.commit()

    handler = create_handler(settings, root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    sleep(0.05)
    try:
        conn_h = HTTPConnection("127.0.0.1", port, timeout=8)
        conn_h.request("GET", "/api/items")
        payload = json.loads(conn_h.getresponse().read().decode("utf-8"))
        assert payload["ok"] is True
        assert payload["summary"]["non_poster"] == 1
        assert payload["summary"]["excluded"] == 1
        item = payload["items"][0]
        assert item["room_id"] == "info_latin_korea"
        assert item["status"] == "non_poster"

        body = json.dumps(
            {
                "room_id": "info_latin_korea",
                "sha256": sha,
                "status": "poster",
            }
        ).encode("utf-8")
        conn_h.request(
            "POST",
            "/api/label",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        out = json.loads(conn_h.getresponse().read().decode("utf-8"))
        assert out["ok"] is True

        with connect(settings.db_path) as conn:
            row = conn.execute(
                """
                SELECT status, source, excluded_from_upload
                FROM poster_classify
                WHERE room_id = ? AND lower(sha256) = ?
                """,
                ("info_latin_korea", sha),
            ).fetchone()
        assert row is not None
        assert row["status"] == "poster"
        assert row["source"] == "human"
        assert int(row["excluded_from_upload"]) == 0
        assert payload.get("has_more") is False
        assert payload.get("limit") == 20
        conn_h.request("GET", f"/thumb/{pid}")
        thumb_res = conn_h.getresponse()
        assert thumb_res.status == 200
        thumb_res.read()
        conn_h.request("GET", f"/photo/{pid}")
        photo_res = conn_h.getresponse()
        assert photo_res.status == 200
        photo_res.read()
    finally:
        server.shutdown()
        close_fn = getattr(handler, "close_resources", None)
        if callable(close_fn):
            close_fn()


def test_ensure_poster_schema_skips_ddl_while_reader_open(tmp_path: Path) -> None:
    """스키마가 있으면 읽기 트랜잭션 중에도 executescript 로 잠금이 나지 않는다."""
    root = tmp_path / "raw"
    root.mkdir()
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)

    reader = connect(settings.db_path)
    # [변경사유]: 암시적 BEGIN 과 겹치지 않게 읽기 트랜잭션을 직접 연다
    reader.isolation_level = None
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM poster_classify").fetchone()
    writer = connect(settings.db_path)
    try:
        writer.execute("PRAGMA busy_timeout = 300")
        ensure_poster_schema(writer)
        writer.execute(
            """
            INSERT INTO poster_classify (
              room_id, sha256, rel_path, file_name, status, source
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("room", "b" * 64, "room/photos/a.jpg", "a.jpg", "uncertain", "human"),
        )
        writer.commit()
        left = writer.execute(
            "SELECT COUNT(*) AS n FROM poster_classify WHERE room_id = ?",
            ("room",),
        ).fetchone()
        assert int(left["n"]) == 1
    finally:
        writer.close()
        reader.execute("ROLLBACK")
        reader.close()


def test_review_api_paginates_twenty(tmp_path: Path) -> None:
    """한 요청에 20장만 주고 has_more 로 나머지를 이어서 받는다."""
    root = tmp_path / "raw"
    photos = root / "info_latin_korea" / "photos"
    photos.mkdir(parents=True)
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    with connect(settings.db_path) as conn:
        ensure_poster_schema(conn)
        for i in range(21):
            name = f"p{i:02d}.jpg"
            (photos / name).write_bytes(b"fake-image")
            sha = f"{i:064x}"
            pid = conn.execute(
                "INSERT INTO photo_file (rel_path,file_name,name_parse_ok,sha256) VALUES (?,?,1,?)",
                (f"info_latin_korea/photos/{name}", name, sha),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO poster_classify (
                  room_id, photo_id, sha256, rel_path, file_name,
                  model_version, poster_score, status, source, excluded_from_upload, classified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    "info_latin_korea",
                    pid,
                    sha,
                    f"info_latin_korea/photos/{name}",
                    name,
                    "poster-clip-v1",
                    0.1,
                    "uncertain",
                    "model",
                    0,
                ),
            )
        conn.commit()

    handler = create_handler(settings, root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    sleep(0.05)
    try:
        conn_h = HTTPConnection("127.0.0.1", port, timeout=8)
        conn_h.request("GET", "/api/items?status=uncertain&limit=20&offset=0")
        first = json.loads(conn_h.getresponse().read().decode("utf-8"))
        assert first["ok"] is True
        assert first["summary"]["total"] == 21
        assert first["summary"]["uncertain"] == 21
        assert len(first["items"]) == 20
        assert first["has_more"] is True
        conn_h.request("GET", "/api/items?status=uncertain&limit=20&offset=20")
        second = json.loads(conn_h.getresponse().read().decode("utf-8"))
        assert len(second["items"]) == 1
        assert second["has_more"] is False
        assert second["summary"]["total"] == 21
    finally:
        server.shutdown()
        close_fn = getattr(handler, "close_resources", None)
        if callable(close_fn):
            close_fn()


def test_review_thumb_is_smaller_than_original(tmp_path: Path) -> None:
    """목록 /thumb 는 긴 변 400px JPEG. 라이트박스 /photo 는 원본."""
    from io import BytesIO

    pytest.importorskip("PIL")
    from PIL import Image

    root = tmp_path / "raw"
    photos = root / "info_latin_korea" / "photos"
    photos.mkdir(parents=True)
    image_path = photos / "big.jpg"
    src = Image.new("RGB", (1200, 800), (12, 80, 40))
    src.save(image_path, "JPEG", quality=95)
    orig_size = image_path.stat().st_size
    sha = "c" * 64
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    with connect(settings.db_path) as conn:
        ensure_poster_schema(conn)
        pid = conn.execute(
            "INSERT INTO photo_file (rel_path,file_name,name_parse_ok,sha256) VALUES (?,?,1,?)",
            ("info_latin_korea/photos/big.jpg", "big.jpg", sha),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO poster_classify (
              room_id, photo_id, sha256, rel_path, file_name,
              model_version, poster_score, status, source, excluded_from_upload, classified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                "info_latin_korea",
                pid,
                sha,
                "info_latin_korea/photos/big.jpg",
                "big.jpg",
                "poster-clip-v1",
                0.2,
                "uncertain",
                "model",
                0,
            ),
        )
        conn.commit()

    handler = create_handler(settings, root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    sleep(0.05)
    try:
        conn_h = HTTPConnection("127.0.0.1", port, timeout=8)
        conn_h.request("GET", f"/thumb/{pid}")
        thumb_res = conn_h.getresponse()
        assert thumb_res.status == 200
        assert "jpeg" in (thumb_res.getheader("Content-Type") or "").lower()
        thumb_bytes = thumb_res.read()
        assert len(thumb_bytes) < orig_size
        with Image.open(BytesIO(thumb_bytes)) as im:
            assert max(im.size) <= 400
        conn_h.request("GET", f"/photo/{pid}")
        photo_res = conn_h.getresponse()
        photo_bytes = photo_res.read()
        assert photo_res.status == 200
        assert len(photo_bytes) == orig_size
    finally:
        server.shutdown()
        close_fn = getattr(handler, "close_resources", None)
        if callable(close_fn):
            close_fn()
