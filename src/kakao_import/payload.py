# [변경사유]: Phase3 — Import payload 생성 (인접/merged 메시지만, 절대경로 금지)
"""서버 Import용 payload 빌더."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from kakao_import.config import PROJECT_ROOT, Settings
from kakao_import.db import connect
from kakao_import.logging_util import get_logger
from kakao_import.similar_policy import upload_policy_for_decision

log = get_logger(__name__)

# [변경사유]: 서버 KAKAO_IMPORT_MAX_INGRESS_BYTES(50MiB) 와 맞춤 — 초과만 전송 전 거부
MAX_UPLOAD_FILE_BYTES = 50 * 1024 * 1024
# [변경사유]: 예전 15MiB 하드 스킵 제거 확인용 (회귀 테스트)
LEGACY_HARD_SKIP_BYTES = 15 * 1024 * 1024


def exceeds_ingress_limit(byte_size: int) -> bool:
    """수신 상한(50MiB) 초과 여부. 15MiB 초과는 허용."""
    return int(byte_size) > MAX_UPLOAD_FILE_BYTES

INSTANCE_FILE = PROJECT_ROOT / "data" / "client_instance_id.txt"


def ensure_client_instance_id() -> str:
    """로컬 인스턴스 UUID (쿠키 아님, 디스크에 UUID만)."""
    INSTANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if INSTANCE_FILE.is_file():
        raw = INSTANCE_FILE.read_text(encoding="utf-8").strip()
        if raw:
            return raw
    value = str(uuid.uuid4())
    INSTANCE_FILE.write_text(value + "\n", encoding="utf-8")
    log.info("client_instance_id created")
    return value


def _idempotency_key(client_id: str, sha256: str, local_item_id: str) -> str:
    raw = f"{client_id}|{sha256}|{local_item_id}".encode()
    return hashlib.sha256(raw).hexdigest()


def _fingerprint_message(sent_at: str, text: str) -> str:
    return hashlib.sha256(f"{sent_at}|{text}".encode()).hexdigest()


def is_attachment_marker_text(text: str) -> bool:
    """
    카카오 첨부 마커만 있는 본문인지.
    [변경사유]: '사진'/'사진 N장' 등은 caption·matched_messages 에 넣지 않음
    """
    t = (text or "").strip()
    if not t:
        return True
    if t in ("사진", "동영상", "이모티콘"):
        return True
    if re.fullmatch(r"사진\s+\d+장", t):
        return True
    if t.startswith(("파일:", "파일 :")):
        return True
    return False


def _has_table(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (name,),
    ).fetchone()
    return bool(row)


def _load_similar_membership(
    conn,
    photo_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """photo_id -> similar membership/policy view."""
    if not photo_ids:
        return {}
    if not _has_table(conn, "similar_image_group") or not _has_table(
        conn, "similar_image_member"
    ):
        return {}
    placeholders = ",".join("?" for _ in photo_ids)
    rows = conn.execute(
        f"""
        SELECT
          m.photo_id,
          g.id AS group_id,
          g.group_key,
          g.decision,
          g.representative_photo_id,
          g.member_count,
          m.subgroup_key,
          m.is_subgroup_rep,
          m.is_representative,
          (
            SELECT COUNT(*)
            FROM similar_image_member sm2
            WHERE sm2.group_id = m.group_id
              AND IFNULL(sm2.subgroup_key, '') = IFNULL(m.subgroup_key, '')
          ) AS subgroup_size
        FROM similar_image_member m
        JOIN similar_image_group g ON g.id = m.group_id
        WHERE m.photo_id IN ({placeholders})
        """,
        tuple(photo_ids),
    ).fetchall()
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        pid = int(r["photo_id"])
        decision = str(r["decision"] or "deferred")
        out[pid] = {
            "group_id": int(r["group_id"]),
            "group_key": str(r["group_key"]),
            "decision": decision,
            "upload_policy": upload_policy_for_decision(decision),
            "representative_photo_id": (
                int(r["representative_photo_id"])
                if r["representative_photo_id"] is not None
                else None
            ),
            "member_count": int(r["member_count"] or 0),
            "subgroup_key": r["subgroup_key"],
            "subgroup_size": int(r["subgroup_size"] or 0),
            "is_subgroup_rep": bool(r["is_subgroup_rep"]),
            "is_representative": bool(r["is_representative"]),
        }
    return out


def _load_grouped_photo_shape(
    conn,
    photo_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """동일 시각대 매칭 그룹 메타(향후 main+sub 후보 구조 준비)."""
    if not photo_ids:
        return {}
    placeholders = ",".join("?" for _ in photo_ids)
    rows = conn.execute(
        f"""
        SELECT
          a.photo_id,
          a.group_id,
          a.slot_index,
          ig.group_key,
          ig.chat_id,
          (
            SELECT COUNT(*)
            FROM photo_message_assignment ax
            WHERE ax.group_id = a.group_id
              AND ax.message_id IS NOT NULL
          ) AS slot_count
        FROM photo_message_assignment a
        JOIN image_group ig ON ig.id = a.group_id
        WHERE a.photo_id IN ({placeholders})
          AND a.group_id IS NOT NULL
          AND a.message_id IS NOT NULL
        """,
        tuple(photo_ids),
    ).fetchall()
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        out[int(r["photo_id"])] = {
            "group_id": int(r["group_id"]),
            "group_key": str(r["group_key"]),
            "chat_id": int(r["chat_id"]),
            "slot_index": int(r["slot_index"]),
            "slot_count": int(r["slot_count"] or 0),
            "bundle_candidate": int(r["slot_count"] or 0) >= 2,
        }
    return out


def _apply_similar_policy(
    items: list[dict[str, Any]],
    similar_map: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """similar decision 기준으로 업로드 후보 필터."""
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    deferred_groups: dict[int, dict[str, Any]] = {}

    for item in items:
        meta = item.get("_meta") if isinstance(item.get("_meta"), dict) else {}
        pid = int(meta.get("photo_id") or 0)
        sim = similar_map.get(pid)
        if not sim:
            kept.append(item)
            continue

        decision = str(sim["decision"])
        policy = str(sim["upload_policy"])
        reason: str | None = None
        if decision == "deferred":
            reason = "similar_deferred"
            gid = int(sim["group_id"])
            deferred_groups.setdefault(
                gid,
                {
                    "group_id": gid,
                    "group_key": sim["group_key"],
                    "decision": decision,
                    "upload_policy": policy,
                    "representative_photo_id": sim["representative_photo_id"],
                    "member_count": sim["member_count"],
                },
            )
        elif decision == "same_content":
            if pid != int(sim["representative_photo_id"] or 0):
                reason = "similar_non_representative"
        elif decision == "partial":
            subgroup_key = str(sim.get("subgroup_key") or "")
            subgroup_size = int(sim.get("subgroup_size") or 0)
            if subgroup_key.startswith("solo-"):
                reason = None
            elif subgroup_size >= 2 and bool(sim.get("is_subgroup_rep")):
                reason = None
            else:
                reason = "similar_partial_non_representative"

        if reason:
            skipped.append(
                {
                    "photo_id": pid,
                    "rel": item.get("rel_path"),
                    "local_item_id": item.get("local_item_id"),
                    "decision": decision,
                    "upload_policy": policy,
                    "group_id": sim["group_id"],
                    "group_key": sim["group_key"],
                    "reason": reason,
                    "representative_photo_id": sim["representative_photo_id"],
                    "subgroup_key": sim.get("subgroup_key"),
                }
            )
            continue

        kept.append(item)

    return {
        "items": kept,
        "skipped": skipped,
        "deferred_groups": sorted(
            deferred_groups.values(), key=lambda x: int(x["group_id"])
        ),
    }


def build_upload_items(settings: Settings, *, limit: int | None = None) -> list[dict[str, Any]]:
    """
    exact SHA 대표 사진 + text_merge(있으면) / group_text 기반 인접 메시지로 items 생성.
    excluded_from_upload=1 멤버는 제외.
    """
    client_id = ensure_client_instance_id()
    items: list[dict[str, Any]] = []
    with connect(settings.db_path) as conn:
        rows = conn.execute(
            """
            SELECT
              g.sha256,
              p.id AS photo_id,
              p.rel_path,
              p.file_name,
              p.byte_size,
              m.decision AS merge_decision,
              m.merged_text,
              m.id AS merge_id
            FROM exact_sha_group g
            JOIN photo_file p ON p.id = g.representative_photo_id
            LEFT JOIN text_merge m
              ON m.sha256 = g.sha256 AND m.status = 'active'
            WHERE EXISTS (
              SELECT 1 FROM exact_sha_member em
              WHERE em.group_id = g.id AND em.is_representative = 1
                AND IFNULL(em.excluded_from_upload, 0) = 0
            )
            ORDER BY g.id
            """
        ).fetchall()

        for row in rows:
            sha = (row["sha256"] or "").lower()
            if not sha or len(sha) != 64:
                continue
            photo_id = int(row["photo_id"])
            rel_path = str(row["rel_path"] or "").replace("\\", "/")
            # [변경사유]: 절대경로 금지 — photos/ 상대만
            if rel_path.startswith("/") or (len(rel_path) > 1 and rel_path[1] == ":"):
                log.warning("skip absolute rel_path photo_id=%s", photo_id)
                continue

            # [변경사유]: 50MiB ingress 초과만 제외 (서버와 동일). 선최적화(Pillow) 없음
            byte_size = int(row["byte_size"] or 0)
            abs_photo = (settings.export_root / rel_path) if settings.export_root else None
            if abs_photo is not None and abs_photo.is_file():
                byte_size = abs_photo.stat().st_size
            if exceeds_ingress_limit(byte_size):
                log.warning(
                    "skip oversized photo_id=%s bytes=%s max=%s error=FILE_EXCEEDS_INGRESS_LIMIT",
                    photo_id,
                    byte_size,
                    MAX_UPLOAD_FILE_BYTES,
                )
                continue

            matched: list[dict[str, Any]] = []
            merge_decision = row["merge_decision"]
            if merge_decision in ("merged", "collapse") and row["merged_text"]:
                text = str(row["merged_text"])
                if is_attachment_marker_text(text):
                    decision = "upload_one"
                    text_relation = "same"
                else:
                    matched.append(
                        {
                            "sent_at": "",
                            "text": text,
                            "message_fingerprint": _fingerprint_message("", text),
                        }
                    )
                    decision = "collapse" if merge_decision == "collapse" else "upload_one"
                    text_relation = "collapse" if merge_decision == "collapse" else "merged"
            elif merge_decision == "review":
                # [변경사유]: review 건은 payload에 넣되 decision=partial 로 표시
                decision = "partial"
                text_relation = "conflict"
                srcs = conn.execute(
                    """
                    SELECT body_raw, abs_time FROM text_merge_source
                    WHERE merge_id = ? ORDER BY seq_in_source
                    """,
                    (row["merge_id"],),
                ).fetchall()
                for s in srcs:
                    t = str(s["body_raw"] or "")
                    at = str(s["abs_time"] or "")
                    if not t.strip():
                        continue
                    matched.append(
                        {
                            "sent_at": at,
                            "text": t,
                            "message_fingerprint": _fingerprint_message(at, t),
                        }
                    )
            else:
                decision = "upload_one"
                text_relation = "same"
                # [변경사유]: assignment 의 photo 메시지(body='사진')는 caption 후보에서 제외
                #   설명은 group_text(text) 만 사용
                msgs = conn.execute(
                    """
                    SELECT pm.abs_time AS abs_time, pm.body_raw AS body_raw, pm.msg_kind AS msg_kind
                    FROM photo_message_assignment a
                    JOIN image_group ig ON ig.id = a.group_id
                    JOIN group_text gt ON gt.group_id = ig.id
                    JOIN parsed_message pm ON pm.id = gt.message_id
                    WHERE a.photo_id = ?
                      AND pm.msg_kind = 'text'
                    ORDER BY gt.seq_in_group, pm.abs_time
                    """,
                    (photo_id,),
                ).fetchall()
                seen: set[str] = set()
                for m in msgs:
                    t = str(m["body_raw"] or "")
                    at = str(m["abs_time"] or "")
                    if not t.strip() or is_attachment_marker_text(t):
                        continue
                    fp = _fingerprint_message(at, t)
                    if fp in seen:
                        continue
                    seen.add(fp)
                    matched.append(
                        {
                            "sent_at": at,
                            "text": t,
                            "message_fingerprint": fp,
                        }
                    )

            local_item_id = f"photo:{photo_id}:{sha[:16]}"
            item = {
                "local_item_id": local_item_id,
                "decision": decision,
                "sha256": sha,
                "rel_path": rel_path,
                "matched_messages": matched,
                "match_hints": {
                    "local_exact": True,
                    "text_relation": text_relation,
                },
                "_meta": {
                    "file_name": row["file_name"],
                    "photo_id": photo_id,
                    "idempotency_key": _idempotency_key(client_id, sha, local_item_id),
                },
            }
            items.append(item)

        photo_ids = [
            int((it.get("_meta") or {}).get("photo_id") or 0)
            for it in items
            if int((it.get("_meta") or {}).get("photo_id") or 0) > 0
        ]
        similar_map = _load_similar_membership(conn, photo_ids)
        grouped_map = _load_grouped_photo_shape(conn, photo_ids)
        for it in items:
            meta = it.get("_meta") if isinstance(it.get("_meta"), dict) else {}
            pid = int(meta.get("photo_id") or 0)
            if pid in similar_map:
                meta["similar"] = similar_map[pid]
            if pid in grouped_map:
                meta["group_candidate"] = grouped_map[pid]
        policy_result = _apply_similar_policy(items, similar_map)
        items = policy_result["items"]
        if limit is not None and len(items) > limit:
            items = items[:limit]

        # build_batch_manifest 에서 재사용할 수 있게 함수 속성에 저장
        build_upload_items._last_policy = {  # type: ignore[attr-defined]
            "skipped": policy_result["skipped"],
            "deferred_groups": policy_result["deferred_groups"],
            "grouped_candidates": [
                {
                    "photo_id": int((it.get("_meta") or {}).get("photo_id") or 0),
                    **((it.get("_meta") or {}).get("group_candidate") or {}),
                }
                for it in items
                if ((it.get("_meta") or {}).get("group_candidate") or {}).get(
                    "bundle_candidate"
                )
            ],
        }

    log.info("build_upload_items count=%s", len(items))
    return items


def build_batch_manifest(
    settings: Settings,
    *,
    limit: int | None = None,
    room_key: str | None = None,
) -> dict[str, Any]:
    """배치 매니페스트 (dry-run 출력용)."""
    client_id = ensure_client_instance_id()
    batch_id = str(uuid.uuid4())
    items = build_upload_items(settings, limit=limit)
    policy_info = getattr(build_upload_items, "_last_policy", {})  # type: ignore[attr-defined]
    requests: list[dict[str, Any]] = []
    for it in items:
        meta = it.pop("_meta")
        requests.append(
            {
                "source": "kakao_local",
                "client_instance_id": client_id,
                "idempotency_key": meta["idempotency_key"],
                "batch_id": batch_id,
                "room_key": room_key,
                "auto_register": False,
                "item": it,
                "file_rel": it["rel_path"],
                "file_name": meta.get("file_name"),
            }
        )
    return {
        "source": "kakao_local",
        "client_instance_id": client_id,
        "batch_id": batch_id,
        "auto_register": False,
        "item_count": len(requests),
        "requests": requests,
        "similar_policy": {
            "skipped": list(policy_info.get("skipped") or []),
            "deferred_groups": list(policy_info.get("deferred_groups") or []),
        },
        "grouped_photo_candidates": list(policy_info.get("grouped_candidates") or []),
    }


def write_manifest(manifest: dict[str, Any], out_path: Path) -> Path:
    """매니페스트 JSON 저장."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("manifest written path=%s items=%s", out_path.name, manifest.get("item_count"))
    return out_path
