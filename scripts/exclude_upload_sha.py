# [변경사유]: PowerShell 따옴표 이슈 회피 — 업로드 제외 헬퍼
"""대표 사진을 excluded_from_upload=1 로 표시."""

from __future__ import annotations

import argparse
import sys

from kakao_import.config import load_settings
from kakao_import.db import connect


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--sha-prefix",
        required=True,
        help="sha256 앞자리 (예: 006f0c3e391a)",
    )
    args = p.parse_args()
    prefix = args.sha_prefix.strip().lower()
    if len(prefix) < 8:
        print("sha-prefix 너무 짧음", file=sys.stderr)
        return 2

    settings = load_settings()
    with connect(settings.db_path) as conn:
        cur = conn.execute(
            """
            UPDATE exact_sha_member
            SET excluded_from_upload = 1
            WHERE is_representative = 1
              AND group_id = (
                SELECT id FROM exact_sha_group
                WHERE lower(sha256) LIKE ? || '%'
                LIMIT 1
              )
            """,
            (prefix,),
        )
        conn.commit()
        print(f"excluded rows={cur.rowcount} sha_prefix={prefix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
