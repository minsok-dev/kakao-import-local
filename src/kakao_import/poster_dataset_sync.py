# [변경사유]: poster-review human 라벨 → dataset/ 복사 (원본 photos 불변) → 재학습 입력
"""포스터 human 라벨을 dataset 폴더로 동기화."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from kakao_import.db import connect
from kakao_import.logging_util import get_logger
from kakao_import.poster_const import (
    NON_POSTER_DIR,
    POSTER_DIR,
    SOURCE_HUMAN,
    STATUS_NON_POSTER,
    STATUS_POSTER,
)
from kakao_import.poster_schema import ensure_poster_schema
from kakao_import.similar_detect import resolve_photo_path

log = get_logger(__name__)


def _dataset_basename(*, room_id: str, sha256: str, file_name: str) -> str:
    """충돌 적은 복사본 이름."""
    safe_room = "".join(c if c.isalnum() or c in "-_" else "_" for c in room_id)[:40]
    stem = Path(file_name or "img").name
    return f"{safe_room}__{sha256[:12]}__{stem}"


def cmd_poster_dataset_sync(
    settings,
    *,
    poster_dir: Path | None = None,
    non_poster_dir: Path | None = None,
) -> dict[str, Any]:
    """
    source=human 이고 poster|non_poster 인 행을 dataset/ 로 복사.
    uncertain 스킵. 반대 폴더에 같은 sha 접두 파일이 있으면 제거 후 올바른 쪽에 복사.
    """
    root = settings.export_root
    if root is None or not Path(root).is_dir():
        return {"ok": False, "error": "export_root missing"}
    if not settings.db_path.exists():
        return {"ok": False, "error": "db missing"}

    p_dir = poster_dir or POSTER_DIR
    n_dir = non_poster_dir or NON_POSTER_DIR
    p_dir.mkdir(parents=True, exist_ok=True)
    n_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0
    missing = 0
    moved_away = 0
    errors = 0

    with connect(settings.db_path) as conn:
        ensure_poster_schema(conn)
        rows = conn.execute(
            """
            SELECT room_id, sha256, rel_path, file_name, status
            FROM poster_classify
            WHERE source = ?
              AND status IN (?, ?)
            ORDER BY classified_at DESC, room_id, sha256
            """,
            (SOURCE_HUMAN, STATUS_POSTER, STATUS_NON_POSTER),
        ).fetchall()

        seen_sha: set[str] = set()
        for r in rows:
            sha = str(r["sha256"] or "").lower()
            if len(sha) != 64 or sha in seen_sha:
                skipped += 1
                continue
            seen_sha.add(sha)
            status = str(r["status"])
            target_dir = p_dir if status == STATUS_POSTER else n_dir
            other_dir = n_dir if status == STATUS_POSTER else p_dir
            # [변경사유]: 라벨 번복 시 반대 클래스 폴더의 동일 sha 복사본 제거
            prefix = f"__{sha[:12]}__"
            for stale in other_dir.iterdir() if other_dir.is_dir() else []:
                if stale.is_file() and prefix in stale.name:
                    try:
                        stale.unlink()
                        moved_away += 1
                    except OSError as exc:
                        log.warning("dataset sync unlink fail path=%s err=%s", stale, exc)
                        errors += 1

            src = resolve_photo_path(Path(root), str(r["rel_path"] or ""))
            if src is None or not src.is_file():
                missing += 1
                log.warning(
                    "dataset sync missing src room=%s sha=%s rel=%s",
                    r["room_id"],
                    sha[:12],
                    r["rel_path"],
                )
                continue
            dest_name = _dataset_basename(
                room_id=str(r["room_id"] or "room"),
                sha256=sha,
                file_name=str(r["file_name"] or src.name),
            )
            dest = target_dir / dest_name
            try:
                if dest.is_file() and dest.stat().st_size == src.stat().st_size:
                    skipped += 1
                    continue
                shutil.copy2(src, dest)
                copied += 1
                log.info(
                    "dataset sync copy status=%s sha=%s dest=%s",
                    status,
                    sha[:12],
                    dest.name,
                )
            except OSError as exc:
                errors += 1
                log.warning("dataset sync copy fail sha=%s err=%s", sha[:12], exc)

    out = {
        "ok": errors == 0,
        "copied": copied,
        "skipped": skipped,
        "missing": missing,
        "removed_opposite": moved_away,
        "errors": errors,
        "poster_dir": str(p_dir),
        "non_poster_dir": str(n_dir),
    }
    log.info("poster-dataset-sync done %s", out)
    return out


def cmd_poster_retrain_from_review(
    settings,
    *,
    activate: bool = False,
    activate_force: bool = False,
) -> dict[str, Any]:
    """
    human → dataset sync 후 poster-train.
    activate=True 이면 게이트 통과 시 poster-activate (기본 수동).
    """
    sync = cmd_poster_dataset_sync(settings)
    if not sync.get("ok") and sync.get("errors", 0) > 0 and sync.get("copied", 0) == 0:
        return {"ok": False, "sync": sync, "error": "dataset sync failed"}

    from kakao_import.poster_train import cmd_poster_train

    train = cmd_poster_train()
    out: dict[str, Any] = {"ok": bool(train.get("ok")), "sync": sync, "train": train}
    if not train.get("ok"):
        out["error"] = train.get("error") or "poster-train failed"
        return out

    if activate:
        from kakao_import.poster_activate import cmd_poster_activate

        ver = str(train.get("version") or "")
        act = cmd_poster_activate(version=ver, force=activate_force)
        out["activate"] = act
        out["ok"] = bool(act.get("ok"))
        if not act.get("ok"):
            out["error"] = act.get("error") or "poster-activate failed"
    return out
