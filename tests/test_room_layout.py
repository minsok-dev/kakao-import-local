# [변경사유]: 방별 chats/photos 레이아웃 — 매칭 범위 분리
"""방 폴더 스캔·매칭 격리."""

from __future__ import annotations

from pathlib import Path

from kakao_import.config import Settings
from kakao_import.db import connect, init_schema
from kakao_import.pipeline import (
    LEGACY_ROOM_ID,
    cmd_match,
    cmd_parse,
    cmd_scan,
    iter_room_layouts,
    room_id_from_rel,
)

# 1x1 PNG — 파일명 시각만 매칭에 사용
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc`\x00\x00"
    b"\x00\x02\x00\x01\xe5'\xde\xfc\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_room_id_from_rel() -> None:
    assert room_id_from_rel("gangnam_latin/photos/a.jpg") == "gangnam_latin"
    assert room_id_from_rel("gangnamton_news/chats/x.txt") == "gangnamton_news"
    assert room_id_from_rel("photos/a.jpg") == LEGACY_ROOM_ID
    assert room_id_from_rel("chats/x.txt") == LEGACY_ROOM_ID


def test_iter_room_layouts_new_and_legacy(tmp_path: Path) -> None:
    a_chats = tmp_path / "gangnam_latin" / "chats"
    a_photos = tmp_path / "gangnam_latin" / "photos"
    a_chats.mkdir(parents=True)
    a_photos.mkdir(parents=True)
    (tmp_path / "chats").mkdir()
    (tmp_path / "photos").mkdir()
    layouts = {rid: (c, p) for rid, c, p in iter_room_layouts(tmp_path)}
    assert "gangnam_latin" in layouts
    assert LEGACY_ROOM_ID in layouts
    assert layouts["gangnam_latin"][0] == a_chats
    assert layouts["gangnam_latin"][1] == a_photos


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


def _write_room(
    root: Path, room_id: str, *, photo_name: str, chat_name: str, caption: str
) -> None:
    chats = root / room_id / "chats"
    photos = root / room_id / "photos"
    chats.mkdir(parents=True)
    photos.mkdir(parents=True)
    (photos / photo_name).write_bytes(_TINY_PNG)
    # [변경사유]: 같은 시각·다른 방 — 캡션이 섞이면 실패해야 함
    (chats / chat_name).write_text(
        f"""{room_id} 님과 카카오톡 대화
--------------- 2026년 7월 24일 금요일 ---------------
[A] [오전 12:50] 사진
[A] [오전 12:53] {caption}
""",
        encoding="utf-8",
    )


def test_match_isolates_rooms_same_minute(tmp_path: Path) -> None:
    """같은 분 사진이 두 방에 있어도 캡션은 그 방만."""
    root = tmp_path / "raw"
    fname = "KakaoTalk_20260724_005030533.png"
    _write_room(
        root,
        "gangnam_latin",
        photo_name=fname,
        chat_name="latin.txt",
        caption="LATIN_ONLY",
    )
    _write_room(
        root,
        "gangnamton_news",
        photo_name=fname,
        chat_name="news.txt",
        caption="NEWS_ONLY",
    )
    s = _settings(tmp_path, root)
    init_schema(s.db_path, reset=True)
    cmd_scan(s, root)
    cmd_parse(s, root)
    cmd_match(s, root)
    with connect(s.db_path) as conn:
        rows = conn.execute(
            """
            SELECT p.rel_path, m.body_raw
            FROM photo_file p
            JOIN photo_message_assignment a ON a.photo_id = p.id
            JOIN group_text gt ON gt.group_id = a.group_id
            JOIN parsed_message m ON m.id = gt.message_id
            ORDER BY p.rel_path
            """
        ).fetchall()
    by_rel: dict[str, set[str]] = {}
    for r in rows:
        by_rel.setdefault(r["rel_path"], set()).add(r["body_raw"])
    assert by_rel["gangnam_latin/photos/" + fname] == {"LATIN_ONLY"}
    assert by_rel["gangnamton_news/photos/" + fname] == {"NEWS_ONLY"}
