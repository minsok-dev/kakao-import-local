# [변경사유]: Phase1 — KakaoTalk_YYYYMMDD_HHMMSSmmm 파일명 시각 (EXIF보다 SSOT)
"""사진 파일명 파싱."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# KakaoTalk_20260724_005030533.png
_NAME_RE = re.compile(
    r"^KakaoTalk_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})(\d{3})\.([A-Za-z0-9]+)$"
)


@dataclass(frozen=True)
class PhotoNameParse:
    """파일명 파싱 결과."""

    ok: bool
    name_time: datetime | None
    ext: str
    error: str | None = None


def parse_kakaotalk_filename(file_name: str) -> PhotoNameParse:
    """파일명 → datetime(밀리초 포함). 확장자 소문자 정규화."""
    m = _NAME_RE.match(file_name)
    if not m:
        ext = Path(file_name).suffix.lstrip(".").lower()
        return PhotoNameParse(ok=False, name_time=None, ext=ext, error="unparsed_photo")
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh, mm, ss, mss = int(m.group(4)), int(m.group(5)), int(m.group(6)), int(m.group(7))
    ext = m.group(8).lower()
    try:
        dt = datetime(y, mo, d, hh, mm, ss, mss * 1000)
    except ValueError as exc:
        return PhotoNameParse(ok=False, name_time=None, ext=ext, error=str(exc))
    return PhotoNameParse(ok=True, name_time=dt, ext=ext, error=None)
