# [변경사유]: 환경변수·경로 설정 — 서버 시크릿 미사용
"""설정 로더."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# 프로젝트 루트 (src/kakao_import/../..)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    """런타임 설정."""

    export_root: Path | None
    db_path: Path
    log_level: str


def load_settings(env_file: Path | None = None) -> Settings:
    """`.env` 로드 후 Settings 반환."""
    # [변경사유]: cwd 또는 프로젝트 루트의 .env
    load_dotenv(env_file or (PROJECT_ROOT / ".env"))
    raw_root = (os.getenv("KAKAO_EXPORT_ROOT") or "").strip()
    export_root = Path(raw_root).expanduser() if raw_root else None
    db_raw = (os.getenv("KAKAO_LOCAL_DB") or "./data/kakao_local.db").strip()
    db_path = Path(db_raw).expanduser()
    if not db_path.is_absolute():
        db_path = (PROJECT_ROOT / db_path).resolve()
    log_level = (os.getenv("LOG_LEVEL") or "INFO").upper()
    return Settings(export_root=export_root, db_path=db_path, log_level=log_level)
