# [변경사유]: 고정 test 오제외가 늘지 않을 때만 활성. 첫 모델은 false_exclude==0 일 때 허용
"""모델 활성화."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kakao_import.logging_util import get_logger
from kakao_import.poster_const import ACTIVE_MODEL_PATH, MODELS_DIR

log = get_logger(__name__)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def cmd_poster_activate(
    *,
    version: str,
    models_dir: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """active-model.json 교체. 게이트 실패 시 이전 활성 유지."""
    mdir = models_dir or MODELS_DIR
    ver = version.strip()
    meta_path = mdir / f"{ver}.json"
    joblib_path = mdir / f"{ver}.joblib"
    if not meta_path.is_file() or not joblib_path.is_file():
        return {"ok": False, "error": f"model files missing version={ver}"}
    new_meta = _read_json(meta_path) or {}
    new_fe = int(new_meta.get("false_exclude") or 0)
    active_path = mdir / "active-model.json" if models_dir else ACTIVE_MODEL_PATH
    prev = _read_json(active_path)
    prev_fe: int | None = None
    if prev:
        prev_ver = str(prev.get("version") or "")
        prev_meta = _read_json(mdir / f"{prev_ver}.json") or {}
        prev_fe = int(prev_meta.get("false_exclude") or 0)
    if not force:
        if prev_fe is not None and new_fe > prev_fe:
            log.warning(
                "poster-activate blocked new_false_exclude=%s prev=%s",
                new_fe,
                prev_fe,
            )
            return {
                "ok": False,
                "error": "false_exclude increased",
                "new_false_exclude": new_fe,
                "prev_false_exclude": prev_fe,
            }
        if prev is None and new_fe > 0:
            log.warning("poster-activate blocked first model false_exclude=%s", new_fe)
            return {
                "ok": False,
                "error": "first model false_exclude>0 — 포스터 오제외 있음. --force 또는 데이터 보강",
                "new_false_exclude": new_fe,
            }
    payload = {
        "version": ver,
        "joblib": joblib_path.name,
        "activated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "false_exclude": new_fe,
        "exclude_threshold": new_meta.get("exclude_threshold"),
        "poster_threshold": new_meta.get("poster_threshold"),
    }
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("poster-activate ok version=%s false_exclude=%s", ver, new_fe)
    return {"ok": True, **payload}
