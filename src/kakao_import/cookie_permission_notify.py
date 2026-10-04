# [변경사유]: 업로드 403 INSUFFICIENT_PERMISSION — 카톡으로 쿠키 갱신·재업로드 bat 안내
"""세션 쿠키 권한 실패 시 관리자 카톡 알림."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from kakao_import.config import PROJECT_ROOT
from kakao_import.logging_util import get_logger

log = get_logger(__name__)

BAT_NAME = "requeue_cookie_upload.bat"


def count_insufficient_permission(results: list[dict]) -> int:
    """HTTP 403 권한 부족으로 실패한 업로드 건수."""
    n = 0
    for row in results:
        if row.get("ok"):
            continue
        err = str(row.get("error") or "")
        if "INSUFFICIENT_PERMISSION" in err:
            n += 1
    return n


def cookie_permission_notice(fail_count: int) -> str:
    """카톡 본문. 쿠키 값·경로는 안내만 하고 비밀은 넣지 않는다."""
    bat = PROJECT_ROOT / BAT_NAME
    return (
        "[카카오업로드] 쿠키 만료\n"
        f"세션 쿠키 권한 오류(403)로 {fail_count}건이 업로드되지 않았습니다.\n"
        "자동으로 다시 올리지 않습니다.\n"
        "1) kakao-import-local .env 의 KAKAO_IMPORT_SESSION_COOKIE 를\n"
        "   슈퍼관리자 로그인 쿠키로 바꾸세요.\n"
        f"2) 그다음 이 파일을 실행하세요.\n{bat}"
    )


def notify_cookie_permission_failure(fail_count: int) -> None:
    """kakao-pc-collect notify-test 로 1통 전송. 실패해도 업로드 결과는 유지."""
    if fail_count <= 0:
        return
    text = cookie_permission_notice(fail_count)
    root = Path(
        os.getenv("KAKAO_PC_COLLECT_ROOT")
        or (PROJECT_ROOT.parent / "kakao-pc-collect")
    ).resolve()
    exe = root / ".venv" / "Scripts" / "kakao-pc-collect.exe"
    if exe.is_file():
        cmd = [str(exe), "notify-test", "--text", text]
    else:
        py = root / ".venv" / "Scripts" / "python.exe"
        if not py.is_file():
            log.warning(
                "cookie-notify skip — kakao-pc-collect 없음 root=%s",
                root,
            )
            return
        cmd = [str(py), "-m", "kakao_pc_collect", "notify-test", "--text", text]
    log.info(
        "cookie-notify send fail_count=%s collect_root=%s",
        fail_count,
        root,
    )
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except Exception as exc:  # noqa: BLE001 — 알림 실패로 업로드 실패로 만들지 않음
        log.warning("cookie-notify failed err=%s", exc)
        return
    if proc.returncode != 0:
        tail = (proc.stdout or "") + (proc.stderr or "")
        log.warning(
            "cookie-notify exit=%s tail=%s",
            proc.returncode,
            tail[-500:],
        )
    else:
        log.info("cookie-notify sent fail_count=%s", fail_count)
