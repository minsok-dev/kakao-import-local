# [변경사유]: hash 이후 판정만 기록. 파일 이동 금지. human 행 유지. fail-open
"""포스터 분류 적용."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kakao_import.db import connect
from kakao_import.logging_util import get_logger
from kakao_import.pipeline import room_id_from_rel
from kakao_import.poster_ahash import image_min_side_and_bytes
from kakao_import.poster_const import (
    ACTIVE_MODEL_PATH,
    CLIP_MODEL_KEY,
    MODELS_DIR,
    SOURCE_HUMAN,
    SOURCE_MODEL,
    SOURCE_RULE,
    STATUS_NON_POSTER,
    TINY_MAX_BYTES,
    TINY_MIN_SIDE,
)
from kakao_import.poster_decision import decide_status, excluded_flag
from kakao_import.poster_embed import embed_cached, extra_install_hint, has_poster_extra
from kakao_import.poster_schema import ensure_poster_schema, get_classify_row

log = get_logger(__name__)


def load_active_model(models_dir: Path | None = None) -> dict[str, Any] | None:
    """active-model.json + joblib. 없으면 None."""
    mdir = models_dir or MODELS_DIR
    active_path = mdir / "active-model.json" if models_dir else ACTIVE_MODEL_PATH
    if not active_path.is_file():
        return None
    try:
        meta = json.loads(active_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("active-model read fail err=%s", exc)
        return None
    version = str(meta.get("version") or "")
    joblib_name = str(meta.get("joblib") or f"{version}.joblib")
    joblib_path = mdir / joblib_name
    if not version or not joblib_path.is_file():
        log.warning("active-model missing joblib version=%s", version)
        return None
    if not has_poster_extra():
        log.warning("%s", extra_install_hint())
        return None
    import joblib

    bundle = joblib.load(joblib_path)
    return {
        "version": version,
        "clf": bundle["clf"],
        "exclude_threshold": float(bundle["exclude_threshold"]),
        "poster_threshold": float(bundle["poster_threshold"]),
        "clip_model": bundle.get("clip_model") or CLIP_MODEL_KEY,
    }


def _upsert_classify(
    conn,
    *,
    room_id: str,
    photo_id: int | None,
    sha256: str,
    rel_path: str,
    file_name: str,
    model_version: str | None,
    poster_score: float | None,
    status: str,
    source: str,
) -> str:
    """human 이면 덮지 않음. 반환: skipped_human | inserted | updated."""
    sha = sha256.lower()
    existing = get_classify_row(conn, room_id, sha)
    if existing and existing.get("source") == SOURCE_HUMAN:
        log.info("poster-classify keep human room=%s sha=%s", room_id, sha[:12])
        return "skipped_human"
    excl = excluded_flag(status)
    conn.execute(
        """
        INSERT INTO poster_classify (
          room_id, photo_id, sha256, rel_path, file_name,
          model_version, poster_score, status, source, excluded_from_upload, classified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(room_id, sha256) DO UPDATE SET
          photo_id = excluded.photo_id,
          rel_path = excluded.rel_path,
          file_name = excluded.file_name,
          model_version = excluded.model_version,
          poster_score = excluded.poster_score,
          status = excluded.status,
          source = excluded.source,
          excluded_from_upload = excluded.excluded_from_upload,
          classified_at = datetime('now')
        """,
        (
            room_id,
            photo_id,
            sha,
            rel_path,
            file_name,
            model_version,
            poster_score,
            status,
            source,
            excl,
        ),
    )
    return "updated" if existing else "inserted"


def cmd_poster_classify(
    settings,
    root: Path | None = None,
    *,
    models_dir: Path | None = None,
) -> dict[str, Any]:
    """
    photo_file(sha 있음) 판정. 활성 모델 없으면 no-op.
    extra 없음·오류 → 제외하지 않음.
    """
    root = root or settings.export_root
    if not settings.db_path.exists():
        log.info("poster-classify skip no-db")
        return {"ok": True, "skipped": "no_db", "classified": 0}
    with connect(settings.db_path) as conn:
        ensure_poster_schema(conn)
        conn.commit()
    if not has_poster_extra():
        log.warning("poster-classify skip %s", extra_install_hint())
        return {"ok": True, "skipped": "no_extra", "classified": 0}
    active = load_active_model(models_dir)
    if active is None:
        log.info("poster-classify skip no-active-model")
        return {"ok": True, "skipped": "no_active_model", "classified": 0}

    clf = active["clf"]
    exclude_threshold = active["exclude_threshold"]
    poster_threshold = active["poster_threshold"]
    version = active["version"]
    classes = list(clf.classes_)
    col = classes.index(1) if 1 in classes else len(classes) - 1

    counts = {
        "classified": 0,
        "non_poster": 0,
        "poster": 0,
        "uncertain": 0,
        "rule": 0,
        "fail_open": 0,
        "skipped_human": 0,
        "missing_file": 0,
    }
    with connect(settings.db_path) as conn:
        ensure_poster_schema(conn)
        rows = conn.execute(
            """
            SELECT id, rel_path, file_name, sha256, byte_size
            FROM photo_file
            WHERE sha256 IS NOT NULL AND sha256 != ''
            ORDER BY id
            """
        ).fetchall()
        log.info("poster-classify photos=%s version=%s", len(rows), version)
        for row in rows:
            photo_id = int(row["id"])
            rel = str(row["rel_path"] or "").replace("\\", "/")
            name = str(row["file_name"] or Path(rel).name)
            sha = str(row["sha256"]).lower()
            room_id = room_id_from_rel(rel)
            abs_path = (root / rel) if root else None
            if abs_path is None or not abs_path.is_file():
                counts["missing_file"] += 1
                log.warning("poster-classify missing photo_id=%s rel=%s", photo_id, rel)
                continue
            # 작은 스티커 rule
            min_side, nbytes = image_min_side_and_bytes(abs_path)
            if (
                min_side is not None
                and min_side < TINY_MIN_SIDE
                and nbytes < TINY_MAX_BYTES
            ):
                action = _upsert_classify(
                    conn,
                    room_id=room_id,
                    photo_id=photo_id,
                    sha256=sha,
                    rel_path=rel,
                    file_name=name,
                    model_version=version,
                    poster_score=None,
                    status=STATUS_NON_POSTER,
                    source=SOURCE_RULE,
                )
                counts["rule"] += 1
                if action == "skipped_human":
                    counts["skipped_human"] += 1
                else:
                    counts["classified"] += 1
                    counts["non_poster"] += 1
                continue
            vec = embed_cached(abs_path, sha)
            if not vec:
                counts["fail_open"] += 1
                log.warning("poster-classify fail-open photo_id=%s", photo_id)
                continue
            try:
                import numpy as np

                proba = clf.predict_proba(np.array([vec], dtype=np.float32))[0]
                score = float(proba[col])
            except Exception as exc:  # noqa: BLE001
                counts["fail_open"] += 1
                log.warning("poster-classify predict fail photo_id=%s err=%s", photo_id, exc)
                continue
            status = decide_status(
                score,
                exclude_threshold=exclude_threshold,
                poster_threshold=poster_threshold,
            )
            action = _upsert_classify(
                conn,
                room_id=room_id,
                photo_id=photo_id,
                sha256=sha,
                rel_path=rel,
                file_name=name,
                model_version=version,
                poster_score=score,
                status=status,
                source=SOURCE_MODEL,
            )
            if action == "skipped_human":
                counts["skipped_human"] += 1
                continue
            counts["classified"] += 1
            counts[status] = counts.get(status, 0) + 1
        conn.commit()
    log.info("poster-classify done %s", counts)
    return {"ok": True, "version": version, **counts}
