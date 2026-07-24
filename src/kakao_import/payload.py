# [변경사유]: Phase3 — Import payload 생성 (인접/merged 메시지만, 절대경로 금지)
"""서버 Import용 payload 빌더."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from kakao_import.config import PROJECT_ROOT, Settings
from kakao_import.db import connect
from kakao_import.logging_util import get_logger

log = get_logger(__name__)

# [변경사유]: 서버 KAKAO_IMPORT_MAX_FILE_BYTES(15MiB) 와 맞춤 — 초과 파일은 전송 전 스킵
MAX_UPLOAD_FILE_BYTES = 15 * 1024 * 1024

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

            # [변경사유]: 15MB 초과는 서버가 413 — 후보에서 제외하고 다음 건 선택
            byte_size = int(row["byte_size"] or 0)
            abs_photo = (settings.export_root / rel_path) if settings.export_root else None
            if abs_photo is not None and abs_photo.is_file():
                byte_size = abs_photo.stat().st_size
            if byte_size > MAX_UPLOAD_FILE_BYTES:
                log.warning(
                    "skip oversized photo_id=%s bytes=%s max=%s",
                    photo_id,
                    byte_size,
                    MAX_UPLOAD_FILE_BYTES,
                )
                continue

            matched: list[dict[str, Any]] = []
            merge_decision = row["merge_decision"]
            if merge_decision in ("merged", "collapse") and row["merged_text"]:
                text = str(row["merged_text"])
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
                # group_text / assignment 인접
                msgs = conn.execute(
                    """
                    SELECT abs_time, body_raw FROM (
                      SELECT pm.abs_time AS abs_time, pm.body_raw AS body_raw
                      FROM photo_message_assignment a
                      JOIN parsed_message pm ON pm.id = a.message_id
                      WHERE a.photo_id = ?
                      UNION
                      SELECT pm.abs_time AS abs_time, pm.body_raw AS body_raw
                      FROM photo_message_assignment a
                      JOIN image_group ig ON ig.id = a.group_id
                      JOIN group_text gt ON gt.group_id = ig.id
                      JOIN parsed_message pm ON pm.id = gt.message_id
                      WHERE a.photo_id = ?
                    )
                    ORDER BY abs_time
                    """,
                    (photo_id, photo_id),
                ).fetchall()
                seen: set[str] = set()
                for m in msgs:
                    t = str(m["body_raw"] or "")
                    at = str(m["abs_time"] or "")
                    if not t.strip():
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
            if limit is not None and len(items) >= limit:
                break

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
