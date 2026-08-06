# [변경사유]: Phase1 설정 + Phase2 MERGE_MODE
"""런타임 설정."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

MergeMode = Literal["safe", "balanced", "auto"]


@dataclass(frozen=True)
class Settings:
    """로컬 collector 설정."""

    export_root: Path | None
    db_path: Path
    log_level: str
    match_tolerance_seconds: int
    group_text_max_gap_minutes: int
    different_sender_grace_seconds: int
    different_sender_max_chars: int
    merge_mode: MergeMode
    # [변경사유]: Phase 4.0 — 서버 MEDIA_SIMILAR_MAX_DISTANCE 기본(10)과 정렬
    similar_max_distance: int = 10
    # [변경사유]: 사진 앞 설명 귀속 창(초). 운영 합의 2분 이내
    group_text_before_max_seconds: int = 120


def load_settings(env_file: Path | None = None) -> Settings:
    """`.env` 로드."""
    load_dotenv(env_file or (PROJECT_ROOT / ".env"))
    raw_root = (os.getenv("KAKAO_EXPORT_ROOT") or "./input/raw").strip()
    export_root = Path(raw_root).expanduser()
    if not export_root.is_absolute():
        export_root = (PROJECT_ROOT / export_root).resolve()
    db_raw = (os.getenv("KAKAO_LOCAL_DB") or "./data/kakao_local.db").strip()
    db_path = Path(db_raw).expanduser()
    if not db_path.is_absolute():
        db_path = (PROJECT_ROOT / db_path).resolve()
    mode_raw = (os.getenv("MERGE_MODE") or "balanced").strip().lower()
    if mode_raw not in ("safe", "balanced", "auto"):
        mode_raw = "balanced"
    similar_max = int(os.getenv("SIMILAR_MAX_DISTANCE") or "10")
    if similar_max < 0:
        similar_max = 10
    return Settings(
        export_root=export_root,
        db_path=db_path,
        log_level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
        # [변경사유]: 운영 요청 — 이미지↔채팅 매칭 허용을 2분→1분으로 축소
        match_tolerance_seconds=int(os.getenv("MATCH_TOLERANCE_SECONDS") or "60"),
        group_text_max_gap_minutes=int(os.getenv("GROUP_TEXT_MAX_GAP_MINUTES") or "30"),
        different_sender_grace_seconds=int(
            os.getenv("DIFFERENT_SENDER_GRACE_SECONDS") or "120"
        ),
        different_sender_max_chars=int(os.getenv("DIFFERENT_SENDER_MAX_CHARS") or "80"),
        merge_mode=mode_raw,  # type: ignore[arg-type]
        similar_max_distance=similar_max,
        group_text_before_max_seconds=int(
            os.getenv("GROUP_TEXT_BEFORE_MAX_SECONDS") or "120"
        ),
    )
