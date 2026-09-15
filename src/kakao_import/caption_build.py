# [변경사유]: Similar 같은콘텐츠/부분 — 멤버 설명을 exact SHA 처럼 union
# [변경사유]: group_text 로드 시 단톡방·앞뒤 position 포함
# [변경사유]: 로컬 SQLite 캡션 조회 — 상관 서브쿼리 제거·장마다 SQL 반복 제거
"""업로드 캡션 조립 (DB → 본문)."""

from __future__ import annotations

import re
import time
from collections import defaultdict
from typing import Any

from kakao_import.caption_sep import join_room_caption_blocks, join_texts_by_room
from kakao_import.logging_util import get_logger
from kakao_import.normalize import normalize_for_compare

log = get_logger(__name__)

# 사진 첨부 마커는 본문에 넣지 않음 (payload.is_attachment_marker_text 와 동일 규칙)
_ATTACH_MARKERS = frozenset({"사진", "동영상", "이모티콘"})
_PHOTO_N_RE = re.compile(r"사진\s+\d+장")
_SEED_TABLE = "tmp_caption_seed"


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


def _ensure_indexes(conn) -> None:
    # [변경사유]: upload-only 경로도 기존 DB에 인덱스 생성 (run/init_schema 없이)
    from kakao_import.db import ensure_caption_query_indexes

    ensure_caption_query_indexes(conn)


def _unique_photo_ids(photo_ids: list[int]) -> list[int]:
    ordered: list[int] = []
    seen: set[int] = set()
    for pid in photo_ids:
        n = int(pid or 0)
        if n > 0 and n not in seen:
            seen.add(n)
            ordered.append(n)
    return ordered


def _fill_seed_ids(conn, ids: list[int]) -> None:
    conn.execute(f"DROP TABLE IF EXISTS {_SEED_TABLE}")
    conn.execute(
        f"CREATE TEMP TABLE {_SEED_TABLE} (photo_id INTEGER PRIMARY KEY NOT NULL)"
    )
    if ids:
        conn.executemany(
            f"INSERT OR IGNORE INTO {_SEED_TABLE}(photo_id) VALUES (?)",
            [(i,) for i in ids],
        )


def _drop_seed(conn) -> None:
    conn.execute(f"DROP TABLE IF EXISTS {_SEED_TABLE}")


def expand_exact_sha_photo_ids(conn, photo_ids: list[int]) -> list[int]:
    """같은 SHA 멤버(다른 방 복사본)까지 포함. 없는 행은 원본 id 유지."""
    mapping = expand_exact_sha_member_map(conn, photo_ids)
    ordered: list[int] = []
    seen: set[int] = set()
    for pid in _unique_photo_ids(photo_ids):
        for n in mapping.get(pid, [pid]):
            if n not in seen:
                seen.add(n)
                ordered.append(n)
    return ordered


def expand_exact_sha_member_map(conn, photo_ids: list[int]) -> dict[int, list[int]]:
    """입력 photo_id → 같은 SHA 멤버 id 목록(자신 포함)."""
    ordered = _unique_photo_ids(photo_ids)
    mapping: dict[int, list[int]] = {pid: [pid] for pid in ordered}
    if not ordered or not _has_table(conn, "exact_sha_member"):
        return mapping
    _ensure_indexes(conn)
    t0 = time.perf_counter()
    _fill_seed_ids(conn, ordered)
    try:
        rows = conn.execute(
            f"""
            SELECT em.photo_id AS src_id, em2.photo_id AS member_id
            FROM exact_sha_member em
            JOIN {_SEED_TABLE} s ON s.photo_id = em.photo_id
            JOIN exact_sha_member em2 ON em2.group_id = em.group_id
            ORDER BY em.photo_id, em2.photo_id
            """
        ).fetchall()
    finally:
        _drop_seed(conn)
    for r in rows:
        src = int(r["src_id"])
        member = int(r["member_id"])
        bucket = mapping.setdefault(src, [src])
        if member not in bucket:
            bucket.append(member)
    log.info(
        "caption expand_exact_sha photo_n=%s member_rows=%s ms=%.1f",
        len(ordered),
        len(rows),
        (time.perf_counter() - t0) * 1000.0,
    )
    return mapping


def _part_from_row(r: Any) -> dict[str, Any] | None:
    mid = int(r["message_id"])
    body = str(r["body_raw"] or "")
    if not body.strip() or _is_attachment_marker(body):
        return None
    first_seq = r["first_photo_seq"]
    msg_seq = r["msg_seq"]
    position: str | None = None
    if first_seq is not None and msg_seq is not None:
        position = "before" if int(msg_seq) < int(first_seq) else "after"
    return {
        "message_id": mid,
        "chat_id": int(r["chat_id"]) if r["chat_id"] is not None else None,
        "room_title": r["room_title"],
        "chat_rel_path": r["chat_rel_path"],
        "abs_time": r["abs_time"],
        "body_raw": body,
        "body_norm": None,
        "position": position,
        "seq_in_group": int(r["seq_in_group"] or 0),
        "photo_id": int(r["photo_id"]) if r["photo_id"] is not None else 0,
        "sender": "",
    }


def _fetch_photo_origins(conn, ids: list[int]) -> dict[int, dict[str, Any]]:
    """사진 메시지 기준 단톡방·대화명. group_text 가 없어도 확인용 캡션에 쓴다."""
    if not ids:
        return {}
    _ensure_indexes(conn)
    _fill_seed_ids(conn, ids)
    try:
        rows = conn.execute(
            f"""
            SELECT
              a.photo_id AS photo_id,
              a.message_id AS photo_message_id,
              pm.sender AS sender,
              COALESCE(pm.chat_id, ig.chat_id) AS chat_id,
              cs.room_title AS room_title,
              cs.rel_path AS chat_rel_path
            FROM photo_message_assignment a
            JOIN {_SEED_TABLE} s ON s.photo_id = a.photo_id
            LEFT JOIN parsed_message pm ON pm.id = a.message_id
            LEFT JOIN image_group ig ON ig.id = a.group_id
            LEFT JOIN chat_source cs
              ON cs.id = COALESCE(pm.chat_id, ig.chat_id)
            """
        ).fetchall()
    finally:
        _drop_seed(conn)
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        pid = int(r["photo_id"] or 0)
        if pid <= 0:
            continue
        out[pid] = {
            "photo_id": pid,
            "photo_message_id": int(r["photo_message_id"] or 0),
            "sender": str(r["sender"] or "").strip(),
            "chat_id": int(r["chat_id"]) if r["chat_id"] is not None else None,
            "room_title": r["room_title"],
            "chat_rel_path": r["chat_rel_path"],
        }
    return out


def _origin_stub_part(origin: dict[str, Any]) -> dict[str, Any]:
    pid = int(origin.get("photo_id") or 0)
    msg_id = int(origin.get("photo_message_id") or 0)
    return {
        "message_id": msg_id if msg_id > 0 else -pid,
        "chat_id": origin.get("chat_id"),
        "room_title": origin.get("room_title"),
        "chat_rel_path": origin.get("chat_rel_path"),
        "abs_time": None,
        "body_raw": "",
        "body_norm": None,
        "position": None,
        "seq_in_group": 0,
        "photo_id": pid,
        "sender": str(origin.get("sender") or "").strip(),
    }


def _stamp_origin_sender(
    parts: list[dict[str, Any]], origins: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """group_text 파트에 사진 올린이 대화명을 붙이고, 본문 없는 방은 스텁을 추가."""
    seen_photos = {int(p.get("photo_id") or 0) for p in parts}
    for part in parts:
        origin = origins.get(int(part.get("photo_id") or 0))
        if not origin:
            continue
        sender = str(origin.get("sender") or "").strip()
        if sender:
            part["sender"] = sender
    for pid, origin in origins.items():
        if pid in seen_photos:
            continue
        if not origin.get("sender") and not origin.get("room_title") and not origin.get(
            "chat_rel_path"
        ):
            continue
        parts.append(_origin_stub_part(origin))
    return parts


def _fetch_caption_rows(conn, ids: list[int]) -> list[Any]:
    """
    assignment → group_text → 텍스트.
    first_photo_seq 는 대상 그룹만 한 번 집계 (행마다 상관 서브쿼리 금지).
    """
    if not ids:
        return []
    _ensure_indexes(conn)
    t0 = time.perf_counter()
    _fill_seed_ids(conn, ids)
    try:
        rows = conn.execute(
            f"""
            WITH groups AS (
              SELECT DISTINCT a.group_id AS group_id
              FROM photo_message_assignment a
              JOIN {_SEED_TABLE} s ON s.photo_id = a.photo_id
              WHERE a.group_id IS NOT NULL
            ),
            first_seq AS (
              SELECT a2.group_id AS group_id, MIN(pm2.seq) AS first_photo_seq
              FROM photo_message_assignment a2
              JOIN groups g ON g.group_id = a2.group_id
              JOIN parsed_message pm2 ON pm2.id = a2.message_id
              WHERE a2.message_id IS NOT NULL
              GROUP BY a2.group_id
            )
            SELECT
              a.photo_id AS photo_id,
              pm.id AS message_id,
              pm.chat_id AS chat_id,
              cs.room_title AS room_title,
              cs.rel_path AS chat_rel_path,
              pm.abs_time AS abs_time,
              pm.body_raw AS body_raw,
              pm.seq AS msg_seq,
              gt.seq_in_group AS seq_in_group,
              a.group_id AS group_id,
              fs.first_photo_seq AS first_photo_seq
            FROM photo_message_assignment a
            JOIN {_SEED_TABLE} s ON s.photo_id = a.photo_id
            JOIN group_text gt ON gt.group_id = a.group_id
            JOIN parsed_message pm ON pm.id = gt.message_id
            JOIN chat_source cs ON cs.id = pm.chat_id
            LEFT JOIN first_seq fs ON fs.group_id = a.group_id
            WHERE pm.msg_kind = 'text'
            ORDER BY pm.chat_id, gt.seq_in_group, pm.abs_time, pm.id
            """
        ).fetchall()
    finally:
        _drop_seed(conn)
    log.info(
        "caption load group_text photo_n=%s rows=%s ms=%.1f",
        len(ids),
        len(rows),
        (time.perf_counter() - t0) * 1000.0,
    )
    return list(rows)


def load_caption_parts_for_photos(conn, photo_ids: list[int]) -> list[dict[str, Any]]:
    """
    assignment → group_text → 텍스트 메시지.
    position=before|after 는 해당 이미지 그룹 첫 사진 seq 기준.
    """
    ids = _unique_photo_ids(photo_ids)
    if not ids:
        return []
    parts: list[dict[str, Any]] = []
    seen_msg: set[int] = set()
    for r in _fetch_caption_rows(conn, ids):
        part = _part_from_row(r)
        if part is None:
            continue
        mid = int(part["message_id"])
        if mid in seen_msg:
            continue
        seen_msg.add(mid)
        parts.append(part)
    origins = _fetch_photo_origins(conn, ids)
    parts = _stamp_origin_sender(parts, origins)
    log.info(
        "caption load result photos=%s messages=%s origins=%s",
        len(ids),
        len(parts),
        len(origins),
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


def build_captions_by_photo_ids(conn, photo_ids: list[int]) -> dict[int, str]:
    """
    여러 대표 사진 캡션을 SQL 2회(SHA 확장 + group_text)로 만든다.
    SHA 그룹끼리는 본문을 섞지 않는다.
    """
    ordered = _unique_photo_ids(photo_ids)
    if not ordered:
        return {}
    mapping = expand_exact_sha_member_map(conn, ordered)
    all_ids: list[int] = []
    seen_all: set[int] = set()
    for pid in ordered:
        for n in mapping.get(pid, [pid]):
            if n not in seen_all:
                seen_all.add(n)
                all_ids.append(n)
    rows = _fetch_caption_rows(conn, all_ids)
    origins = _fetch_photo_origins(conn, all_ids)
    parsed: list[tuple[int, dict[str, Any] | None]] = []
    by_photo: dict[int, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        pid = int(r["photo_id"] or 0)
        parsed.append((pid, _part_from_row(r)))
        by_photo[pid].append(i)
    out: dict[int, str] = {}
    for src in ordered:
        members = mapping.get(src, [src])
        idxs: list[int] = []
        for mid in members:
            idxs.extend(by_photo.get(mid, []))
        idxs.sort()
        parts: list[dict[str, Any]] = []
        seen_msg: set[int] = set()
        for i in idxs:
            _pid, part = parsed[i]
            if part is None:
                continue
            msg_id = int(part["message_id"])
            if msg_id in seen_msg:
                continue
            seen_msg.add(msg_id)
            parts.append(part)
        member_origins = {
            pid: origins[pid] for pid in members if pid in origins
        }
        parts = _stamp_origin_sender(parts, member_origins)
        out[src] = join_texts_by_room(parts) if parts else ""
    log.info(
        "caption batch photos=%s unique_members=%s nonempty=%s",
        len(ordered),
        len(all_ids),
        sum(1 for v in out.values() if v),
    )
    return out


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
        rows = conn.execute(
            """
            SELECT photo_id FROM similar_image_member
            WHERE group_id = ? ORDER BY photo_id
            """,
            (gid,),
        ).fetchall()
        log.info("caption similar members group_id=%s n=%s", gid, len(rows))
        return [int(r["photo_id"]) for r in rows]
    if decision == "partial":
        subgroup_key = str(sim.get("subgroup_key") or "")
        if subgroup_key.startswith("solo-"):
            return None
        if int(sim.get("subgroup_size") or 0) < 2:
            return None
        if not bool(sim.get("is_subgroup_rep")):
            return None
        rows = conn.execute(
            """
            SELECT photo_id FROM similar_image_member
            WHERE group_id = ? AND IFNULL(subgroup_key, '') = ?
            ORDER BY photo_id
            """,
            (gid, subgroup_key),
        ).fetchall()
        log.info(
            "caption similar subgroup group_id=%s key=%s n=%s",
            gid,
            subgroup_key,
            len(rows),
        )
        return [int(r["photo_id"]) for r in rows]
    return None


def compose_union_caption(captions: dict[int, str], photo_ids: list[int]) -> str:
    """이미 만든 장별 캡션을 ADD 구분선으로 연결 (정규화 중복 제외)."""
    blocks: list[str] = []
    seen: set[str] = set()
    for pid in photo_ids:
        text = str(captions.get(int(pid), "") or "").strip()
        if not text:
            continue
        key = normalize_for_compare(text)
        if not key or key in seen:
            continue
        seen.add(key)
        blocks.append(text)
    return join_room_caption_blocks(blocks)


def union_captions_for_photo_ids(conn, photo_ids: list[int]) -> str:
    """
    멤버마다 SHA 그룹 캡션을 만든 뒤, 정규화 중복을 빼고 ADD 구분선으로 연결.
    한 멤버 안(앞/뒤·여러 방)은 build_caption_for_photos 가 처리.
    """
    captions = build_captions_by_photo_ids(conn, list(photo_ids))
    merged = compose_union_caption(captions, list(photo_ids))
    log.info(
        "caption union members=%s chars=%s",
        len(photo_ids),
        len(merged),
    )
    return merged
