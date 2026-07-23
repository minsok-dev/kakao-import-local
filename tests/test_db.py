# [변경사유]: Phase0 — init-db 스모크
from pathlib import Path

from kakao_import.db import init_schema, status_counts


def test_init_schema(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    init_schema(db)
    counts = status_counts(db)
    assert counts["db_exists"] == 1
    assert counts["source_room"] == 0
    assert counts["local_media"] == 0
