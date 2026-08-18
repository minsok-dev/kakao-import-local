# [변경사유]: 평균 해시 — extra 없이 테스트 가능. CLIP 과 분리
"""8x8 aHash (hex)."""

from __future__ import annotations

from pathlib import Path


def average_hash_hex(path: Path, *, size: int = 8) -> str | None:
    """
    간단 평균 해시. Pillow 없으면 None (그룹은 SHA·앨범만).
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            im = im.convert("L").resize((size, size))
            pixels = list(im.getdata())
    except OSError:
        return None
    if not pixels:
        return None
    avg = sum(pixels) / len(pixels)
    bits = 0
    for i, p in enumerate(pixels):
        if p >= avg:
            bits |= 1 << i
    width = (size * size + 3) // 4
    return f"{bits:0{width}x}"


def image_min_side_and_bytes(path: Path) -> tuple[int | None, int]:
    """한 변 최소 px, 파일 크기. 디코드 실패 시 px=None."""
    byte_size = path.stat().st_size if path.is_file() else 0
    try:
        from PIL import Image
    except ImportError:
        return None, byte_size
    try:
        with Image.open(path) as im:
            w, h = im.size
            return min(int(w), int(h)), byte_size
    except OSError:
        return None, byte_size
