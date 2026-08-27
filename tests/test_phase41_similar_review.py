# [변경사유]: Phase 4.1 — similar-review UI 핸들러 스모크
"""로컬 리뷰 UI: HTML·API가 decision만 다루는지."""

from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from time import sleep

from kakao_import.config import Settings
from kakao_import.db import connect, init_schema
from kakao_import.similar_detect import ensure_similar_schema, rebuild_similar_groups
from kakao_import.similar_review import _page_html, create_handler
from http.server import ThreadingHTTPServer


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


def test_page_html_mentions_decision_only() -> None:
    html = _page_html()
    assert "decision" in html.lower() or "콘텐츠" in html
    assert "업로드 큐" in html or "upload" in html.lower()
    # [변경사유]: 썸네일 확대 라이트박스 + partial 서브그룹 UI
    assert "lightbox" in html
    assert "data-open-lb" in html
    assert "data-save-partial" in html
    assert "선택 묶기" in html
    # [변경사유]: Poster 분류 리뷰와 동일 — decision 필터
    assert 'id="decision-filter"' in html
    assert 'value="deferred"' in html
    assert 'value="same_content"' in html
    assert "applyFilter" in html
    assert "groupsAll" in html


def test_review_api_decide(tmp_path: Path) -> None:
    root = tmp_path / "raw"
    photos = root / "photos"
    photos.mkdir(parents=True)
    # 최소 JPEG 헤더만으로는 서명 불필요 — DB에 signature 직접 삽입
    (photos / "a.jpg").write_bytes(b"fake-a")
    (photos / "b.jpg").write_bytes(b"fake-b")
    settings = _settings(tmp_path, root)
    init_schema(settings.db_path)
    with connect(settings.db_path) as conn:
        ensure_similar_schema(conn)
        for name, sha in [("photos/a.jpg", "sha_a"), ("photos/b.jpg", "sha_b")]:
            pid = conn.execute(
                "INSERT INTO photo_file (rel_path,file_name,name_parse_ok,sha256) VALUES (?,?,1,?)",
                (name, Path(name).name, sha),
            ).lastrowid
            conn.execute(
                "INSERT INTO photo_signature (photo_id,algo_version,dhash_hex,phash_hex,source_sha256) "
                "VALUES (?,'t','0000','0000',?)",
                (pid, sha),
            )
        rebuild_similar_groups(conn, max_distance=10)
        conn.commit()

    handler = create_handler(settings, root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    sleep(0.05)
    try:
        conn_h = HTTPConnection("127.0.0.1", port, timeout=3)
        conn_h.request("GET", "/api/groups")
        groups = json.loads(conn_h.getresponse().read().decode("utf-8"))
        assert len(groups) == 1
        gid = groups[0]["group_id"]
        body = json.dumps(
            {
                "group_id": gid,
                "decision": "same_content",
                "representative_photo_id": groups[0]["members"][1]["photo_id"],
            }
        ).encode("utf-8")
        conn_h.request(
            "POST",
            "/api/decide",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        out = json.loads(conn_h.getresponse().read().decode("utf-8"))
        assert out["ok"] is True
        assert out["decision"] == "same_content"
        assert out["upload_policy"] == "upload_representative"
        assert "no auto-merge/delete" in out["note"] or "upload queue unchanged" in out["note"]
    finally:
        server.shutdown()
