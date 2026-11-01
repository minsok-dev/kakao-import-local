# [변경사유]: 밴드 게이트 — 작은 이미지는 모델 없이 non_poster
"""poster_gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from kakao_import.poster_gate import FAIL_OPEN, classify_image_path


def test_tiny_image_is_non_poster(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    from PIL import Image

    img = tmp_path / "sticker.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(img)
    assert classify_image_path(img) == "non_poster"


def test_missing_file_is_fail_open(tmp_path: Path) -> None:
    assert classify_image_path(tmp_path / "nope.jpg") == FAIL_OPEN
