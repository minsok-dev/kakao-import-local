# [변경사유]: 단톡방 헤더·채팅분리 구분선 단위 테스트
"""caption_sep room header / chat-split / ADD."""

from __future__ import annotations

from kakao_import.caption_sep import (
    CHAT_SPLIT_SEPARATOR,
    ROOM_ADD_SEPARATOR,
    format_room_header,
    join_texts_by_room,
    room_display_name,
)


def test_room_display_prefers_title() -> None:
    assert room_display_name("강남턴 소식방", "gangnamton_news/chats/a.txt") == "강남턴 소식방"
    assert room_display_name("", "gangnamton_news/chats/a.txt") == "gangnamton_news"
    assert room_display_name(None, None) == "알 수 없는 방"


def test_join_room_change_uses_add_separator() -> None:
    raw = join_texts_by_room(
        [
            {"chat_id": 1, "body_raw": "room77 text"},
            {"chat_id": 2, "body_raw": "room80 text"},
        ]
    )
    assert "room77 text" in raw
    assert "room80 text" in raw
    assert ROOM_ADD_SEPARATOR in raw
    assert CHAT_SPLIT_SEPARATOR not in raw
    assert f"\n\n{ROOM_ADD_SEPARATOR}\n\n" in raw


def test_join_before_after_uses_chat_split_not_add() -> None:
    raw = join_texts_by_room(
        [
            {
                "chat_id": 1,
                "room_title": "강남턴 소식방",
                "body_raw": "앞 설명",
                "position": "before",
            },
            {
                "chat_id": 1,
                "room_title": "강남턴 소식방",
                "body_raw": "뒤 설명",
                "position": "after",
            },
        ]
    )
    assert raw.startswith(format_room_header("강남턴 소식방"))
    assert "앞 설명" in raw and "뒤 설명" in raw
    assert CHAT_SPLIT_SEPARATOR in raw
    assert ROOM_ADD_SEPARATOR not in raw
    assert f"\n\n{CHAT_SPLIT_SEPARATOR}\n\n" in raw


def test_join_includes_sender_and_empty_body_origin() -> None:
    # [변경사유]: 본문이 없어도 단톡방+대화명, 있으면 제목 아래 대화명
    with_body = join_texts_by_room(
        [
            {
                "chat_id": 1,
                "room_title": "정보방",
                "body_raw": "진주 인근이시면 놀러오세요~",
                "sender": "달콩",
            }
        ]
    )
    assert with_body.startswith(format_room_header("정보방"))
    assert "[대화명: 달콩]" in with_body
    assert "진주 인근이시면 놀러오세요~" in with_body

    empty_body = join_texts_by_room(
        [
            {
                "chat_id": 1,
                "room_title": "정보방",
                "body_raw": "",
                "sender": "달콩",
            }
        ]
    )
    assert empty_body == f"{format_room_header('정보방')}\n[대화명: 달콩]"


def test_join_without_room_hint_keeps_plain_text() -> None:
    raw = join_texts_by_room([{"chat_id": 1, "body_raw": "hello"}])
    assert raw == "hello"
