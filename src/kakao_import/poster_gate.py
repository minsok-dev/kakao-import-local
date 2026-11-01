# [변경사유]: 밴드 수집이 카카오와 같은 포스터 모델을 쓰게 한다. 파일은 옮기지 않는다
"""이미지 한 장의 포스터 판정. 모델이 없으면 fail_open."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from kakao_import.logging_util import get_logger
from kakao_import.poster_ahash import image_min_side_and_bytes
from kakao_import.poster_const import (
    STATUS_NON_POSTER,
    TINY_MAX_BYTES,
    TINY_MIN_SIDE,
)
from kakao_import.poster_decision import decide_status

log = get_logger(__name__)

FAIL_OPEN = "fail_open"


def classify_image_path(path: Path) -> str:
    """poster | uncertain | non_poster | fail_open.

    작은 스티커는 모델 없이 non_poster. 모델·임베딩 실패는 fail_open (업로드 유지).
    """
    if not path.is_file():
        log.warning("poster-gate missing path=%s", path)
        return FAIL_OPEN
    min_side, nbytes = image_min_side_and_bytes(path)
    if (
        min_side is not None
        and min_side < TINY_MIN_SIDE
        and nbytes < TINY_MAX_BYTES
    ):
        log.info("poster-gate tiny non_poster path=%s side=%s bytes=%s", path.name, min_side, nbytes)
        return STATUS_NON_POSTER

    from kakao_import.poster_classify import load_active_model
    from kakao_import.poster_embed import embed_image

    active = load_active_model()
    if active is None:
        log.info("poster-gate fail-open no-model path=%s", path.name)
        return FAIL_OPEN
    vec = embed_image(path)
    if not vec:
        log.info("poster-gate fail-open no-embed path=%s", path.name)
        return FAIL_OPEN
    try:
        import numpy as np

        clf = active["clf"]
        classes = list(clf.classes_)
        col = classes.index(1) if 1 in classes else len(classes) - 1
        proba = clf.predict_proba(np.array([vec], dtype=np.float32))[0]
        score = float(proba[col])
    except Exception as exc:  # noqa: BLE001
        log.warning("poster-gate predict fail path=%s err=%s", path.name, exc)
        return FAIL_OPEN
    status = decide_status(
        score,
        exclude_threshold=float(active["exclude_threshold"]),
        poster_threshold=float(active["poster_threshold"]),
    )
    log.info("poster-gate status=%s score=%.4f path=%s", status, score, path.name)
    return status


def _reply(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def serve_stdio() -> None:
    """한 줄 JSON 요청 → 한 줄 JSON 응답. 로그는 stderr."""
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            req = json.loads(text)
        except json.JSONDecodeError as exc:
            _reply({"ok": False, "error": f"json {exc}"})
            continue
        paths = req.get("paths") if isinstance(req, dict) else None
        if not isinstance(paths, list):
            _reply({"ok": False, "id": req.get("id") if isinstance(req, dict) else None, "error": "paths"})
            continue
        results = []
        for raw in paths:
            status = classify_image_path(Path(str(raw)))
            results.append({"path": str(raw), "status": status})
        _reply({"ok": True, "id": req.get("id"), "results": results})


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--serve"]:
        serve_stdio()
        return
    statuses = [classify_image_path(Path(a)) for a in args]
    _reply({"ok": True, "results": [{"path": a, "status": s} for a, s in zip(args, statuses)]})


if __name__ == "__main__":
    main()
