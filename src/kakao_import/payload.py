# [변경사유]: Phase3 — Import payload 생성 (인접/merged 메시지만, 절대경로 금지)
# [변경사유]: Phase 4.2+ — similar 이후 채팅 매칭 묶음을 main+sub_images 1 request로 붕괴
# [변경사유]: 실제 붕괴는 KakaoTalk `_01` 동일 시각 스템만 — 연속 단독 사진 제외
# [변경사유]: same_content/partial 멤버 캡션 union + 단톡방 헤더·채팅분리 구분선
"""서버 Import용 payload 빌더."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from kakao_import.config import PROJECT_ROOT, Settings
from kakao_import.caption_build import (
    build_caption_for_photos,
    similar_union_member_ids,
    union_captions_for_photo_ids,
)
from kakao_import.db import connect
from kakao_import.logging_util import get_logger
from kakao_import.photo_name import kakao_album_stem_and_seq
from kakao_import.similar_policy import upload_policy_for_decision
from kakao_import.upload_state import (
    load_incremental_caption_plan,
    record_similar_policy_skips,
)

log = get_logger(__name__)

# [변경사유]: 서버 KAKAO_IMPORT_MAX_INGRESS_BYTES(50MiB) 와 맞춤 — 초과만 전송 전 거부
MAX_UPLOAD_FILE_BYTES = 50 * 1024 * 1024
# [변경사유]: 예전 15MiB 하드 스킵 제거 확인용 (회귀 테스트)
LEGACY_HARD_SKIP_BYTES = 15 * 1024 * 1024
# [변경사유]: Phase 4.2+ — Nginx total body · 서버 maxFiles 와 맞춤 (main+4 sub)
MAX_BUNDLE_MEMBERS = 5


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


def _idempotency_key(media_fp: str, caption_fp: str) -> str:
    """[변경사유]: photo_id 제거 — 미디어+캡션 지문만으로 동일 요청 인식."""
    raw = f"kakao:upload:v1:{media_fp}:{caption_fp}".encode()
    return hashlib.sha256(raw).hexdigest()


def caption_fingerprint(matched_messages: list[Any] | None) -> str:
    """캡션 지문. 날짜·본문은 유지하고 문자열만 이어 해시."""
    parts: list[str] = []
    for m in matched_messages or []:
        if isinstance(m, dict):
            parts.append(str(m.get("text") or ""))
        else:
            parts.append(str(m or ""))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def media_fingerprint(main_sha: str, member_shas: list[str] | None = None) -> str:
    """단일 = source SHA. 묶음 1차 = 정렬 멤버 SHA 집합."""
    main = str(main_sha or "").strip().lower()
    extra = [str(s).strip().lower() for s in (member_shas or []) if str(s).strip()]
    shas = [s for s in ([main] + extra) if len(s) == 64]
    uniq = sorted(set(shas))
    if len(uniq) <= 1:
        return main
    joined = "|".join(uniq)
    return hashlib.sha256(f"bundle|{joined}".encode()).hexdigest()


def assign_item_idempotency(item: dict[str, Any]) -> None:
    """최종 캡션·묶음 반영 후 멱등키 기록."""
    sha = str(item.get("sha256") or "").strip().lower()
    member = [sha]
    for s in item.get("sub_images") or []:
        if isinstance(s, dict):
            member.append(str(s.get("sha256") or "").strip().lower())
    media_fp = media_fingerprint(sha, member)
    cap_fp = caption_fingerprint(item.get("matched_messages") if isinstance(item.get("matched_messages"), list) else [])
    meta = item.get("_meta") if isinstance(item.get("_meta"), dict) else {}
    meta["idempotency_key"] = _idempotency_key(media_fp, cap_fp)
    meta["media_fingerprint"] = media_fp
    meta["caption_fingerprint"] = cap_fp
    item["_meta"] = meta


def _bundle_idempotency_key(member_shas: list[str], caption_fp: str) -> str:
    """묶음 멤버 SHA 집합 + 캡션."""
    media_fp = media_fingerprint(member_shas[0] if member_shas else "", member_shas)
    return _idempotency_key(media_fp, caption_fp)


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
    """채팅 매칭 그룹 메타. slot_count≥2 는 후보 표시일 뿐, 실제 묶음은 `_01` 앨범만."""
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


def _item_file_name(item: dict[str, Any]) -> str:
    """업로드 item 의 원본 파일명."""
    meta = item.get("_meta") if isinstance(item.get("_meta"), dict) else {}
    named = str(meta.get("file_name") or "").strip()
    if named:
        return Path(named).name
    return Path(str(item.get("rel_path") or "")).name


def _item_album_stem_seq(item: dict[str, Any]) -> tuple[str | None, int]:
    """KakaoTalk 앨범 스템·sequence. 파싱 실패면 묶음 불가."""
    return kakao_album_stem_and_seq(_item_file_name(item))


def _is_filename_album_set(members: list[dict[str, Any]]) -> bool:
    """
    같은 시각 스템 ≥2장이고 `_01` 이상(sequence≥1)이 1장이라도 있으면 PC 앨범.
    [변경사유]: 접미사 없는 단독 사진끼리(다른 밀리초)는 묶지 않음.
    """
    if len(members) < 2:
        return False
    stems: set[str] = set()
    max_seq = 0
    for it in members:
        stem, seq = _item_album_stem_seq(it)
        if not stem:
            return False
        stems.add(stem)
        if seq > max_seq:
            max_seq = seq
    return len(stems) == 1 and max_seq >= 1


def _album_order_key(item: dict[str, Any]) -> tuple[int, int, int]:
    """앨범 내 순서: `_NN` sequence → 채팅 slot → photo_id. 본파일(seq=0)이 main."""
    meta = item.get("_meta") if isinstance(item.get("_meta"), dict) else {}
    gc = meta.get("group_candidate") if isinstance(meta.get("group_candidate"), dict) else {}
    _stem, seq = _item_album_stem_seq(item)
    return (seq, int(gc.get("slot_index") or 0), int(meta.get("photo_id") or 0))


def _attach_bundle_to_main(
    ordered: list[dict[str, Any]],
    *,
    client_id: str,
    gid: int,
    album_stem: str,
) -> dict[str, Any]:
    """ordered[0]=main, 나머지 sub_images. bundled_groups 한 행도 반환."""
    main = ordered[0]
    subs = ordered[1:]
    main_meta = main.get("_meta") if isinstance(main.get("_meta"), dict) else {}
    gc0 = (
        main_meta.get("group_candidate")
        if isinstance(main_meta.get("group_candidate"), dict)
        else {}
    )
    member_shas = [str(main.get("sha256") or "").lower()] + [
        str(s.get("sha256") or "").lower() for s in subs
    ]
    member_photo_ids = [int(main_meta.get("photo_id") or 0)] + [
        int((s.get("_meta") or {}).get("photo_id") or 0) for s in subs
    ]
    main["sub_images"] = [
        {
            "sha256": str(s.get("sha256") or "").lower(),
            "rel_path": str(s.get("rel_path") or "").replace("\\", "/"),
        }
        for s in subs
    ]
    main_meta["idempotency_key"] = _bundle_idempotency_key(
        member_shas,
        caption_fingerprint(main.get("matched_messages") if isinstance(main.get("matched_messages"), list) else []),
    )
    main_meta["bundle"] = {
        "group_id": gid,
        "group_key": str(gc0.get("group_key") or ""),
        "album_stem": album_stem,
        "slot_count_chat": int(gc0.get("slot_count") or 0),
        "member_photo_ids": member_photo_ids,
        "main_photo_id": int(main_meta.get("photo_id") or 0),
        "member_count": len(ordered),
    }
    main_meta["sub_file_names"] = [
        str(
            (s.get("_meta") or {}).get("file_name")
            or Path(str(s.get("rel_path") or "")).name
        )
        for s in subs
    ]
    row = {
        "group_id": gid,
        "group_key": str(gc0.get("group_key") or ""),
        "album_stem": album_stem,
        "slot_count_chat": int(gc0.get("slot_count") or 0),
        "member_photo_ids": member_photo_ids,
        "main_photo_id": int(main_meta.get("photo_id") or 0),
        "member_count": len(ordered),
        "sub_count": len(subs),
    }
    log.info(
        "bundle collapse group_id=%s stem=%s members=%s main_photo=%s",
        gid,
        album_stem,
        len(ordered),
        main_meta.get("photo_id"),
    )
    return row


def collapse_grouped_photo_bundles(
    items: list[dict[str, Any]],
    *,
    client_id: str,
    max_members: int = MAX_BUNDLE_MEMBERS,
) -> dict[str, Any]:
    """
    similar policy 이후 — 같은 채팅 group_id 안에서도
    KakaoTalk `_01`/`_02` 동일 시각 스템만 1 request(main+sub)로 붕괴.
    [변경사유]: Phase 4.2+ C+Y — similar는 이미 필터됨.
    [변경사유]: 연속 단독 사진(접미사 없음·다른 밀리초)은 단건 유지 — 오묶음 방지.
    """
    singles: list[dict[str, Any]] = []
    by_group: dict[int, list[dict[str, Any]]] = {}

    for item in items:
        meta = item.get("_meta") if isinstance(item.get("_meta"), dict) else {}
        gc = meta.get("group_candidate") if isinstance(meta.get("group_candidate"), dict) else None
        if not gc or not gc.get("bundle_candidate"):
            singles.append(item)
            continue
        gid = int(gc.get("group_id") or 0)
        if gid <= 0:
            singles.append(item)
            continue
        by_group.setdefault(gid, []).append(item)

    out: list[dict[str, Any]] = list(singles)
    bundled_groups: list[dict[str, Any]] = []
    collapsed = 0

    for gid, members in sorted(by_group.items(), key=lambda x: x[0]):
        by_stem: dict[str | None, list[dict[str, Any]]] = {}
        for it in members:
            stem, _seq = _item_album_stem_seq(it)
            by_stem.setdefault(stem, []).append(it)

        leftovers: list[dict[str, Any]] = []
        for stem, stem_members in sorted(
            by_stem.items(), key=lambda x: x[0] or ""
        ):
            if stem is None or not _is_filename_album_set(stem_members):
                leftovers.extend(stem_members)
                continue

            ordered = sorted(stem_members, key=_album_order_key)
            if len(ordered) > max_members:
                log.warning(
                    "bundle truncate group_id=%s stem=%s kept=%s dropped=%s max=%s",
                    gid,
                    stem,
                    max_members,
                    len(ordered) - max_members,
                    max_members,
                )
                leftovers.extend(ordered[max_members:])
                ordered = ordered[:max_members]
            row = _attach_bundle_to_main(
                ordered, client_id=client_id, gid=gid, album_stem=stem
            )
            out.append(ordered[0])
            collapsed += len(ordered) - 1
            bundled_groups.append(row)

        if leftovers:
            # [변경사유]: 같은 채팅 그룹이어도 `_01` 스템이 아니면 단건 유지
            if len(members) >= 2:
                log.info(
                    "bundle skip non-album group_id=%s leftover=%s",
                    gid,
                    len(leftovers),
                )
            out.extend(leftovers)

    return {
        "items": out,
        "bundled_groups": bundled_groups,
        "bundle_collapsed_count": collapsed,
    }


def build_upload_items(
    settings: Settings,
    *,
    limit: int | None = None,
    room_ids: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[dict[str, Any]]:
    """
    exact SHA 대표 사진 + text_merge(있으면) / group_text 기반 인접 메시지로 items 생성.
    excluded_from_upload=1 멤버는 제외.
    [변경사유]: 증분 — uploaded/hold 등은 caption SQL 재계산 스킵, 캐시 있으면 재사용
    [변경사유]: room_ids 있으면 해당 방 rel 만. 활성 포스터 모델 있으면 미분류 sha 제외.
    """
    from kakao_import.pipeline import normalize_room_ids, room_id_from_rel

    client_id = ensure_client_instance_id()
    items: list[dict[str, Any]] = []
    caption_plan = load_incremental_caption_plan(settings.db_path)
    skip_shas: set[str] = set(caption_plan.get("skip_shas") or set())
    caption_cache: dict[str, str] = dict(caption_plan.get("caption_cache") or {})
    rebuilt = 0
    reused_cache = 0
    skipped_eval = 0
    skipped_room = 0
    skipped_unclassified = 0
    want = normalize_room_ids(room_ids)
    require_classify = False
    try:
        from kakao_import.poster_classify import load_active_model

        require_classify = load_active_model() is not None
    except Exception:  # noqa: BLE001
        require_classify = False
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

        from kakao_import.poster_schema import (
            excluded_poster_shas,
            has_any_classify_for_sha,
        )

        poster_skip = excluded_poster_shas(conn)
        if poster_skip:
            log.info("upload skip poster_non_poster shas=%s", len(poster_skip))

        for row in rows:
            sha = (row["sha256"] or "").lower()
            if not sha or len(sha) != 64:
                continue
            # [변경사유]: 포스터 분류 non_poster 만 제외. exact SHA 제외와 AND
            if sha in poster_skip:
                log.info("upload skip poster-classified sha=%s", sha[:12])
                continue
            rel_path = str(row["rel_path"] or "").replace("\\", "/")
            if want and room_id_from_rel(rel_path) not in want:
                skipped_room += 1
                continue
            # [변경사유]: 활성 모델 있을 때 classify 행 없으면 업로드 금지(미분류 hold)
            if require_classify and not has_any_classify_for_sha(conn, sha):
                skipped_unclassified += 1
                log.info("upload skip unclassified sha=%s rel=%s", sha[:12], rel_path)
                continue
            # [변경사유]: 이미 평가 완료(uploaded/hold/excluded)면 caption 재계산 생략
            if sha in skip_shas:
                skipped_eval += 1
                continue
            photo_id = int(row["photo_id"])
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
            # [변경사유]: 증분 — caption_text_cached 재사용, 없으면 SQL 조립
            if sha in caption_cache:
                text = str(caption_cache.get(sha) or "")
                reused_cache += 1
            else:
                text = build_caption_for_photos(conn, [photo_id])
                rebuilt += 1
                if (
                    not text
                    and merge_decision in ("merged", "collapse")
                    and row["merged_text"]
                ):
                    text = str(row["merged_text"])
            if text and is_attachment_marker_text(text):
                text = ""
            if text:
                matched.append(
                    {
                        "sent_at": "",
                        "text": text,
                        "message_fingerprint": _fingerprint_message("", text),
                    }
                )
            if merge_decision == "review":
                decision = "partial"
                text_relation = "conflict"
            elif merge_decision == "collapse" and text:
                decision = "collapse"
                text_relation = "collapse"
            elif merge_decision == "merged" and text:
                decision = "upload_one"
                text_relation = "merged"
            else:
                decision = "upload_one"
                text_relation = "same"

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
                },
            }
            assign_item_idempotency(item)
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
        # [변경사유]: same_content/partial 묶음 — 스킵 멤버 설명을 대표 본문에 union
        for it in items:
            meta = it.get("_meta") if isinstance(it.get("_meta"), dict) else {}
            pid = int(meta.get("photo_id") or 0)
            sha = str(it.get("sha256") or "").strip().lower()
            # [변경사유]: 캐시 재사용 건은 similar union SQL 도 생략
            if sha in caption_cache:
                continue
            sim = similar_map.get(pid)
            member_ids = similar_union_member_ids(conn, sim) if sim else None
            if not member_ids or len(member_ids) < 2:
                continue
            unioned = union_captions_for_photo_ids(conn, member_ids)
            if not unioned:
                continue
            it["matched_messages"] = [
                {
                    "sent_at": "",
                    "text": unioned,
                    "message_fingerprint": _fingerprint_message("", unioned),
                }
            ]
            hints = it.get("match_hints")
            if isinstance(hints, dict):
                hints["text_relation"] = "similar_union"
            log.info(
                "similar caption union photo_id=%s members=%s chars=%s",
                pid,
                len(member_ids),
                len(unioned),
            )
        # [변경사유]: Phase 4.2+ — similar 필터 후 채팅 묶음 붕괴 (limit 전에 적용)
        bundle_result = collapse_grouped_photo_bundles(items, client_id=client_id)
        items = bundle_result["items"]
        # [변경사유]: union·묶음 이후 최종 캡션으로 멱등키 재계산
        for it in items:
            assign_item_idempotency(it)
        if limit is not None and len(items) > limit:
            items = items[:limit]

        # [변경사유]: similar skip/deferred 를 상태에 남겨 다음 run caption 스킵
        policy_skip_stats = record_similar_policy_skips(
            settings.db_path,
            skipped=list(policy_result.get("skipped") or []),
            deferred_groups=list(policy_result.get("deferred_groups") or []),
        )

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
            "bundled_groups": bundle_result["bundled_groups"],
            "bundle_collapsed_count": bundle_result["bundle_collapsed_count"],
            "incremental": {
                "skip_eval": skipped_eval,
                "reused_cache": reused_cache,
                "rebuilt_caption": rebuilt,
                "policy_skip_stats": policy_skip_stats,
            },
        }

    log.info(
        "build_upload_items count=%s skip_eval=%s cache=%s rebuilt=%s skip_room=%s skip_unclassified=%s rooms=%s",
        len(items),
        skipped_eval,
        reused_cache,
        rebuilt,
        skipped_room,
        skipped_unclassified,
        sorted(want) if want else None,
    )
    return items


def build_batch_manifest(
    settings: Settings,
    *,
    limit: int | None = None,
    room_key: str | None = None,
    room_ids: list[str] | tuple[str, ...] | set[str] | None = None,
) -> dict[str, Any]:
    """배치 매니페스트 (dry-run 출력용)."""
    client_id = ensure_client_instance_id()
    batch_id = str(uuid.uuid4())
    items = build_upload_items(settings, limit=limit, room_ids=room_ids)
    policy_info = getattr(build_upload_items, "_last_policy", {})  # type: ignore[attr-defined]
    requests: list[dict[str, Any]] = []
    for it in items:
        meta = it.pop("_meta")
        sub_rels = [
            str(s.get("rel_path") or "").replace("\\", "/")
            for s in (it.get("sub_images") or [])
            if isinstance(s, dict)
        ]
        req: dict[str, Any] = {
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
        # [변경사유]: 증분 업로드 상태 테이블 — media 단위 안정 key
        if meta.get("media_fingerprint"):
            req["candidate_key"] = f"media:{meta['media_fingerprint']}"
        # [변경사유]: 장부에 media/caption 지문 저장 — caption-only 판별용
        if meta.get("media_fingerprint"):
            req["media_fingerprint"] = meta["media_fingerprint"]
        if meta.get("caption_fingerprint"):
            req["caption_fingerprint"] = meta["caption_fingerprint"]
        if sub_rels:
            req["sub_file_rels"] = sub_rels
            req["sub_file_names"] = list(meta.get("sub_file_names") or [])
            if meta.get("bundle"):
                req["bundle"] = meta["bundle"]
        requests.append(req)
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
        "bundled_groups": list(policy_info.get("bundled_groups") or []),
        "bundle_collapsed_count": int(policy_info.get("bundle_collapsed_count") or 0),
        "incremental": dict(policy_info.get("incremental") or {}),
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
