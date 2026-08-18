# [변경사유]: dataset 스캔 → 그룹 split → 로지스틱 전체 재학습. 활성은 바꾸지 않음
"""포스터 분류기 학습."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kakao_import.hashutil import is_image_path, sha256_file
from kakao_import.logging_util import get_logger
from kakao_import.poster_ahash import average_hash_hex
from kakao_import.poster_const import (
    CLIP_MODEL_KEY,
    MODELS_DIR,
    NON_POSTER_DIR,
    POSTER_DIR,
    SOURCE_MODEL,
)
from kakao_import.poster_decision import decide_status, pick_thresholds
from kakao_import.poster_embed import embed_cached, extra_install_hint, has_poster_extra
from kakao_import.poster_split import LabeledImage, album_and_time, split_train_test

log = get_logger(__name__)


def scan_dataset_dir(folder: Path, label: int) -> list[LabeledImage]:
    """한 클래스 폴더 스캔. 원본 raw 는 보지 않음."""
    out: list[LabeledImage] = []
    if not folder.is_dir():
        log.warning("dataset missing dir=%s", folder)
        return out
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if not is_image_path(path):
            continue
        sha = sha256_file(path)
        stem, nt = album_and_time(path.name)
        out.append(
            LabeledImage(
                path=path,
                sha256=sha,
                label=label,
                album_stem=stem,
                name_time=nt,
                ahash_hex=average_hash_hex(path),
            )
        )
    log.info("dataset scan dir=%s label=%s n=%s", folder.name, label, len(out))
    return out


def next_model_version(models_dir: Path | None = None) -> str:
    """poster-clip-vN 다음 번호."""
    d = models_dir or MODELS_DIR
    d.mkdir(parents=True, exist_ok=True)
    n_max = 0
    for p in d.glob("poster-clip-v*.json"):
        m = re.search(r"poster-clip-v(\d+)\.json$", p.name)
        if m:
            n_max = max(n_max, int(m.group(1)))
    return f"poster-clip-v{n_max + 1}"


def cmd_poster_train(
    *,
    poster_dir: Path | None = None,
    non_poster_dir: Path | None = None,
    models_dir: Path | None = None,
) -> dict[str, Any]:
    """학습. 활성 모델은 건드리지 않음."""
    if not has_poster_extra():
        return {"ok": False, "error": extra_install_hint()}
    import joblib
    import numpy as np
    from sklearn.linear_model import LogisticRegression

    pdir = poster_dir or POSTER_DIR
    ndir = non_poster_dir or NON_POSTER_DIR
    mdir = models_dir or MODELS_DIR
    samples = scan_dataset_dir(pdir, 1) + scan_dataset_dir(ndir, 0)
    n_pos = sum(1 for s in samples if s.label == 1)
    n_neg = sum(1 for s in samples if s.label == 0)
    log.info("poster-train start poster=%s non_poster=%s", n_pos, n_neg)
    if n_pos < 10 or n_neg < 10:
        return {
            "ok": False,
            "error": f"dataset too small poster={n_pos} non_poster={n_neg} (min 10)",
        }

    vectors: list[list[float] | None] = []
    for i, s in enumerate(samples):
        vec = embed_cached(s.path, s.sha256)
        vectors.append(vec)
        if (i + 1) % 25 == 0:
            log.info("embed progress %s/%s", i + 1, len(samples))
    ok_idx = [i for i, v in enumerate(vectors) if v]
    dropped = len(samples) - len(ok_idx)
    if dropped:
        log.warning("embed dropped=%s", dropped)
    usable = [samples[i] for i in ok_idx]
    usable_vec = [vectors[i] for i in ok_idx]
    if not usable:
        return {"ok": False, "error": "no embeddings"}

    train_idx, test_idx = split_train_test(usable)
    if not train_idx:
        return {"ok": False, "error": "empty train split"}

    x_train = np.array([usable_vec[i] for i in train_idx], dtype=np.float32)
    y_train = np.array([usable[i].label for i in train_idx], dtype=np.int32)
    clf = LogisticRegression(
        max_iter=2000,
        class_weight="balanced",
        random_state=42,
    )
    clf.fit(x_train, y_train)
    log.info("logistic fit train_n=%s classes=%s", len(train_idx), list(clf.classes_))

    def scores_of(idxs: list[int]) -> list[float]:
        if not idxs:
            return []
        x = np.array([usable_vec[i] for i in idxs], dtype=np.float32)
        proba = clf.predict_proba(x)
        # 클래스 1(포스터) 열
        classes = list(clf.classes_)
        col = classes.index(1) if 1 in classes else len(classes) - 1
        return [float(p[col]) for p in proba]

    test_poster_idx = [i for i in test_idx if usable[i].label == 1]
    test_non_idx = [i for i in test_idx if usable[i].label == 0]
    test_poster_scores = scores_of(test_poster_idx)
    exclude_threshold, poster_threshold, thr_meta = pick_thresholds(test_poster_scores)

    false_exclude = 0
    for sc in test_poster_scores:
        if decide_status(
            sc, exclude_threshold=exclude_threshold, poster_threshold=poster_threshold
        ) == "non_poster":
            false_exclude += 1
    test_non_scores = scores_of(test_non_idx)
    caught = 0
    for sc in test_non_scores:
        if decide_status(
            sc, exclude_threshold=exclude_threshold, poster_threshold=poster_threshold
        ) == "non_poster":
            caught += 1

    version = next_model_version(mdir)
    mdir.mkdir(parents=True, exist_ok=True)
    joblib_path = mdir / f"{version}.joblib"
    json_path = mdir / f"{version}.json"
    created = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    payload = {
        "version": version,
        "clip_model": CLIP_MODEL_KEY,
        "exclude_threshold": exclude_threshold,
        "poster_threshold": poster_threshold,
        "created_at": created,
        "train_n": len(train_idx),
        "test_n": len(test_idx),
        "poster_n": n_pos,
        "non_poster_n": n_neg,
        "test_poster_n": len(test_poster_idx),
        "test_non_poster_n": len(test_non_idx),
        "false_exclude": false_exclude,
        "test_non_poster_caught": caught,
        "threshold_meta": thr_meta,
        "source": SOURCE_MODEL,
    }
    joblib.dump(
        {
            "clf": clf,
            "clip_model": CLIP_MODEL_KEY,
            "exclude_threshold": exclude_threshold,
            "poster_threshold": poster_threshold,
        },
        joblib_path,
    )
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "poster-train done version=%s false_exclude=%s exclude_threshold=%.6f poster_threshold=%.6f",
        version,
        false_exclude,
        exclude_threshold,
        poster_threshold,
    )
    return {"ok": True, **payload, "joblib": str(joblib_path), "json": str(json_path)}
