# [변경사유]: Phase1 — scan/parse/match/hash/report/run 파이프라인
"""파이프라인."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from kakao_import.config import Settings
from kakao_import.db import (
    clear_batch_match_data,
    connect,
    finish_batch,
    init_schema,
    insert_parse_error,
    rebuild_exact_groups,
    replace_messages,
    start_batch,
    update_photo_sha,
    upsert_chat_source,
    upsert_photo,
)
from kakao_import.hashutil import sha256_file
from kakao_import.logging_util import get_logger
from kakao_import.matcher import PhotoSlot, match_photos_to_messages
from kakao_import.parser import parse_chat_file
from kakao_import.photo_name import parse_kakaotalk_filename

log = get_logger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def resolve_layout(root: Path) -> tuple[Path, Path]:
    """chats/ + photos/ 경로."""
    chats = root / "chats"
    photos = root / "photos"
    if not chats.is_dir():
        raise FileNotFoundError(f"chats/ missing under {root.name}")
    if not photos.is_dir():
        raise FileNotFoundError(f"photos/ missing under {root.name}")
    return chats, photos


def rel_under(root: Path, path: Path) -> str:
    """상대경로 (posix)."""
    return path.resolve().relative_to(root.resolve()).as_posix()


def cmd_init(settings: Settings, *, reset: bool = False) -> None:
    """DB 초기화."""
    init_schema(settings.db_path, reset=reset)


def cmd_scan(settings: Settings, root: Path | None = None) -> dict[str, Any]:
    """사진 파일 인덱스만 (SHA 전)."""
    root = root or settings.export_root
    assert root is not None
    _, photos_dir = resolve_layout(root)
    with connect(settings.db_path) as conn:
        batch_id = start_batch(conn, root.name)
        n = 0
        unparsed = 0
        for path in sorted(photos_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
                continue
            parsed = parse_kakaotalk_filename(path.name)
            st = path.stat()
            rel = rel_under(root, path)
            name_time = parsed.name_time.isoformat(timespec="milliseconds") if parsed.name_time else None
            pid = upsert_photo(
                conn,
                rel_path=rel,
                file_name=path.name,
                ext=parsed.ext,
                byte_size=st.st_size,
                mtime_ns=getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)),
                name_time=name_time,
                name_parse_ok=1 if parsed.ok else 0,
                batch_id=batch_id,
            )
            n += 1
            if not parsed.ok:
                unparsed += 1
                insert_parse_error(
                    conn,
                    batch_id=batch_id,
                    source_rel=rel,
                    line_no=None,
                    raw_excerpt=path.name,
                    error_code="unparsed_photo",
                    detail=parsed.error,
                )
            log.info("scan photo id=%s parse_ok=%s", pid, parsed.ok)
        summary = {"photos": n, "unparsed_photo": unparsed, "batch_id": batch_id}
        finish_batch(conn, batch_id, summary)
        conn.commit()
        return summary


def cmd_parse(settings: Settings, root: Path | None = None) -> dict[str, Any]:
    """채팅 TXT 파싱."""
    root = root or settings.export_root
    assert root is not None
    chats_dir, _ = resolve_layout(root)
    with connect(settings.db_path) as conn:
        batch_id = start_batch(conn, root.name)
        rooms = 0
        msgs = 0
        photo_msgs = 0
        for path in sorted(chats_dir.glob("*.txt")):
            result = parse_chat_file(path)
            st = path.stat()
            rel = rel_under(root, path)
            chat_id = upsert_chat_source(
                conn,
                rel_path=rel,
                room_title=result.room_title,
                file_size=st.st_size,
                mtime_ns=getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)),
                content_sha256=result.content_sha256,
                encoding=result.encoding,
                batch_id=batch_id,
            )
            rows = []
            for m in result.messages:
                rows.append(
                    {
                        "seq": m.seq,
                        "msg_kind": m.msg_kind,
                        "sender": m.sender,
                        "abs_time": m.abs_time.isoformat(timespec="seconds") if m.abs_time else None,
                        "body_raw": m.body_raw,
                        "body_norm": m.body_norm,
                        "photo_count": m.photo_count,
                        "line_no": m.line_no,
                    }
                )
                if m.msg_kind in ("photo", "photo_multi"):
                    photo_msgs += 1
            replace_messages(conn, chat_id, rows)
            for err in result.errors:
                insert_parse_error(
                    conn,
                    batch_id=batch_id,
                    source_rel=rel,
                    line_no=err.line_no,
                    raw_excerpt=err.raw_excerpt,
                    error_code=err.error_code,
                    detail=err.detail,
                )
            rooms += 1
            msgs += len(rows)
            log.info("parse chat rel=%s messages=%s encoding=%s", rel, len(rows), result.encoding)
        summary = {
            "batch_id": batch_id,
            "rooms": rooms,
            "messages": msgs,
            "photo_messages": photo_msgs,
        }
        finish_batch(conn, batch_id, summary)
        conn.commit()
        return summary


def cmd_match(settings: Settings, root: Path | None = None) -> dict[str, Any]:
    """시각 매칭 + 그룹 + 설명."""
    root = root or settings.export_root
    assert root is not None
    with connect(settings.db_path) as conn:
        batch_id = start_batch(conn, root.name)
        clear_batch_match_data(conn, batch_id)
        # 이전 배치 잔여 정리: 최신 매칭만 유지 — 이전 batch image_group 삭제
        conn.execute("DELETE FROM group_text")
        conn.execute("DELETE FROM photo_message_assignment")
        conn.execute("DELETE FROM image_group")
        conn.execute("DELETE FROM review_item")

        photos_rows = conn.execute(
            "SELECT id, rel_path, name_time, name_parse_ok FROM photo_file"
        ).fetchall()
        photo_slots: list[PhotoSlot] = []
        for r in photos_rows:
            if not r["name_parse_ok"] or not r["name_time"]:
                continue
            photo_slots.append(
                PhotoSlot(
                    photo_id=int(r["id"]),
                    rel_path=r["rel_path"],
                    name_time=datetime.fromisoformat(r["name_time"]),
                )
            )

        msg_rows = conn.execute(
            """
            SELECT id, chat_id, seq, msg_kind, sender, abs_time, body_raw, body_norm, photo_count
            FROM parsed_message
            ORDER BY chat_id, seq
            """
        ).fetchall()
        messages = [dict(r) for r in msg_rows]

        result = match_photos_to_messages(
            photos=photo_slots,
            messages=messages,
            tolerance_seconds=settings.match_tolerance_seconds,
            group_text_max_gap_minutes=settings.group_text_max_gap_minutes,
            different_sender_grace_seconds=settings.different_sender_grace_seconds,
            different_sender_max_chars=settings.different_sender_max_chars,
        )

        group_ids: dict[str, int] = {}
        for g in result.groups:
            cur = conn.execute(
                """
                INSERT INTO image_group (
                  batch_id, chat_id, group_key, confidence, review_required, match_reason
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    g["chat_id"],
                    g["group_key"],
                    g["confidence"],
                    1 if g["review_required"] else 0,
                    g["match_reason"],
                ),
            )
            group_ids[g["group_key"]] = int(cur.lastrowid)

        conf_counts = {"high": 0, "medium": 0, "ambiguous": 0, "unmatched": 0}
        for a in result.assignments:
            conf_counts[a.confidence] = conf_counts.get(a.confidence, 0) + 1
            if a.confidence == "unmatched":
                conn.execute(
                    """
                    INSERT INTO photo_message_assignment (
                      batch_id, group_id, photo_id, message_id, slot_index,
                      confidence, match_reason, review_required
                    ) VALUES (?, NULL, ?, NULL, 0, ?, ?, 1)
                    """,
                    (batch_id, a.photo_id, a.confidence, a.match_reason),
                )
            else:
                gid = group_ids.get(a.group_key)
                conn.execute(
                    """
                    INSERT INTO photo_message_assignment (
                      batch_id, group_id, photo_id, message_id, slot_index,
                      confidence, match_reason, review_required
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        gid,
                        a.photo_id,
                        a.message_id,
                        a.slot_index,
                        a.confidence,
                        a.match_reason,
                        1 if a.review_required else 0,
                    ),
                )

        for gt in result.group_texts:
            gid = group_ids.get(gt.group_key)
            if gid is None:
                continue
            conn.execute(
                """
                INSERT INTO group_text (group_id, message_id, seq_in_group)
                VALUES (?, ?, ?)
                """,
                (gid, gt.message_id, gt.seq_in_group),
            )

        for rev in result.reviews:
            conn.execute(
                """
                INSERT INTO review_item (batch_id, kind, ref_type, ref_id, reason, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    rev.get("kind", "review"),
                    "group_key",
                    None,
                    str(rev.get("reason", "")),
                    json.dumps(rev, ensure_ascii=False),
                ),
            )

        summary = {
            "batch_id": batch_id,
            "assignments": len(result.assignments),
            "groups": len(result.groups),
            "confidence": conf_counts,
            "reviews": len(result.reviews),
            "group_texts": len(result.group_texts),
        }
        finish_batch(conn, batch_id, summary)
        conn.commit()
        return summary


def cmd_hash(settings: Settings, root: Path | None = None) -> dict[str, Any]:
    """SHA-256 + exact 그룹."""
    root = root or settings.export_root
    assert root is not None
    with connect(settings.db_path) as conn:
        rows = conn.execute("SELECT id, rel_path, sha256 FROM photo_file").fetchall()
        updated = 0
        for r in rows:
            path = root / r["rel_path"]
            if not path.is_file():
                log.warning("hash missing rel=%s", r["rel_path"])
                continue
            digest = sha256_file(path)
            if r["sha256"] != digest:
                update_photo_sha(conn, int(r["id"]), digest)
                updated += 1
            log.info("hash photo_id=%s sha=%s", r["id"], digest[:12])
        exact = rebuild_exact_groups(conn)
        conn.commit()
        return {"hashed": len(rows), "updated": updated, **exact}


def build_report(settings: Settings, root: Path | None = None) -> dict[str, Any]:
    """리포트 dict (절대경로·전문 원문 제외)."""
    root = root or settings.export_root
    with connect(settings.db_path) as conn:
        rooms = conn.execute("SELECT COUNT(*) c FROM chat_source").fetchone()["c"]
        messages = conn.execute("SELECT COUNT(*) c FROM parsed_message").fetchone()["c"]
        photo_msgs = conn.execute(
            "SELECT COUNT(*) c FROM parsed_message WHERE msg_kind IN ('photo','photo_multi')"
        ).fetchone()["c"]
        photos = conn.execute("SELECT COUNT(*) c FROM photo_file").fetchone()["c"]
        conf = {}
        for row in conn.execute(
            "SELECT confidence, COUNT(*) c FROM photo_message_assignment GROUP BY confidence"
        ):
            conf[row["confidence"]] = row["c"]
        exact = conn.execute("SELECT COUNT(*) c FROM exact_sha_group").fetchone()["c"]
        reviews = [
            dict(r)
            for r in conn.execute(
                "SELECT id, kind, reason, status FROM review_item WHERE status='pending' LIMIT 200"
            )
        ]
        parse_errors = [
            {"error_code": r["error_code"], "source_rel": r["source_rel"], "line_no": r["line_no"]}
            for r in conn.execute(
                "SELECT error_code, source_rel, line_no FROM parse_error LIMIT 200"
            )
        ]
        esencia = _esencia_check(conn)
        return {
            "rooms": rooms,
            "messages": messages,
            "photo_messages": photo_msgs,
            "photos": photos,
            "confidence": conf,
            "exact_groups": exact,
            "review_required": reviews,
            "parse_errors_sample": parse_errors,
            "esencia_golden": esencia,
        }


def _esencia_check(conn) -> dict[str, Any]:
    """ESENCIA golden 자동 검증."""
    targets = (
        "photos/KakaoTalk_20260724_005030533.png",
        "photos/KakaoTalk_20260724_005034512.png",
    )
    rows = []
    for rel in targets:
        r = conn.execute(
            """
            SELECT p.rel_path, a.confidence, a.review_required, a.message_id, a.slot_index,
                   a.group_id, g.group_key
            FROM photo_file p
            LEFT JOIN photo_message_assignment a ON a.photo_id = p.id
            LEFT JOIN image_group g ON g.id = a.group_id
            WHERE p.rel_path = ? OR p.file_name = ?
            """,
            (rel, Path(rel).name),
        ).fetchone()
        rows.append(dict(r) if r else {"rel_path": rel, "missing": True})

    ok = True
    reasons: list[str] = []
    if any(r.get("missing") for r in rows):
        ok = False
        reasons.append("photo_missing")
    confs = [r.get("confidence") for r in rows if not r.get("missing")]
    if confs and set(confs) != {"high"}:
        ok = False
        reasons.append(f"confidence={confs}")
    if any(r.get("review_required") for r in rows if not r.get("missing")):
        ok = False
        reasons.append("review_required")
    gids = {r.get("group_id") for r in rows if not r.get("missing")}
    if len(gids) != 1 or None in gids:
        ok = False
        reasons.append(f"group_ids={gids}")
    # group texts
    gid = next(iter(gids)) if gids and None not in gids else None
    text_count = 0
    if gid:
        text_count = conn.execute(
            "SELECT COUNT(*) c FROM group_text WHERE group_id = ?", (gid,)
        ).fetchone()["c"]
        if text_count < 2:
            ok = False
            reasons.append(f"group_text_count={text_count}")
    # order by name
    slots = [r.get("slot_index") for r in rows if not r.get("missing")]
    if slots != [0, 1]:
        got = []
        for rel in targets:
            for r in rows:
                name = Path(rel).name
                rp = r.get("rel_path") or ""
                if rp.endswith(name) or r.get("file_name") == name:
                    got.append(r.get("slot_index"))
        if got != [0, 1]:
            ok = False
            reasons.append(f"slot_order={got}")
    return {
        "passed": ok,
        "reasons": reasons,
        "assignments": rows,
        "group_text_count": text_count,
    }


def cmd_run(settings: Settings, root: Path | None = None) -> dict[str, Any]:
    """Phase1 전체."""
    root = root or settings.export_root
    assert root is not None
    if not settings.db_path.exists():
        cmd_init(settings)
    out: dict[str, Any] = {}
    out["scan"] = cmd_scan(settings, root)
    out["parse"] = cmd_parse(settings, root)
    out["match"] = cmd_match(settings, root)
    out["hash"] = cmd_hash(settings, root)
    out["report"] = build_report(settings, root)
    return out
