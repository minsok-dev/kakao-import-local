# [변경사유]: Similar 같은콘텐츠/부분 — 멤버 설명을 exact SHA 처럼 union
# [변경사유]: group_text 로드 시 단톡방·앞뒤 position 포함
"""업로드 캡션 조립 (DB → 본문)."""

from __future__ import annotations

import re
from typing import Any

from kakao_import.caption_sep import join_room_caption_blocks, join_texts_by_room
from kakao_import.logging_util import get_logger
from kakao_import.normalize import normalize_for_compare

log = get_logger(__name__)

# 사진 첨부 마커는 본문에 넣지 않음 (payload.is_attachment_marker_text 와 동일 규칙)
_ATTACH_MARKERS = frozenset({"사진", "동영상", "이모티콘"})
_PHOTO_N_RE = re.compile(r"사진\s+\d+장")


def _is_attachment_marker(text: str) -> bool:
    t = (text or "").strip()
    if not t or t in _ATTACH_MARKERS:
        return True
    if t.startswith(("파일:", "파일 :")):
        return True
    if _PHOTO_N_RE.fullmatch(t):
        return True
    return False


def _has_table(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (name,),
    ).fetchone()
    return bool(row)


def expand_exact_sha_photo_ids(conn, photo_ids: list[int]) -> list[int]:
    """같은 SHA 멤버(다른 방 복사본)까지 포함. 없는 행은 원본 id 유지."""
    ordered: list[int] = []
    seen: set[int] = set()
    for pid in photo_ids:
        n = int(pid or 0)
        if n > 0 and n not in seen:
            seen.add(n)
            ordered.append(n)
    if not ordered or not _has_table(conn, "exact_sha_member"):
        return ordered
    placeholders = ",".join("?" for _ in ordered)
    sql = f"""
        SELECT DISTINCT em2.photo_id AS photo_id
        FROM exact_sha_member em
        JOIN exact_sha_member em2 ON em2.group_id = em.group_id
        WHERE em.photo_id IN ({placeholders})
        ORDER BY em2.photo_id
        """
    log.info(
        "caption expand_exact_sha photo_n=%s sql=%s",
        len(ordered),
        " ".join(sql.split()),
    )
    rows = conn.execute(sql, tuple(ordered)).fetchall()
    for r in rows:
        n = int(r["photo_id"])
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    return ordered


def load_caption_parts_for_photos(conn, photo_ids: list[int]) -> list[dict[str, Any]]:
    """
    assignment → group_text → 텍스트 메시지.
    position=before|after 는 해당 이미지 그룹 첫 사진 seq 기준.
    """
    ids = [int(p) for p in photo_ids if int(p or 0) > 0]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    sql = f"""
        SELECT
          pm.id AS message_id,
          pm.chat_id AS chat_id,
          cs.room_title AS room_title,
          cs.rel_path AS chat_rel_path,
          pm.abs_time AS abs_time,
          pm.body_raw AS body_raw,
          pm.seq AS msg_seq,
          gt.seq_in_group AS seq_in_group,
          a.group_id AS group_id,
          (
            SELECT MIN(pm2.seq)
            FROM photo_message_assignment a2
            JOIN parsed_message pm2 ON pm2.id = a2.message_id
            WHERE a2.group_id = a.group_id
              AND a2.message_id IS NOT NULL
          ) AS first_photo_seq
        FROM photo_message_assignment a
        JOIN group_text gt ON gt.group_id = a.group_id
        JOIN parsed_message pm ON pm.id = gt.message_id
        JOIN chat_source cs ON cs.id = pm.chat_id
        WHERE a.photo_id IN ({placeholders})
          AND pm.msg_kind = 'text'
        ORDER BY pm.chat_id, gt.seq_in_group, pm.abs_time, pm.id
        """
    log.info(
        "caption load group_text photo_n=%s sql=%s",
        len(ids),
        " ".join(sql.split()),
    )
    rows = conn.execute(sql, tuple(ids)).fetchall()
    parts: list[dict[str, Any]] = []
    seen_msg: set[int] = set()
    for r in rows:
        mid = int(r["message_id"])
        if mid in seen_msg:
            continue
        body = str(r["body_raw"] or "")
        if not body.strip() or _is_attachment_marker(body):
            continue
        seen_msg.add(mid)
        first_seq = r["first_photo_seq"]
        msg_seq = r["msg_seq"]
        position: str | None = None
        if first_seq is not None and msg_seq is not None:
            position = "before" if int(msg_seq) < int(first_seq) else "after"
        parts.append(
            {
                "message_id": mid,
                "chat_id": int(r["chat_id"]) if r["chat_id"] is not None else None,
                "room_title": r["room_title"],
                "chat_rel_path": r["chat_rel_path"],
                "abs_time": r["abs_time"],
                "body_raw": body,
                "body_norm": None,
                "position": position,
                "seq_in_group": int(r["seq_in_group"] or 0),
            }
        )
    log.info(
        "caption load result photos=%s messages=%s",
        len(ids),
        len(parts),
    )
    return parts


def build_caption_for_photos(conn, photo_ids: list[int]) -> str:
    """
    사진들의 설명을 한 본문으로 합침.
    exact SHA 복사본(다른 방)도 포함. 동일 메시지·동일 정규화 본문은 1회만.
    """
    expanded = expand_exact_sha_photo_ids(conn, photo_ids)
    parts = load_caption_parts_for_photos(conn, expanded)
    if not parts:
        return ""
    return join_texts_by_room(parts)


def similar_union_member_ids(conn, sim: dict[str, Any]) -> list[int] | None:
    """
    캡션을 합칠 similar 멤버 photo_id.
    same_content = 그룹 전원, partial 묶음 = 같은 subgroup_key.
    합치지 않으면 None.
    """
    if not sim:
        return None
    decision = str(sim.get("decision") or "")
    gid = int(sim.get("group_id") or 0)
    if gid <= 0 or not _has_table(conn, "similar_image_member"):
        return None
    if decision == "same_content":
        sql = """
            SELECT photo_id FROM similar_image_member
            WHERE group_id = ? ORDER BY photo_id
            """
        log.info("caption similar members group_id=%s sql=%s", gid, " ".join(sql.split()))
        rows = conn.execute(sql, (gid,)).fetchall()
        return [int(r["photo_id"]) for r in rows]
    if decision == "partial":
        subgroup_key = str(sim.get("subgroup_key") or "")
        if subgroup_key.startswith("solo-"):
            return None
        if int(sim.get("subgroup_size") or 0) < 2:
            return None
        if not bool(sim.get("is_subgroup_rep")):
            return None
        sql = """
            SELECT photo_id FROM similar_image_member
            WHERE group_id = ? AND IFNULL(subgroup_key, '') = ?
            ORDER BY photo_id
            """
        log.info(
            "caption similar subgroup group_id=%s key=%s sql=%s",
            gid,
            subgroup_key,
            " ".join(sql.split()),
        )
        rows = conn.execute(sql, (gid, subgroup_key)).fetchall()
        return [int(r["photo_id"]) for r in rows]
    return None


def union_captions_for_photo_ids(conn, photo_ids: list[int]) -> str:
    """
    멤버마다 SHA 그룹 캡션을 만든 뒤, 정규화 중복을 빼고 ADD 구분선으로 연결.
    한 멤버 안(앞/뒤·여러 방)은 build_caption_for_photos 가 처리.
    """
    blocks: list[str] = []
    seen: set[str] = set()
    for pid in photo_ids:
        text = build_caption_for_photos(conn, [pid]).strip()
        if not text:
            continue
        key = normalize_for_compare(text)
        if not key or key in seen:
            continue
        seen.add(key)
        blocks.append(text)
    merged = join_room_caption_blocks(blocks)
    log.info(
        "caption union members=%s unique_blocks=%s chars=%s",
        len(photo_ids),
        len(blocks),
        len(merged),
    )
    return merged
