# [변경사유]: Phase1 — TXT encoding 감지 (utf-8-sig / utf-8 / cp949)
"""텍스트 파일 인코딩."""

from __future__ import annotations

from pathlib import Path


def read_text_with_encoding(path: Path) -> tuple[str, str]:
    """
    파일을 읽어 (text, encoding_name) 반환.
    시도 순서: utf-8-sig → utf-8 → cp949.
    """
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    # [변경사유]: 최후 latin-1 — 파싱 오류로 남기기 위함
    return data.decode("latin-1", errors="replace"), "latin-1-replace"
