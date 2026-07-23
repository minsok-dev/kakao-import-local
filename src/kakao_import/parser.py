# [변경사유]: Phase1 — 카카오 대화 TXT 파서
"""대화 export 파서."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from kakao_import.encoding_util import read_text_with_encoding
from kakao_import.normalize import normalize_for_compare
from kakao_import.timeutil import combine_abs

_DATE_RE = re.compile(
    r"^---------------\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일"
)
_MSG_RE = re.compile(
    r"^\[([^\]]+)\]\s*\[(오전|오후)\s*(\d{1,2}):(\d{2})\]\s*(.*)$"
)
_PHOTO_N_RE = re.compile(r"^사진\s+(\d+)장$")
_TITLE_RE = re.compile(r"^(.+?)\s*님과\s*카카오톡\s*대화\s*$")


@dataclass
class ParseErrorRow:
    """파싱 실패/미인식 행."""

    line_no: int
    raw_excerpt: str
    error_code: str
    detail: str


@dataclass
class ParsedMsg:
    """파싱 메시지."""

    seq: int
    msg_kind: str
    sender: str | None
    abs_time: datetime | None
    body_raw: str
    body_norm: str
    photo_count: int | None
    line_no: int


@dataclass
class ChatParseResult:
    """채팅 파일 파싱 결과."""

    room_title: str | None
    encoding: str
    content_sha256: str
    messages: list[ParsedMsg] = field(default_factory=list)
    errors: list[ParseErrorRow] = field(default_factory=list)


def _classify_body(body: str) -> tuple[str, int | None]:
    """본문 → (msg_kind, photo_count)."""
    b = body.strip()
    if b == "사진":
        return "photo", 1
    m = _PHOTO_N_RE.match(b)
    if m:
        return "photo_multi", int(m.group(1))
    if b == "동영상":
        return "video", None
    if b == "이모티콘":
        return "emoji", None
    if b.startswith(("파일:", "파일 :")):
        return "file", None
    return "text", None


def _is_system_line(line: str) -> bool:
    """[닉] 없는 시스템성 한 줄."""
    s = line.strip()
    if not s or s.startswith("---------------"):
        return False
    if _MSG_RE.match(s):
        return False
    if "님이 들어왔습니다" in s or "님이 나갔습니다" in s:
        return True
    if s.startswith("저장한 날짜"):
        return False
    return False


def parse_chat_text(text: str, *, source_label: str = "") -> ChatParseResult:
    """문자열 파싱 (테스트용)."""
    _ = source_label
    lines = text.splitlines()
    room_title: str | None = None
    current_date: date | None = None
    messages: list[ParsedMsg] = []
    errors: list[ParseErrorRow] = []
    seq = 0
    i = 0
    content_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

    while i < len(lines):
        line = lines[i]
        line_no = i + 1
        stripped = line.strip()

        if i == 0:
            tm = _TITLE_RE.match(stripped)
            if tm:
                room_title = tm.group(1).strip()
                i += 1
                continue

        if stripped.startswith("저장한 날짜"):
            i += 1
            continue

        dm = _DATE_RE.match(stripped)
        if dm:
            try:
                current_date = date(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)))
            except ValueError as exc:
                errors.append(
                    ParseErrorRow(line_no, stripped[:200], "bad_date_header", str(exc))
                )
            i += 1
            continue

        mm = _MSG_RE.match(line)
        if mm:
            sender = mm.group(1)
            ampm = mm.group(2)
            hour = int(mm.group(3))
            minute = int(mm.group(4))
            first_body = mm.group(5)
            body_lines = [first_body]
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if _DATE_RE.match(nxt.strip()) or _MSG_RE.match(nxt) or _is_system_line(nxt):
                    break
                if nxt.strip() == "" and j + 1 < len(lines):
                    # 빈 줄은 본문 일부로 유지
                    peek = lines[j + 1]
                    if _DATE_RE.match(peek.strip()) or _MSG_RE.match(peek):
                        break
                body_lines.append(nxt)
                j += 1
            body_raw = "\n".join(body_lines)
            abs_time: datetime | None = None
            if current_date is None:
                errors.append(
                    ParseErrorRow(
                        line_no, line[:200], "message_without_date", "no date header yet"
                    )
                )
            else:
                try:
                    abs_time = combine_abs(current_date, ampm, hour, minute)
                except ValueError as exc:
                    errors.append(
                        ParseErrorRow(line_no, line[:200], "bad_message_time", str(exc))
                    )
            kind, pcount = _classify_body(body_raw)
            seq += 1
            messages.append(
                ParsedMsg(
                    seq=seq,
                    msg_kind=kind,
                    sender=sender,
                    abs_time=abs_time,
                    body_raw=body_raw,
                    body_norm=normalize_for_compare(body_raw),
                    photo_count=pcount,
                    line_no=line_no,
                )
            )
            i = j
            continue

        if _is_system_line(line):
            seq += 1
            messages.append(
                ParsedMsg(
                    seq=seq,
                    msg_kind="system",
                    sender=None,
                    abs_time=None,
                    body_raw=stripped,
                    body_norm=normalize_for_compare(stripped),
                    photo_count=None,
                    line_no=line_no,
                )
            )
            i += 1
            continue

        if stripped == "":
            i += 1
            continue

        # 미인식 — 버리지 않음
        errors.append(
            ParseErrorRow(line_no, stripped[:200], "unparsed_line", "no pattern matched")
        )
        i += 1

    return ChatParseResult(
        room_title=room_title,
        encoding="utf-8",
        content_sha256=content_sha,
        messages=messages,
        errors=errors,
    )


def parse_chat_file(path: Path) -> ChatParseResult:
    """파일 파싱 + encoding."""
    text, enc = read_text_with_encoding(path)
    result = parse_chat_text(text, source_label=path.name)
    result.encoding = enc
    # content hash of raw bytes preferred
    result.content_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return result
