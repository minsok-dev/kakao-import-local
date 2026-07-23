# [변경사유]: Phase1 scan stub — 디렉터리·이미지 SHA만 (전문 파서 후속)
"""export 루트 스캔."""

from __future__ import annotations

from pathlib import Path

from kakao_import.db import connect
from kakao_import.hashutil import is_image_path, sha256_file
from kakao_import.logging_util import get_logger

log = get_logger(__name__)


def _room_key_from_path(root: Path, room_dir: Path) -> str:
    """루트 대비 상대 경로를 room_key로."""
    try:
        rel = room_dir.relative_to(root)
        key = rel.as_posix()
    except ValueError:
        key = room_dir.name
    return key or room_dir.name


def scan_export_root(db_path: Path, root: Path) -> dict[str, int]:
    """
    export 루트를 스캔해 source_room + local_media 적재.
    Phase1 stub: 하위 1-depth 디렉터리 = room, 그 안 이미지 재귀 수집.
    원본 파일은 읽기만 함 (삭제/이동 없음).
    """
    if not root.is_dir():
        raise NotADirectoryError(f"export root not found: {root}")

    rooms = 0
    media = 0
    skipped = 0

    # root 직하 폴더들 + root 자체에 이미지가 있으면 root room
    candidates: list[Path] = [p for p in sorted(root.iterdir()) if p.is_dir()]
    if not candidates:
        candidates = [root]

    with connect(db_path) as conn:
        for room_dir in candidates:
            room_key = _room_key_from_path(root, room_dir)
            log.info("scan room_key=%s path=%s", room_key, room_dir)
            cur = conn.execute(
                """
                INSERT INTO source_room (room_key, display_name, root_path, scanned_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(room_key) DO UPDATE SET
                  root_path = excluded.root_path,
                  scanned_at = excluded.scanned_at
                """,
                (room_key, room_dir.name, str(room_dir.resolve())),
            )
            # room id
            row = conn.execute(
                "SELECT id FROM source_room WHERE room_key = ?", (room_key,)
            ).fetchone()
            room_id = int(row["id"])
            rooms += 1
            _ = cur  # noqa: F841 — 변경 사유: executemany 대비 placeholder

            for path in room_dir.rglob("*"):
                if not path.is_file() or not is_image_path(path):
                    continue
                try:
                    digest = sha256_file(path)
                    rel = path.relative_to(room_dir).as_posix()
                    abs_path = str(path.resolve())
                    size = path.stat().st_size
                    conn.execute(
                        """
                        INSERT INTO local_media (
                          room_id, message_id, rel_path, abs_path, sha256, byte_size, mime_hint
                        ) VALUES (?, NULL, ?, ?, ?, ?, ?)
                        ON CONFLICT(room_id, rel_path) DO UPDATE SET
                          abs_path = excluded.abs_path,
                          sha256 = excluded.sha256,
                          byte_size = excluded.byte_size
                        """,
                        (room_id, rel, abs_path, digest, size, path.suffix.lower()),
                    )
                    media += 1
                    log.info(
                        "scan media room_id=%s rel=%s sha256=%s size=%s",
                        room_id,
                        rel,
                        digest[:12],
                        size,
                    )
                except OSError as exc:
                    skipped += 1
                    log.warning("scan skip path=%s err=%s", path, exc)

        conn.commit()

    summary = {"rooms": rooms, "media": media, "skipped": skipped}
    log.info("scan done %s", summary)
    return summary
