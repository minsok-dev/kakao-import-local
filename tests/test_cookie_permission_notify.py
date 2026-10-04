# [변경사유]: 403 권한 실패 건수·카톡 안내 문구
"""cookie permission notify."""

from kakao_import.cookie_permission_notify import (
    count_insufficient_permission,
    cookie_permission_notice,
)


def test_count_insufficient_permission() -> None:
    rows = [
        {"ok": False, "error": "HTTP 403 INSUFFICIENT_PERMISSION"},
        {"ok": False, "error": "HTTP 500 boom"},
        {"ok": True, "error": "HTTP 403 INSUFFICIENT_PERMISSION"},
    ]
    assert count_insufficient_permission(rows) == 1


def test_notice_mentions_bat_and_cookie() -> None:
    text = cookie_permission_notice(9)
    assert "9건" in text
    assert "KAKAO_IMPORT_SESSION_COOKIE" in text
    assert "requeue_cookie_upload.bat" in text
