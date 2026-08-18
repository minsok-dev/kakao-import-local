# [변경사유]: Phase 4.2+ multi_room 캡션 — 방 사이 ADD 구분선
# [변경사유]: 단톡방 헤더 + 이미지 앞/뒤 설명은 채팅분리 구분선 (ADD 와 구분)
"""캡션 합치기 시 방·앞뒤 채팅 구분."""

from __future__ import annotations

from typing import Any

# [변경사유]: 여러 방 텍스트 union 시 운영자가 구분할 수 있게
ROOM_ADD_SEPARATOR = "<------------- ADD 구분선 ------------>"
# [변경사유]: 같은 방·같은 사진 그룹에서 이미지 앞 글과 뒤 글을 합칠 때 ADD 와 구분
CHAT_SPLIT_SEPARATOR = "<------------- 채팅분리 구분선 ------------>"


def room_display_name(
    room_title: str | None,
    chat_rel_path: str | None = None,
) -> str:
    """단톡방 표시명. 채팅 제목 우선, 없으면 폴더명."""
    title = str(room_title or "").strip()
    if title:
        return title
    rel = str(chat_rel_path or "").replace("\\", "/").strip()
    if rel:
        parts = [p for p in rel.split("/") if p and p not in ("chats", "photos")]
        if parts:
            return parts[0]
    return "알 수 없는 방"


def format_room_header(title: str) -> str:
    """설명 최상단 단톡방 표기."""
    return f"[단톡방: {title}]"


def _join_room_buffer(buf: list[dict[str, Any]]) -> str:
    """
    한 방 버퍼: 앞(before)→뒤(after) 전환 지점에 채팅분리 구분선.
    position 없으면 기존처럼 줄바꿈만.
    """
    has_before = any(x.get("position") == "before" for x in buf)
    has_after = any(x.get("position") == "after" for x in buf)
    insert_split = has_before and has_after
    seen_after = False
    pieces: list[str] = []
    for item in buf:
        raw = str(item.get("text") or "").strip()
        if not raw:
            continue
        pos = item.get("position")
        if insert_split and pos == "after" and not seen_after:
            if pieces:
                pieces.append(CHAT_SPLIT_SEPARATOR)
            seen_after = True
        pieces.append(raw)
    if not pieces:
        return ""

    body = pieces[0]
    i = 1
    while i < len(pieces):
        if pieces[i] == CHAT_SPLIT_SEPARATOR:
            body += f"\n\n{CHAT_SPLIT_SEPARATOR}\n\n"
            i += 1
            if i < len(pieces):
                body += pieces[i]
                i += 1
            continue
        body += "\n" + pieces[i]
        i += 1

    has_hint = any(
        str(x.get("room_title") or "").strip()
        or str(x.get("chat_rel_path") or "").strip()
        for x in buf
    )
    if not has_hint:
        return body
    title = ""
    for item in buf:
        title = room_display_name(item.get("room_title"), item.get("chat_rel_path"))
        if title:
            break
    if not title:
        return body
    return f"{format_room_header(title)}\n{body}"


def join_texts_by_room(
    parts: list[dict[str, Any]],
    *,
    text_key: str = "body_raw",
    room_key: str = "chat_id",
) -> str:
    """
    같은 chat_id 연속 구간은 줄바꿈, 이미지 앞/뒤는 채팅분리 구분선,
    방이 바뀌면 ADD 구분선(+위아래 빈 줄)으로 연결.
    chat_id 없으면 기존처럼 \\n 조인.
    """
    blocks: list[str] = []
    current_room: Any = object()
    buf: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal buf
        chunk = _join_room_buffer(buf).strip()
        if chunk:
            blocks.append(chunk)
        buf = []

    for p in parts:
        raw = str(p.get(text_key) or "").strip()
        if not raw:
            continue
        room = p.get(room_key)
        pos = p.get("position")
        entry = {
            "text": raw,
            "position": pos if pos in ("before", "after") else None,
            "room_title": p.get("room_title"),
            "chat_rel_path": p.get("chat_rel_path"),
        }
        if room is None:
            if current_room is not object() and current_room is not None:
                flush()
            current_room = None
            buf.append(entry)
            continue
        if current_room is object():
            current_room = room
            buf.append(entry)
            continue
        if room != current_room:
            flush()
            current_room = room
            buf.append(entry)
        else:
            buf.append(entry)
    flush()
    if not blocks:
        return ""
    if len(blocks) == 1:
        return blocks[0]
    sep = f"\n\n{ROOM_ADD_SEPARATOR}\n\n"
    return sep.join(blocks)


def join_room_caption_blocks(blocks: list[str]) -> str:
    """이미 방 단위로 나뉜 본문 블록을 ADD 구분선으로 연결 (중복 공백 블록 스킵)."""
    cleaned = [b.strip() for b in blocks if b and str(b).strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    sep = f"\n\n{ROOM_ADD_SEPARATOR}\n\n"
    return sep.join(cleaned)
