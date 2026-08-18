# [변경사유]: poster-review UI 핸들러 스모크 — 목록 조회 + human 라벨 저장
"""로컬 포스터 리뷰 UI: HTML·API가 분류 조회와 라벨 저장을 다루는지."""

from __future__ import annotations

import json
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
        conn_h = HTTPConnection("127.0.0.1", port, timeout=3)
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
    finally:
        server.shutdown()
