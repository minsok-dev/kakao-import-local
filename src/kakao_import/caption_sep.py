# [변경사유]: Phase 4.2+ multi_room 캡션 — 방 사이 ADD 구분선
"""캡션 합치기 시 방 구분."""

from __future__ import annotations

from typing import Any

# [변경사유]: 여러 방 텍스트 union 시 운영자가 구분할 수 있게
ROOM_ADD_SEPARATOR = "<------------- ADD 구분선 ------------>"


def join_texts_by_room(
    parts: list[dict[str, Any]],
    *,
    text_key: str = "body_raw",
    room_key: str = "chat_id",
) -> str:
    """
    같은 chat_id 연속 구간은 \\n 으로, 방이 바뀌면 구분선(+위아래 빈 줄)으로 연결.
    chat_id 없으면 기존처럼 \\n 조인.
    """
    blocks: list[str] = []
    current_room: Any = object()
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        chunk = "\n".join(x for x in buf if x.strip()).strip()
        if chunk:
            blocks.append(chunk)
        buf = []

    for p in parts:
        raw = str(p.get(text_key) or "").strip()
        if not raw:
            continue
        room = p.get(room_key)
        if room is None:
            if current_room is not object() and current_room is not None:
                flush()
            current_room = None
            buf.append(raw)
            continue
        if current_room is object():
            current_room = room
            buf.append(raw)
            continue
        if room != current_room:
            flush()
            current_room = room
            buf.append(raw)
        else:
            buf.append(raw)
    flush()
    if not blocks:
        return ""
    if len(blocks) == 1:
        return blocks[0]
    sep = f"\n\n{ROOM_ADD_SEPARATOR}\n\n"
    return sep.join(blocks)


def join_room_caption_blocks(blocks: list[str]) -> str:
    """이미 방 단위로 나뉜 본문 블록을 구분선으로 연결 (중복 공백 블록 스킵)."""
    cleaned = [b.strip() for b in blocks if b and str(b).strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    sep = f"\n\n{ROOM_ADD_SEPARATOR}\n\n"
    return sep.join(cleaned)
