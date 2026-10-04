# [변경사유]: 쿠키 403 으로 failed_terminal 된 후보만 다시 업로드 대기열로 되돌린다
"""INSUFFICIENT_PERMISSION 후보를 state=new 로 되돌린다."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "kakao_local.db"

SQL = """
UPDATE upload_candidate
SET needs_rebuild = 1,
    state = 'new',
    state_reason = 'manual_requeue_cookie',
    attempt_count = 0,
    next_retry_at = NULL,
    last_error_code = NULL,
    last_error = NULL
WHERE state = 'failed_terminal'
  AND last_error_code = 'INSUFFICIENT_PERMISSION'
"""


def main() -> int:
    if not DB.is_file():
        print(f"db 없음: {DB}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(DB)
    try:
        cur = conn.execute(SQL)
        conn.commit()
        print(f"requeued {cur.rowcount}  (INSUFFICIENT_PERMISSION -> new)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
