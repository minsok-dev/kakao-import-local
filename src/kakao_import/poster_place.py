# [변경사유]: non_poster 만 방/non_posters 로 옮기고, poster·uncertain 은 photos 로 되돌린다
"""분류 상태에 맞춰 사진 파일을 photos / non_posters 에 둔다."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from kakao_import.logging_util import get_logger
from kakao_import.poster_const import STATUS_NON_POSTER

log = get_logger(__name__)

MEDIA_PHOTOS = "photos"
MEDIA_NON_POSTERS = "non_posters"
_MEDIA_DIRS = (MEDIA_PHOTOS, MEDIA_NON_POSTERS, "chats")


def split_media_rel(rel: str) -> tuple[str | None, str, str] | None:
    """
    gangnam/photos/a.jpg → (gangnam, photos, a.jpg)
    photos/a.jpg → (None, photos, a.jpg)
    그 외 경로는 None.
    """
    parts = (rel or "").replace("\\", "/").strip("/").split("/")
    if len(parts) == 2 and parts[0] in _MEDIA_DIRS:
        return None, parts[0], parts[1]
    if len(parts) == 3 and parts[1] in _MEDIA_DIRS:
        return parts[0], parts[1], parts[2]
    return None


def join_media_rel(room: str | None, media: str, name: str) -> str:
    """방 유무에 맞춰 상대경로를 만든다."""
    if room:
        return f"{room}/{media}/{name}"
    return f"{media}/{name}"


def bucket_for_status(status: str) -> str:
    """non_poster 만 별도 폴더. poster·uncertain 은 업로드 후보 폴더."""
    if status == STATUS_NON_POSTER:
        return MEDIA_NON_POSTERS
    return MEDIA_PHOTOS


def place_classified_photo(
    conn: sqlite3.Connection,
    root: Path,
    photo_id: int,
    rel: str,
    status: str,
) -> str:
    """
    파일을 상태 폴더로 옮기고 photo_file·poster_classify 의 rel_path 를 맞춘다.
    목적지에 다른 파일이 있으면 덮어쓰지 않고 원래 경로를 반환한다.
    """
    current = (rel or "").replace("\\", "/")
    parsed = split_media_rel(current)
    if parsed is None:
        log.info("poster-place skip unknown layout photo_id=%s rel=%s", photo_id, current)
        return current
    room, _media, name = parsed
    new_rel = join_media_rel(room, bucket_for_status(status), name)
    if new_rel == current:
        return new_rel

    clash_sql = "SELECT id FROM photo_file WHERE rel_path = ? AND id != ?"
    clash = conn.execute(clash_sql, (new_rel, photo_id)).fetchone()
    log.info(
        "poster-place clash sql=%s photo_id=%s new_rel=%s hit=%s",
        clash_sql,
        photo_id,
        new_rel,
        bool(clash),
    )
    if clash:
        log.warning(
            "poster-place skip rel clash photo_id=%s dest=%s other_id=%s",
            photo_id,
            new_rel,
            clash["id"],
        )
        return current

    src = root / current
    dst = root / new_rel
    if dst.exists() and src.is_file():
        log.warning("poster-place skip dest exists photo_id=%s dest=%s", photo_id, new_rel)
        return current
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        log.info("poster-place moved photo_id=%s from=%s to=%s", photo_id, current, new_rel)
    elif dst.is_file():
        log.info("poster-place already at dest photo_id=%s rel=%s", photo_id, new_rel)
    else:
        log.warning(
            "poster-place missing both photo_id=%s rel=%s dest=%s",
            photo_id,
            current,
            new_rel,
        )
        return current

    photo_sql = "UPDATE photo_file SET rel_path = ?, updated_at = datetime('now') WHERE id = ?"
    conn.execute(photo_sql, (new_rel, photo_id))
    log.info("poster-place photo sql=%s photo_id=%s rel=%s", photo_sql, photo_id, new_rel)
    classify_sql = "UPDATE poster_classify SET rel_path = ? WHERE photo_id = ?"
    conn.execute(classify_sql, (new_rel, photo_id))
    log.info(
        "poster-place classify sql=%s photo_id=%s rel=%s",
        classify_sql,
        photo_id,
        new_rel,
    )
    return new_rel
