# [변경사유]: Phase1 설정 — 매칭 tolerance·그룹 텍스트 경계 기본값
"""런타임 설정."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    """로컬 collector 설정."""

    export_root: Path | None
    db_path: Path
    log_level: str
    # 분 단위 불일치 시 유일 후보 허용 초
    match_tolerance_seconds: int
    # 사진 그룹 후 설명 최대 간격(분)
    group_text_max_gap_minutes: int
    # 다른 발신자 짧은 응답 허용 초 (초과·긴 문장이면 그룹 종료)
    different_sender_grace_seconds: int
    different_sender_max_chars: int


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
    return Settings(
        export_root=export_root,
        db_path=db_path,
        log_level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
        match_tolerance_seconds=int(os.getenv("MATCH_TOLERANCE_SECONDS") or "120"),
        group_text_max_gap_minutes=int(os.getenv("GROUP_TEXT_MAX_GAP_MINUTES") or "30"),
        different_sender_grace_seconds=int(
            os.getenv("DIFFERENT_SENDER_GRACE_SECONDS") or "120"
        ),
        different_sender_max_chars=int(os.getenv("DIFFERENT_SENDER_MAX_CHARS") or "80"),
    )
