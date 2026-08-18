# [변경사유]: Phase 4.0 — photo signature 계산 + similar 그룹 DB 적재 (upload 미변경)
"""로컬 similar 탐지."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable

from kakao_import.logging_util import get_logger
from kakao_import.similar_cluster import PhotoSig, cluster_similar_photos
from kakao_import.similar_policy import (
    DEFAULT_SIMILAR_MAX_DISTANCE,
    VALID_DECISIONS,
    upload_policy_for_decision,
)

log = get_logger(__name__)

SignFn = Callable[[bytes], tuple[str, str, str]]


def try_import_sign_fn() -> SignFn | None:
    """공용 danceinfo_image_signature 사용 (없으면 None)."""
    try:
        from danceinfo_image_signature import (  # type: ignore
            ALGO_VERSION,
            canonicalize_and_sign,
        )
    except ImportError:
        log.warning(
            "danceinfo_image_signature 미설치 — "
            "pip install -e ../backend/packages/danceinfo_image_signature 후 재시도"
        )
        return None

    def _sign(data: bytes) -> tuple[str, str, str]:
        result = canonicalize_and_sign(data)
        return (
            ALGO_VERSION,
            result.signature.dhash_hex.lower(),
            result.signature.phash_hex.lower(),
        )

    return _sign


def ensure_similar_schema(conn: sqlite3.Connection) -> None:
    """004 테이블 + 005 partial 컬럼 보장."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='similar_image_group'"
    ).fetchone()
    if not row:
        from kakao_import.config import PROJECT_ROOT

        sql_path = PROJECT_ROOT / "sql" / "004_phase4_similar_group.sql"
        conn.executescript(sql_path.read_text(encoding="utf-8"))
        log.info("applied schema %s", sql_path.name)
    # [변경사유]: 기존 DB에도 partial 컬럼 적용
    from kakao_import.db import _apply_phase41_partial_columns

    _apply_phase41_partial_columns(conn)


def resolve_photo_path(root: Path, rel_path: str) -> Path | None:
    """export root 기준 사진 경로."""
    rel = (rel_path or "").replace("\\", "/").lstrip("/")
    candidates = [root / rel, root / "photos" / Path(rel).name]
    for p in candidates:
        if p.is_file():
            return p
    return None


def upsert_photo_signatures(
    conn: sqlite3.Connection,
    root: Path,
    *,
    sign_fn: SignFn,
    limit: int | None = None,
    force: bool = False,
) -> dict[str, int]:
    """photo_file → photo_signature."""
    sql = "SELECT id, rel_path, sha256 FROM photo_file ORDER BY id"
    rows = list(conn.execute(sql))
    if limit is not None and limit > 0:
        rows = rows[:limit]
    ok = skip = miss = err = 0
    for r in rows:
        pid = int(r["id"])
        if not force:
            exists = conn.execute(
                "SELECT 1 FROM photo_signature WHERE photo_id = ?", (pid,)
            ).fetchone()
            if exists:
                skip += 1
                continue
        path = resolve_photo_path(root, str(r["rel_path"]))
        if path is None:
            miss += 1
            log.warning("similar sign missing photo_id=%s", pid)
            continue
        try:
            data = path.read_bytes()
            algo, dhash, phash = sign_fn(data)
            sha = (r["sha256"] or "").lower() or None
            conn.execute(
                """
                INSERT INTO photo_signature (
                  photo_id, algo_version, dhash_hex, phash_hex, source_sha256, computed_at
                ) VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(photo_id) DO UPDATE SET
                  algo_version = excluded.algo_version,
                  dhash_hex = excluded.dhash_hex,
                  phash_hex = excluded.phash_hex,
                  source_sha256 = excluded.source_sha256,
                  computed_at = datetime('now')
                """,
                (pid, algo, dhash, phash, sha),
            )
            ok += 1
        except Exception as exc:  # noqa: BLE001 — 단건 실패 스킵
            err += 1
            log.warning(
                "similar sign fail photo_id=%s err=%s",
                pid,
                str(exc)[:120],
            )
    return {"signed": ok, "skipped": skip, "missing": miss, "errors": err}


def rebuild_similar_groups(
    conn: sqlite3.Connection,
    *,
    max_distance: int = DEFAULT_SIMILAR_MAX_DISTANCE,
    workspace_key: str = "current",
) -> dict[str, Any]:
    """signature 기반 그룹 재구성. decision 기본 deferred. upload 큐는 건드리지 않음."""
    # [변경사유]: SHA 완전 동일(업로드 제외) 장은 similar-review에 넣지 않음 — AllDup 대체
    skip_row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM photo_signature s
        JOIN exact_sha_member em ON em.photo_id = s.photo_id
        WHERE IFNULL(em.excluded_from_upload, 0) = 1
        """
    ).fetchone()
    skipped_exact = int(skip_row["n"] if skip_row else 0)
    rows = conn.execute(
        """
        SELECT s.photo_id, s.dhash_hex, s.phash_hex, p.sha256
        FROM photo_signature s
        JOIN photo_file p ON p.id = s.photo_id
        WHERE s.photo_id NOT IN (
            SELECT photo_id FROM exact_sha_member
            WHERE IFNULL(excluded_from_upload, 0) = 1
        )
        """
    ).fetchall()
    from kakao_import.poster_schema import excluded_poster_photo_ids

    skip_poster_ids = excluded_poster_photo_ids(conn)
    if skip_poster_ids:
        rows = [r for r in rows if int(r["photo_id"]) not in skip_poster_ids]
        log.info("similar skip poster_non_poster photos=%s", len(skip_poster_ids))
    photos = [
        PhotoSig(
            photo_id=int(r["photo_id"]),
            dhash_hex=str(r["dhash_hex"]),
            phash_hex=str(r["phash_hex"]),
            sha256=(str(r["sha256"]).lower() if r["sha256"] else None),
        )
        for r in rows
    ]
    clusters = cluster_similar_photos(photos, max_distance=max_distance)
    log.info(
        "similar groups skip_exact=%s cluster_photos=%s groups=%s members=%s dist=%s",
        skipped_exact,
        len(photos),
        len(clusters),
        sum(len(c.photo_ids) for c in clusters),
        max_distance,
    )

    conn.execute(
        "DELETE FROM similar_image_member WHERE group_id IN "
        "(SELECT id FROM similar_image_group WHERE workspace_key = ?)",
        (workspace_key,),
    )
    conn.execute(
        "DELETE FROM similar_image_group WHERE workspace_key = ?",
        (workspace_key,),
    )

    for i, c in enumerate(clusters, start=1):
        gkey = f"sg-{i:04d}"
        cur = conn.execute(
            """
            INSERT INTO similar_image_group (
              workspace_key, group_key, decision, representative_photo_id,
              max_distance, member_count, created_at, updated_at
            ) VALUES (?, ?, 'deferred', ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                workspace_key,
                gkey,
                c.representative_photo_id,
                max_distance,
                len(c.photo_ids),
            ),
        )
        gid = int(cur.lastrowid)
        for pid in c.photo_ids:
            conn.execute(
                """
                INSERT INTO similar_image_member (group_id, photo_id, is_representative)
                VALUES (?, ?, ?)
                """,
                (gid, pid, 1 if pid == c.representative_photo_id else 0),
            )

    return {
        "signatures": len(photos),
        "skipped_exact": skipped_exact,
        "skipped_poster": len(skip_poster_ids),
        "groups": len(clusters),
        "members": sum(len(c.photo_ids) for c in clusters),
        "max_distance": max_distance,
        "workspace_key": workspace_key,
    }


def list_similar_groups(conn: sqlite3.Connection, *, workspace_key: str = "current") -> list[dict[str, Any]]:
    """그룹 요약 (로그/CLI). partial 시 subgroups 포함."""
    groups = conn.execute(
        """
        SELECT id, group_key, decision, representative_photo_id, member_count, max_distance
        FROM similar_image_group
        WHERE workspace_key = ?
        ORDER BY id
        """,
        (workspace_key,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for g in groups:
        members = conn.execute(
            """
            SELECT m.photo_id, m.is_representative, m.subgroup_key, m.is_subgroup_rep,
                   p.rel_path, p.file_name
            FROM similar_image_member m
            JOIN photo_file p ON p.id = m.photo_id
            WHERE m.group_id = ?
            ORDER BY m.photo_id
            """,
            (int(g["id"]),),
        ).fetchall()
        decision = str(g["decision"])
        member_list = [
            {
                "photo_id": int(m["photo_id"]),
                "rel": m["rel_path"],
                "file_name": m["file_name"],
                "is_representative": bool(m["is_representative"]),
                "subgroup_key": m["subgroup_key"],
                "is_subgroup_rep": bool(m["is_subgroup_rep"]),
            }
            for m in members
        ]
        item: dict[str, Any] = {
            "group_id": int(g["id"]),
            "group_key": g["group_key"],
            "decision": decision,
            "upload_policy": upload_policy_for_decision(decision),
            "representative_photo_id": g["representative_photo_id"],
            "member_count": int(g["member_count"]),
            "max_distance": int(g["max_distance"]),
            "members": member_list,
            "subgroups": _build_subgroups_view(member_list),
        }
        out.append(item)
    return out


def _build_subgroups_view(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """member.subgroup_key → UI/API용 subgroups 목록."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for m in members:
        key = m.get("subgroup_key")
        if not key:
            continue
        buckets.setdefault(str(key), []).append(m)
    out: list[dict[str, Any]] = []
    for key, mems in sorted(buckets.items(), key=lambda x: x[0]):
        rep = next((m["photo_id"] for m in mems if m.get("is_subgroup_rep")), None)
        if rep is None and mems:
            rep = mems[0]["photo_id"]
        out.append(
            {
                "subgroup_key": key,
                "photo_ids": [m["photo_id"] for m in mems],
                "representative_photo_id": rep,
                "is_singleton": len(mems) == 1,
            }
        )
    return out


def set_similar_group_decision(
    conn: sqlite3.Connection,
    *,
    group_id: int,
    decision: str,
    representative_photo_id: int | None = None,
    subgroups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    content decision만 저장. upload 큐·파일 삭제는 하지 않음.
    partial 이면 subgroups 필수:
      [{"photo_ids":[1,2], "representative_photo_id":1}, {"photo_ids":[3]}, ...]
    모든 멤버가 정확히 한 서브그룹에 속해야 함. size≥2 묶음이 1개 이상 권장.
    """
    d = (decision or "").strip()
    if d not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(VALID_DECISIONS)}")
    row = conn.execute(
        "SELECT id, representative_photo_id FROM similar_image_group WHERE id = ?",
        (group_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"group_id={group_id} not found")

    member_rows = conn.execute(
        "SELECT photo_id FROM similar_image_member WHERE group_id = ?",
        (group_id,),
    ).fetchall()
    all_ids = {int(r["photo_id"]) for r in member_rows}
    if not all_ids:
        raise ValueError("group has no members")

    subgroups_out: list[dict[str, Any]] = []

    if d == "partial":
        if not subgroups:
            raise ValueError("partial requires subgroups")
        covered: set[int] = set()
        conn.execute(
            "UPDATE similar_image_member SET subgroup_key = NULL, is_subgroup_rep = 0, "
            "is_representative = 0 WHERE group_id = ?",
            (group_id,),
        )
        multi_count = 0
        for i, sg in enumerate(subgroups, start=1):
            pids = [int(x) for x in (sg.get("photo_ids") or [])]
            if not pids:
                raise ValueError("subgroup photo_ids empty")
            if any(p not in all_ids for p in pids):
                raise ValueError("subgroup photo_id not in group")
            if covered.intersection(pids):
                raise ValueError("photo appears in multiple subgroups")
            covered.update(pids)
            key = str(sg.get("subgroup_key") or f"p{i}")
            if len(pids) >= 2:
                multi_count += 1
            else:
                key = f"solo-{pids[0]}"
            rep = sg.get("representative_photo_id")
            rep_id = int(rep) if rep is not None else pids[0]
            if rep_id not in pids:
                raise ValueError("subgroup representative not in photo_ids")
            for pid in pids:
                conn.execute(
                    """
                    UPDATE similar_image_member
                    SET subgroup_key = ?, is_subgroup_rep = ?,
                        is_representative = ?
                    WHERE group_id = ? AND photo_id = ?
                    """,
                    (
                        key,
                        1 if pid == rep_id else 0,
                        1 if pid == rep_id else 0,
                        group_id,
                        pid,
                    ),
                )
            subgroups_out.append(
                {
                    "subgroup_key": key,
                    "photo_ids": sorted(pids),
                    "representative_photo_id": rep_id,
                    "is_singleton": len(pids) == 1,
                }
            )
        if covered != all_ids:
            missing = sorted(all_ids - covered)
            raise ValueError(f"partial must cover all members; missing={missing}")
        if multi_count < 1:
            raise ValueError(
                "partial needs at least one multi-member subgroup "
                "(otherwise use different_content)"
            )
        # 그룹 대표: 첫 multi 서브그룹 대표
        rep = next(
            s["representative_photo_id"]
            for s in subgroups_out
            if not s["is_singleton"]
        )
    else:
        # non-partial: 서브그룹 클리어
        conn.execute(
            "UPDATE similar_image_member SET subgroup_key = NULL, is_subgroup_rep = 0 "
            "WHERE group_id = ?",
            (group_id,),
        )
        rep = representative_photo_id
        if rep is not None:
            if rep not in all_ids:
                raise ValueError("representative_photo_id not in group")
            conn.execute(
                "UPDATE similar_image_member SET is_representative = 0 WHERE group_id = ?",
                (group_id,),
            )
            conn.execute(
                "UPDATE similar_image_member SET is_representative = 1 "
                "WHERE group_id = ? AND photo_id = ?",
                (group_id, rep),
            )
        else:
            rep = int(row["representative_photo_id"] or 0) or None

    conn.execute(
        """
        UPDATE similar_image_group
        SET decision = ?, representative_photo_id = COALESCE(?, representative_photo_id),
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (d, rep, group_id),
    )
    return {
        "ok": True,
        "group_id": group_id,
        "decision": d,
        "upload_policy": upload_policy_for_decision(d),
        "representative_photo_id": rep,
        "subgroups": subgroups_out,
        "note": "decision only - upload queue unchanged; no auto-merge/delete",
    }
