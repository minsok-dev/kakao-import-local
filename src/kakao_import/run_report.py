# [변경사유]: I5 — upload 단독 실행 시에도 run-report / admin_summary 기록
"""upload 결과 → 관리자 요약 리포트."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kakao_import.logging_util import get_logger

log = get_logger(__name__)


def build_admin_summary_ko_from_upload(summary: dict[str, Any]) -> str:
    """upload summary 만으로 관리자 요약."""
    ok_n = int(summary.get("OK") or summary.get("ok") or 0)
    fail_n = int(summary.get("FAIL") or summary.get("fail") or 0)
    ocr = summary.get("ocr_queued")
    err = summary.get("error")
    status = "실패" if err or (fail_n and not ok_n) else "성공"
    lines = [
        f"[카카오업로드] {status}",
        f"HTTP OK={ok_n} FAIL={fail_n} OCR큐={ocr if ocr is not None else '-'}",
    ]
    by_next = summary.get("by_next")
    if isinstance(by_next, dict) and by_next:
        parts = [f"{k}={v}" for k, v in sorted(by_next.items())]
        lines.append("next: " + ", ".join(parts))
    if err:
        lines.append(f"사유: {err}")
    lines.append(
        f"시각: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')[:19]}"
    )
    return "\n".join(lines)


def write_upload_run_report(
    db_path: Path,
    *,
    summary: dict[str, Any],
    rooms: list[str] | None = None,
) -> Path:
    """data/run-report.json 갱신 (upload 단독 경로)."""
    err = summary.get("error")
    cookie_bad = str(err or "").upper() in (
        "UPLOAD_RUN_LOCK_BUSY",
    ) or "cookie" in str(err or "").lower()
    # 쿠키 미설정 RuntimeError 는 summary 에 안 올 수 있음 — FAIL 과 empty cookie 힌트
    report = {
        "run_id": datetime.now().strftime("%Y%m%d-%H%M%S"),
        "finished_at": datetime.now(timezone.utc)
        .astimezone()
        .isoformat(timespec="seconds"),
        "ok": err is None and int(summary.get("FAIL") or summary.get("fail") or 0) == 0,
        "exit_reason": str(err or "success"),
        "rooms": rooms or [],
        "upload": {
            "http_ok": summary.get("OK", summary.get("ok")),
            "http_fail": summary.get("FAIL", summary.get("fail")),
            "ocr_queued": summary.get("ocr_queued"),
            "by_next": summary.get("by_next"),
            "skipped_uploaded": summary.get("skipped_uploaded"),
        },
        "cookie": {"ok": not cookie_bad},
        "error": err,
        "source": "kakao-import-upload",
    }
    report["admin_summary_ko"] = build_admin_summary_ko_from_upload(summary)
    path = db_path.parent / "run-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log.info(
        "upload run-report written path=%s ok=%s", path, report.get("ok")
    )
    return path
