# [변경사유]: Phase1 — scan/parse/match/hash/report/run 파이프라인
"""파이프라인."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from kakao_import.config import Settings
from kakao_import.db import (
    SCHEMA_SQL_P2,
    _apply_phase2b_columns,
    clear_batch_match_data,
    connect,
    finish_batch,
    init_schema,
    insert_parse_error,
    prune_missing_photo_files,
    rebuild_exact_groups,
    replace_messages,
    start_batch,
    supersede_text_merges,
    update_photo_sha,
    upsert_chat_source,
    upsert_photo,
)
from kakao_import.hashutil import sha256_file
from kakao_import.logging_util import get_logger
from kakao_import.matcher import MatchOutput, PhotoSlot, match_photos_to_messages
from kakao_import.merge_ops import decide_text_merge, undo_text_merge
from kakao_import.parser import parse_chat_file
from kakao_import.photo_name import parse_kakaotalk_filename
from kakao_import.text_merge import TextBundle, merge_exact_texts

log = get_logger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
LEGACY_ROOM_ID = "_legacy"


def room_id_from_rel(rel: str) -> str:
    """
    상대경로에서 방 id.
    gangnam_latin/photos/a.jpg → gangnam_latin
    chats/a.txt (구 레이아웃) → _legacy
    [변경사유]: 방별 폴더 매칭 — 다른 방 캡션과 섞이지 않게.
    """
    parts = (rel or "").replace("\\", "/").strip("/").split("/")
    if len(parts) >= 2 and parts[1] in ("chats", "photos"):
        return parts[0]
    if parts and parts[0] in ("chats", "photos"):
        return LEGACY_ROOM_ID
    return LEGACY_ROOM_ID


def iter_room_layouts(root: Path) -> list[tuple[str, Path, Path]]:
    """
    (room_id, chats_dir, photos_dir).
    신: root/<room_id>/chats + photos
    구: root/chats + root/photos (_legacy) — golden·기존 데이터.
    """
    if not root.is_dir():
        raise FileNotFoundError(f"export root missing: {root}")
    out: list[tuple[str, Path, Path]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in ("chats", "photos"):
            continue
        chats = child / "chats"
        photos = child / "photos"
        if chats.is_dir() or photos.is_dir():
            out.append((child.name, chats, photos))
    legacy_chats = root / "chats"
    legacy_photos = root / "photos"
    if legacy_chats.is_dir() or legacy_photos.is_dir():
        out.append((LEGACY_ROOM_ID, legacy_chats, legacy_photos))
    if not out:
        raise FileNotFoundError(
            f"방 폴더(<id>/chats|photos) 또는 구 chats/+photos/ 없음 under {root}"
        )
    return out


def resolve_layout(root: Path) -> tuple[Path, Path]:
    """구 레이아웃 chats/ + photos/. 테스트·golden 호환."""
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
    layouts = iter_room_layouts(root)
    with connect(settings.db_path) as conn:
        batch_id = start_batch(conn, root.name)
        n = 0
        unparsed = 0
        for room_id, _chats_dir, photos_dir in layouts:
            if not photos_dir.is_dir():
                continue
            log.info("scan room=%s photos_dir=%s", room_id, photos_dir)
            for path in sorted(photos_dir.iterdir()):
                if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
                    continue
                parsed = parse_kakaotalk_filename(path.name)
                st = path.stat()
                rel = rel_under(root, path)
                name_time = (
                    parsed.name_time.isoformat(timespec="milliseconds")
                    if parsed.name_time
                    else None
                )
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
                log.info("scan photo id=%s room=%s parse_ok=%s", pid, room_id, parsed.ok)
        summary = {"photos": n, "unparsed_photo": unparsed, "batch_id": batch_id}
        finish_batch(conn, batch_id, summary)
        conn.commit()
        return summary


def cmd_parse(settings: Settings, root: Path | None = None) -> dict[str, Any]:
    """채팅 TXT 파싱."""
    root = root or settings.export_root
    assert root is not None
    layouts = iter_room_layouts(root)
    with connect(settings.db_path) as conn:
        batch_id = start_batch(conn, root.name)
        rooms = 0
        msgs = 0
        photo_msgs = 0
        for room_id, chats_dir, _photos_dir in layouts:
            if not chats_dir.is_dir():
                continue
            log.info("parse room=%s chats_dir=%s", room_id, chats_dir)
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
                log.info(
                    "parse chat rel=%s room=%s messages=%s encoding=%s",
                    rel,
                    room_id,
                    len(rows),
                    result.encoding,
                )
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
            "SELECT id, rel_path, file_name, name_time, name_parse_ok FROM photo_file"
        ).fetchall()
        photo_slots: list[PhotoSlot] = []
        for r in photos_rows:
            if not r["name_parse_ok"] or not r["name_time"]:
                continue
            seq = parse_kakaotalk_filename(str(r["file_name"] or "")).sequence
            photo_slots.append(
                PhotoSlot(
                    photo_id=int(r["id"]),
                    rel_path=r["rel_path"],
                    name_time=datetime.fromisoformat(r["name_time"]),
                    name_seq=seq,
                )
            )

        msg_rows = conn.execute(
            """
            SELECT m.id, m.chat_id, m.seq, m.msg_kind, m.sender, m.abs_time,
                   m.body_raw, m.body_norm, m.photo_count, c.rel_path AS chat_rel
            FROM parsed_message m
            JOIN chat_source c ON c.id = m.chat_id
            ORDER BY m.chat_id, m.seq
            """
        ).fetchall()
        messages = [dict(r) for r in msg_rows]

        # [변경사유]: 방별로만 매칭 — 같은 분 다른 방 캡션 혼입 방지
        photos_by_room: dict[str, list[PhotoSlot]] = {}
        for p in photo_slots:
            rid = room_id_from_rel(p.rel_path)
            photos_by_room.setdefault(rid, []).append(p)
        msgs_by_room: dict[str, list[dict[str, Any]]] = {}
        for m in messages:
            rid = room_id_from_rel(str(m.get("chat_rel") or ""))
            msgs_by_room.setdefault(rid, []).append(m)

        result = MatchOutput()
        for rid in sorted(set(photos_by_room) | set(msgs_by_room)):
            part = match_photos_to_messages(
                photos=photos_by_room.get(rid, []),
                messages=msgs_by_room.get(rid, []),
                tolerance_seconds=settings.match_tolerance_seconds,
                group_text_max_gap_minutes=settings.group_text_max_gap_minutes,
                different_sender_grace_seconds=settings.different_sender_grace_seconds,
                different_sender_max_chars=settings.different_sender_max_chars,
                group_text_before_max_seconds=settings.group_text_before_max_seconds,
            )
            log.info(
                "match room=%s photos=%s messages=%s groups=%s",
                rid,
                len(photos_by_room.get(rid, [])),
                len(msgs_by_room.get(rid, [])),
                len(part.groups),
            )
            result.assignments.extend(part.assignments)
            result.groups.extend(part.groups)
            result.group_texts.extend(part.group_texts)
            result.reviews.extend(part.reviews)

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

        bundle_candidates = sum(1 for g in result.groups if g.get("bundle_candidate"))
        summary = {
            "batch_id": batch_id,
            "assignments": len(result.assignments),
            "groups": len(result.groups),
            "group_bundle_candidates": bundle_candidates,
            "confidence": conf_counts,
            "reviews": len(result.reviews),
            "group_texts": len(result.group_texts),
        }
        log.info(
            "match summary groups=%s bundle_candidates=%s assignments=%s reviews=%s",
            len(result.groups),
            bundle_candidates,
            len(result.assignments),
            len(result.reviews),
        )
        finish_batch(conn, batch_id, summary)
        conn.commit()
        return summary


def cmd_hash(settings: Settings, root: Path | None = None) -> dict[str, Any]:
    """SHA-256 + exact 그룹. [변경사유]: Phase 3.5 — hash 전 file_missing prune."""
    root = root or settings.export_root
    assert root is not None
    with connect(settings.db_path) as conn:
        # [변경사유]: 삭제된 로컬 파일이 exact 그룹에 남아 fail=N 누적되던 문제
        pruned = prune_missing_photo_files(conn, root)
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
        return {
            "hashed": len(rows),
            "updated": updated,
            "pruned_missing": pruned.get("pruned", 0),
            **exact,
        }


def _ensure_phase2(conn) -> None:
    """002/003 스키마 없으면 적용."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='text_merge'"
    ).fetchone()
    if not row and SCHEMA_SQL_P2.is_file():
        log.info("apply phase2 schema %s", SCHEMA_SQL_P2.name)
        conn.executescript(SCHEMA_SQL_P2.read_text(encoding="utf-8"))
    # [변경사유]: 수동 결정·되돌리기 컬럼
    _apply_phase2b_columns(conn)


def _photo_text_bundle(conn, photo_id: int) -> TextBundle:
    """assignment → group_text → message 로 설명 수집."""
    bundle = TextBundle(photo_id=photo_id)
    rows = conn.execute(
        """
        SELECT m.id AS message_id, m.chat_id, m.body_raw, m.body_norm, m.abs_time, gt.seq_in_group
        FROM photo_message_assignment a
        JOIN group_text gt ON gt.group_id = a.group_id
        JOIN parsed_message m ON m.id = gt.message_id
        WHERE a.photo_id = ?
        ORDER BY gt.seq_in_group
        """,
        (photo_id,),
    ).fetchall()
    for r in rows:
        bundle.parts.append(
            {
                "message_id": r["message_id"],
                "chat_id": int(r["chat_id"]) if r["chat_id"] is not None else None,
                "body_raw": r["body_raw"],
                "body_norm": r["body_norm"],
                "abs_time": r["abs_time"],
            }
        )
    return bundle


def cmd_merge(settings: Settings, mode: str | None = None) -> dict[str, Any]:
    """Phase2: exact SHA 그룹 텍스트 collapse/merge."""
    merge_mode = mode or settings.merge_mode
    if merge_mode not in ("safe", "balanced", "auto"):
        merge_mode = "balanced"
    with connect(settings.db_path) as conn:
        _ensure_phase2(conn)
        supersede_text_merges(conn)
        groups = conn.execute(
            "SELECT id, sha256, member_count FROM exact_sha_group"
        ).fetchall()
        counts = {"collapse": 0, "merged": 0, "review": 0, "skipped": 0}
        for g in groups:
            sha = g["sha256"]
            members = conn.execute(
                "SELECT photo_id FROM exact_sha_member WHERE group_id = ? ORDER BY photo_id",
                (g["id"],),
            ).fetchall()
            bundles = [_photo_text_bundle(conn, int(m["photo_id"])) for m in members]
            result = merge_exact_texts(sha, bundles, merge_mode)  # type: ignore[arg-type]
            counts[result.decision] = counts.get(result.decision, 0) + 1
            cur = conn.execute(
                """
                INSERT INTO text_merge (
                  sha256, mode, decision, review_required, merged_text, merged_norm,
                  before_json, after_json, status, decision_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 'auto')
                """,
                (
                    result.sha256,
                    result.mode,
                    result.decision,
                    1 if result.review_required else 0,
                    result.merged_text,
                    result.merged_norm,
                    json.dumps(result.before, ensure_ascii=False),
                    json.dumps(result.after, ensure_ascii=False) if result.after else None,
                ),
            )
            mid = int(cur.lastrowid)
            for s in result.sources:
                conn.execute(
                    """
                    INSERT INTO text_merge_source (
                      merge_id, photo_id, message_id, body_raw, body_norm, abs_time, seq_in_source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mid,
                        s["photo_id"],
                        s.get("message_id"),
                        s["body_raw"],
                        s.get("body_norm"),
                        s.get("abs_time"),
                        s.get("seq_in_source", 0),
                    ),
                )
            for c in result.conflicts:
                conn.execute(
                    """
                    INSERT INTO text_merge_conflict (merge_id, field_name, values_json)
                    VALUES (?, ?, ?)
                    """,
                    (mid, c["field_name"], c["values_json"]),
                )
            if result.review_required:
                batch_row = conn.execute(
                    "SELECT id FROM import_batch ORDER BY id DESC LIMIT 1"
                ).fetchone()
                batch_id = int(batch_row["id"]) if batch_row else 0
                if batch_id:
                    conn.execute(
                        """
                        INSERT INTO review_item (
                          batch_id, kind, ref_type, ref_id, reason, payload_json
                        ) VALUES (?, 'text_merge', 'text_merge', ?, ?, ?)
                        """,
                        (
                            batch_id,
                            mid,
                            result.decision,
                            json.dumps(
                                {"sha256": sha[:16], "mode": merge_mode},
                                ensure_ascii=False,
                            ),
                        ),
                    )
            log.info(
                "text_merge sha=%s decision=%s review=%s",
                sha[:12],
                result.decision,
                result.review_required,
            )
        conn.commit()
        return {"mode": merge_mode, "groups": len(groups), **counts}


def cmd_merge_undo(
    settings: Settings,
    *,
    merge_id: int | None = None,
    sha256: str | None = None,
) -> dict[str, Any]:
    """Phase2: active text_merge 되돌리기."""
    with connect(settings.db_path) as conn:
        _ensure_phase2(conn)
        out = undo_text_merge(conn, merge_id=merge_id, sha256=sha256)
        conn.commit()
        return out


def cmd_merge_decide(
    settings: Settings,
    *,
    merge_id: int,
    action: str,
    text: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Phase2: review merge 수동 결정."""
    if action not in ("accept", "set-text", "reject"):
        raise ValueError("action 은 accept|set-text|reject")
    with connect(settings.db_path) as conn:
        _ensure_phase2(conn)
        out = decide_text_merge(
            conn,
            merge_id=merge_id,
            action=action,  # type: ignore[arg-type]
            text=text,
            note=note,
        )
        conn.commit()
        return out


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
        merge_stats: dict[str, Any] = {}
        if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='text_merge'"
        ).fetchone():
            for row in conn.execute(
                "SELECT decision, COUNT(*) c FROM text_merge WHERE status='active' GROUP BY decision"
            ):
                merge_stats[row["decision"]] = row["c"]
        return {
            "rooms": rooms,
            "messages": messages,
            "photo_messages": photo_msgs,
            "photos": photos,
            "confidence": conf,
            "exact_groups": exact,
            "text_merge": merge_stats,
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
    gid = next(iter(gids)) if gids and None not in gids else None
    text_count = 0
    if gid:
        text_count = conn.execute(
            "SELECT COUNT(*) c FROM group_text WHERE group_id = ?", (gid,)
        ).fetchone()["c"]
        if text_count < 2:
            ok = False
            reasons.append(f"group_text_count={text_count}")
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
    """Phase1+2: scan→parse→match→hash→merge→report."""
    root = root or settings.export_root
    assert root is not None
    if not settings.db_path.exists():
        cmd_init(settings)
    out: dict[str, Any] = {}
    out["scan"] = cmd_scan(settings, root)
    out["parse"] = cmd_parse(settings, root)
    out["match"] = cmd_match(settings, root)
    out["hash"] = cmd_hash(settings, root)
    out["merge"] = cmd_merge(settings)
    out["report"] = build_report(settings, root)
    return out


# [변경사유]: Phase 4.0 — similar 탐지 (upload 큐 미변경)
def cmd_similar_detect(
    settings: Settings,
    root: Path | None = None,
    *,
    max_distance: int | None = None,
    limit: int | None = None,
    force_resign: bool = False,
) -> dict[str, Any]:
    """signature 계산 + similar 그룹 재구성. decision 기본 deferred."""
    from kakao_import.similar_detect import (
        ensure_similar_schema,
        rebuild_similar_groups,
        try_import_sign_fn,
        upsert_photo_signatures,
    )

    root = root or settings.export_root
    assert root is not None
    dist = (
        max_distance
        if max_distance is not None
        else settings.similar_max_distance
    )
    sign_fn = try_import_sign_fn()
    if sign_fn is None:
        return {
            "ok": False,
            "error": "SIGNATURE_PACKAGE_MISSING",
            "hint": "pip install Pillow && pip install -e ../backend/packages/danceinfo_image_signature",
        }
    with connect(settings.db_path) as conn:
        ensure_similar_schema(conn)
        sign_stats = upsert_photo_signatures(
            conn, root, sign_fn=sign_fn, limit=limit, force=force_resign
        )
        group_stats = rebuild_similar_groups(conn, max_distance=dist)
        conn.commit()
        log.info(
            "similar-detect signed=%s groups=%s members=%s skip_exact=%s dist=%s",
            sign_stats.get("signed"),
            group_stats.get("groups"),
            group_stats.get("members"),
            group_stats.get("skipped_exact"),
            dist,
        )
        return {"ok": True, "sign": sign_stats, "groups": group_stats}


def cmd_similar_list(settings: Settings) -> list[dict[str, Any]]:
    """similar 그룹 목록 (decision + 유도 upload_policy 표시만)."""
    from kakao_import.similar_detect import ensure_similar_schema, list_similar_groups

    with connect(settings.db_path) as conn:
        ensure_similar_schema(conn)
        return list_similar_groups(conn)


def cmd_similar_decide(
    settings: Settings,
    *,
    group_id: int,
    decision: str,
    representative_photo_id: int | None = None,
    subgroups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """content decision만 저장 - upload/삭제/자동병합 없음. partial 시 subgroups."""
    from kakao_import.similar_detect import (
        ensure_similar_schema,
        set_similar_group_decision,
    )

    with connect(settings.db_path) as conn:
        ensure_similar_schema(conn)
        try:
            out = set_similar_group_decision(
                conn,
                group_id=group_id,
                decision=decision,
                representative_photo_id=representative_photo_id,
                subgroups=subgroups,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        conn.commit()
        out["ok"] = True
        return out


# [변경사유]: Phase 4.1 — 로컬 썸네일 리뷰 UI (decision만)
def cmd_similar_review(
    settings: Settings,
    root: Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """브라우저에서 그룹 썸네일 확인 + decision 저장. upload 미변경."""
    from kakao_import.similar_review import run_review_server

    run_review_server(
        settings,
        root=root or settings.export_root,
        host=host,
        port=port,
        open_browser=open_browser,
    )
