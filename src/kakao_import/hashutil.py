# [변경사유]: Phase1 — 파일 SHA-256 (exact only)
"""해시 유틸."""

from __future__ import annotations

import hashlib
from pathlib import Path

# 이미지 확장자 (스캔 stub)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """파일 바이트 SHA-256 hex."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def is_image_path(path: Path) -> bool:
    """이미지 확장자 여부."""
    return path.suffix.lower() in IMAGE_SUFFIXES
