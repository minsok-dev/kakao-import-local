# [변경사유]: 여러 방 합본이 4,000자를 넘으면 업로드 칸을 나눈다
"""ingress matched_messages split."""

from __future__ import annotations

from kakao_import.caption_sep import CHAT_SPLIT_SEPARATOR, ROOM_ADD_SEPARATOR
from kakao_import.payload import (
    MAX_INGRESS_MESSAGE_CHARS,
    matched_messages_from_caption,
)


def _join_rooms(*blocks: str) -> str:
    sep = f"\n\n{ROOM_ADD_SEPARATOR}\n\n"
    return sep.join(blocks)


def test_short_caption_stays_one_message() -> None:
    text = _join_rooms("강남 짧은 글", "경기 짧은 글")
    msgs = matched_messages_from_caption(text)
    assert len(msgs) == 1
    assert msgs[0]["text"] == text
    assert ROOM_ADD_SEPARATOR in msgs[0]["text"]


def test_multi_room_over_limit_splits_by_room() -> None:
    block = "화차타 안내 " * 200
    assert len(block) < MAX_INGRESS_MESSAGE_CHARS
    text = _join_rooms(block, block + "b", block + "c", block + "d")
    assert len(text) > MAX_INGRESS_MESSAGE_CHARS
    msgs = matched_messages_from_caption(text)
    assert len(msgs) == 4
    assert all(len(m["text"]) <= MAX_INGRESS_MESSAGE_CHARS for m in msgs)
    assert all(ROOM_ADD_SEPARATOR not in m["text"] for m in msgs)
    assert msgs[0]["text"] == block.strip()
    assert sum(len(m["text"]) for m in msgs) <= 16_000


def test_one_room_over_limit_splits_on_chat_separator() -> None:
    before = "앞" * 2_500
    after = "뒤" * 2_500
    text = f"{before}\n\n{CHAT_SPLIT_SEPARATOR}\n\n{after}"
    assert len(text) > MAX_INGRESS_MESSAGE_CHARS
    msgs = matched_messages_from_caption(text)
    assert len(msgs) == 2
    assert all(len(m["text"]) <= MAX_INGRESS_MESSAGE_CHARS for m in msgs)
    assert CHAT_SPLIT_SEPARATOR not in msgs[0]["text"]
    assert msgs[0]["text"].startswith("앞")
    assert msgs[1]["text"].startswith("뒤")


def test_no_separator_is_sliced_by_chars() -> None:
    text = "가" * (MAX_INGRESS_MESSAGE_CHARS + 50)
    msgs = matched_messages_from_caption(text)
    assert [len(m["text"]) for m in msgs] == [MAX_INGRESS_MESSAGE_CHARS, 50]
    assert "".join(m["text"] for m in msgs) == text


def test_emoji_uses_utf16_length_like_the_server() -> None:
    """이모지는 Python 1자 · 서버 2자. 서버 4,000을 넘기면 칸을 나눈다."""
    # [변경사유]: 2026-11-08 MESSAGE_TOO_LONG — py 3906 / JS 4048
    text = ("가" * 3900) + ("🙂" * 80)
    assert len(text) <= MAX_INGRESS_MESSAGE_CHARS
    msgs = matched_messages_from_caption(text)
    assert len(msgs) >= 2
    for m in msgs:
        units = len(m["text"].encode("utf-16-le")) // 2
        assert units <= MAX_INGRESS_MESSAGE_CHARS
    assert "".join(m["text"] for m in msgs) == text
