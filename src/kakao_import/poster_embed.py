# [변경사유]: OpenCLIP 동결 임베딩 + sha256 파일 캐시. 실패 시 None (fail-open)
"""CLIP embedding."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

from kakao_import.hashutil import sha256_file
from kakao_import.logging_util import get_logger
from kakao_import.poster_const import (
    CLIP_ARCH,
    CLIP_MODEL_KEY,
    CLIP_PRETRAINED,
    EMBED_CACHE_DIR,
)

log = get_logger(__name__)

_clip_bundle: tuple[Any, Any, str] | None = None


def has_poster_extra() -> bool:
    """torch + open_clip + sklearn 설치 여부."""
    try:
        import joblib  # noqa: F401
        import numpy  # noqa: F401
        import open_clip  # noqa: F401
        import sklearn  # noqa: F401
        import torch  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def extra_install_hint() -> str:
    """운영자 안내."""
    return '포스터 extra 필요: pip install -e ".[poster]"'


def _device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _load_clip() -> tuple[Any, Any, str]:
    """모델·전처리·device. 프로세스당 1회."""
    global _clip_bundle
    if _clip_bundle is not None:
        return _clip_bundle
    import open_clip
    import torch

    device = _device()
    log.info("openclip load arch=%s pretrained=%s device=%s", CLIP_ARCH, CLIP_PRETRAINED, device)
    model, _, preprocess = open_clip.create_model_and_transforms(
        CLIP_ARCH, pretrained=CLIP_PRETRAINED
    )
    model.eval()
    model.to(device)
    _clip_bundle = (model, preprocess, device)
    return _clip_bundle


def _cache_path(sha256: str) -> Path:
    safe = CLIP_MODEL_KEY.replace("/", "_")
    d = EMBED_CACHE_DIR / safe
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{sha256.lower()}.f32"


def _write_f32(path: Path, vec: list[float]) -> None:
    path.write_bytes(struct.pack(f"<{len(vec)}f", *vec))


def _read_f32(path: Path) -> list[float] | None:
    data = path.read_bytes()
    n = len(data) // 4
    if n < 8 or len(data) != n * 4:
        return None
    return list(struct.unpack(f"<{n}f", data))


def embed_image(path: Path) -> list[float] | None:
    """이미지 → L2 정규화 벡터. 실패 시 None."""
    try:
        import torch
        from PIL import Image
    except ImportError:
        log.warning("embed skip extra-missing path=%s", path.name)
        return None
    try:
        model, preprocess, device = _load_clip()
        with Image.open(path) as im:
            im = im.convert("RGB")
            tensor = preprocess(im).unsqueeze(0).to(device)
        with torch.no_grad():
            feat = model.encode_image(tensor)
            feat = feat / feat.norm(dim=-1, keepdim=True)
            vec = feat.squeeze(0).float().cpu().tolist()
        return [float(x) for x in vec]
    except Exception as exc:  # noqa: BLE001 — 단건 fail-open
        log.warning("embed fail path=%s err=%s", path.name, str(exc)[:160])
        return None


def embed_cached(path: Path, sha256: str | None = None) -> list[float] | None:
    """sha256 캐시 우선. 없으면 CLIP 후 저장."""
    sha = (sha256 or sha256_file(path)).lower()
    cache = _cache_path(sha)
    if cache.is_file():
        cached = _read_f32(cache)
        if cached:
            return cached
    vec = embed_image(path)
    if vec:
        _write_f32(cache, vec)
        log.info("embed cache sha=%s dim=%s", sha[:12], len(vec))
    return vec
