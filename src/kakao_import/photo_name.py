# [변경사유]: Phase1 — KakaoTalk_YYYYMMDD_HHMMSSmmm 파일명 시각 (EXIF보다 SSOT)
# [변경사유]: PC 앨범 `_01`/`_02` 접미사 허용 — 동일 name_time + sequence로 묶음 매칭
"""사진 파일명 파싱."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# KakaoTalk_20260724_005030533.png
# KakaoTalk_20260804_161921080_01.png  (앨범 멤버 — 동일 시각 + sequence)
_NAME_RE = re.compile(
    r"^KakaoTalk_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})(\d{3})"
    r"(?:_(\d+))?\.([A-Za-z0-9]+)$"
)


@dataclass(frozen=True)
class PhotoNameParse:
    """파일명 파싱 결과."""

    ok: bool
    name_time: datetime | None
    ext: str
    # [변경사유]: 접미사 없으면 0, `_01`→1 — 분 버킷 내 정렬용
    sequence: int = 0
    error: str | None = None


def parse_kakaotalk_filename(file_name: str) -> PhotoNameParse:
    """파일명 → datetime(밀리초 포함). 확장자 소문자 정규화. 선택적 `_NN` sequence."""
    m = _NAME_RE.match(file_name)
    if not m:
        ext = Path(file_name).suffix.lstrip(".").lower()
        return PhotoNameParse(ok=False, name_time=None, ext=ext, sequence=0, error="unparsed_photo")
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh, mm, ss, mss = int(m.group(4)), int(m.group(5)), int(m.group(6)), int(m.group(7))
    seq_raw = m.group(8)
    sequence = int(seq_raw) if seq_raw is not None else 0
    ext = m.group(9).lower()
    try:
        dt = datetime(y, mo, d, hh, mm, ss, mss * 1000)
    except ValueError as exc:
        return PhotoNameParse(ok=False, name_time=None, ext=ext, sequence=sequence, error=str(exc))
    return PhotoNameParse(ok=True, name_time=dt, ext=ext, sequence=sequence, error=None)


def kakao_album_stem_and_seq(file_name: str) -> tuple[str | None, int]:
    """
    PC 앨범 묶음 키: 동일 `KakaoTalk_YYYYMMDD_HHMMSSmmm` 스템 + `_NN` sequence.
    [변경사유]: 채팅 연속 사진만으로 묶지 않고, `_01` 등 파일명 앨범만 식별.
    """
    parsed = parse_kakaotalk_filename(Path(file_name).name)
    if not parsed.ok or parsed.name_time is None:
        return None, 0
    ms = parsed.name_time.microsecond // 1000
    stem = parsed.name_time.strftime("%Y%m%d_%H%M%S") + f"{ms:03d}"
    return stem, int(parsed.sequence or 0)
