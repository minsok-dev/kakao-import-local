# [변경사유]: I5 — upload run-report 단위 테스트
"""kakao-import run_report."""

from __future__ import annotations

import json
from pathlib import Path

from kakao_import.run_report import (
    build_admin_summary_ko_from_upload,
    write_upload_run_report,
)


def test_build_admin_summary_from_upload() -> None:
    text = build_admin_summary_ko_from_upload(
        {"OK": 2, "FAIL": 0, "ocr_queued": 1, "by_next": {"ocr_queued": 1}}
    )
    assert "성공" in text
    assert "OCR큐=1" in text


def test_write_upload_run_report(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    db.write_text("", encoding="utf-8")
    path = write_upload_run_report(
        db,
        summary={"OK": 1, "FAIL": 0, "ocr_queued": 1},
        rooms=["hongdae_bonita"],
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["rooms"] == ["hongdae_bonita"]
    assert "admin_summary_ko" in data
    assert path.name == "run-report.json"
    arch = db.parent / "runs" / data["run_id"] / "run-report.json"
    assert arch.is_file()
