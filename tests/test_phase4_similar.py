# [변경사유]: Phase 4.0 — similar decision/policy/cluster/detect 단위 테스트
"""similar_group: decision≠upload, detect-only, 자동병합/삭제 없음."""

from __future__ import annotations

from pathlib import Path

from kakao_import.config import Settings
from kakao_import.db import connect, init_schema
from kakao_import.payload import build_batch_manifest
from kakao_import.similar_cluster import (
    PhotoSig,
    cluster_similar_photos,
    hamming_hex,
    min_perceptual_distance,
)
from kakao_import.similar_detect import (
    ensure_similar_schema,
    list_similar_groups,
    rebuild_similar_groups,
    set_similar_group_decision,
)
from kakao_import.similar_policy import upload_policy_for_decision


def test_upload_policy_for_decision() -> None:
    assert upload_policy_for_decision("same_content") == "upload_representative"
    assert upload_policy_for_decision("different_content") == "upload_all_members"
    assert upload_policy_for_decision("partial") == "upload_partial"
    assert upload_policy_for_decision("deferred") == "upload_none"
    assert upload_policy_for_decision("") == "upload_none"


def test_hamming_and_min_distance() -> None:
    assert hamming_hex("00", "00") == 0
    assert hamming_hex("00", "01") == 1
    a = PhotoSig(1, "0000", "0000")
    b = PhotoSig(2, "0001", "00ff")  # dHash=1, pHash=큰값 → min=1
    assert min_perceptual_distance(a, b) == 1


def test_cluster_similar_within_distance() -> None:
    # 16-bit hex (4 chars) — 거리 계산만 검증
    photos = [
        PhotoSig(1, "0000", "0000", sha256="aaa"),
        PhotoSig(2, "0001", "0000", sha256="bbb"),  # dHash dist 1
        PhotoSig(3, "ffff", "ffff", sha256="ccc"),  # 멀음
    ]
    clusters = cluster_similar_photos(photos, max_distance=2)
    assert len(clusters) == 1
    assert clusters[0].photo_ids == (1, 2)
    assert clusters[0].representative_photo_id == 1


def test_cluster_skips_same_sha() -> None:
    photos = [
        PhotoSig(1, "0000", "0000", sha256="same"),
        PhotoSig(2, "0000", "0000", sha256="same"),
    ]
    assert cluster_similar_photos(photos, max_distance=10) == []
    # Exact 영역 제외 끄면 그룹 생김
    assert len(cluster_similar_photos(photos, max_distance=10, skip_same_sha=False)) == 1


def _insert_photo(conn, *, rel: str, sha: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO photo_file (rel_path, file_name, name_parse_ok, sha256)
        VALUES (?, ?, 1, ?)
        """,
        (rel, Path(rel).name, sha),
    )
    return int(cur.lastrowid)


def _insert_sig(conn, photo_id: int, dhash: str, phash: str, sha: str) -> None:
    conn.execute(
        """
        INSERT INTO photo_signature (
          photo_id, algo_version, dhash_hex, phash_hex, source_sha256
        ) VALUES (?, 'test', ?, ?, ?)
        """,
        (photo_id, dhash, phash, sha),
    )


def _settings(tmp_path: Path, db: Path) -> Settings:
    root = tmp_path / "raw"
    return Settings(
        export_root=root,
        db_path=db,
        log_level="INFO",
        match_tolerance_seconds=120,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
        merge_mode="balanced",
        similar_max_distance=10,
    )


def _insert_exact_group(conn, photo_id: int, sha: str) -> None:
    gid = conn.execute(
        """
        INSERT INTO exact_sha_group (sha256, representative_photo_id, member_count)
        VALUES (?, ?, 1)
        """,
        (sha, photo_id),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO exact_sha_member (group_id, photo_id, is_representative, excluded_from_upload)
        VALUES (?, ?, 1, 0)
        """,
        (gid, photo_id),
    )


def _insert_bundle_match_context(conn, *, batch_id: int, photo_ids: list[int]) -> None:
    chat_id = conn.execute(
        """
        INSERT INTO chat_source (
          rel_path, room_title, file_size, mtime_ns, content_sha256, encoding, last_batch_id
        ) VALUES ('chats/room.txt', 'room', 1, 1, 'chatsha', 'utf-8', ?)
        """,
        (batch_id,),
    ).lastrowid
    msg_id = conn.execute(
        """
        INSERT INTO parsed_message (
          chat_id, seq, msg_kind, sender, abs_time, body_raw, body_norm, photo_count, line_no
        ) VALUES (?, 1, 'text', '달콩', '2026-08-04T13:23:00', '[CASE-B] 화요 바차타 특강 안내', '[CASE-B] 화요 바차타 특강 안내', ?, 1)
        """,
        (chat_id, len(photo_ids)),
    ).lastrowid
    group_id = conn.execute(
        """
        INSERT INTO image_group (batch_id, chat_id, group_key, confidence, review_required, match_reason)
        VALUES (?, ?, 'bundle-1', 'high', 0, 'same_sender_same_minute')
        """,
        (batch_id, chat_id),
    ).lastrowid
    for idx, photo_id in enumerate(photo_ids):
        conn.execute(
            """
            INSERT INTO photo_message_assignment (
              batch_id, group_id, photo_id, message_id, slot_index, confidence, match_reason, review_required
            ) VALUES (?, ?, ?, ?, ?, 'high', 'bundle', 0)
            """,
            (batch_id, group_id, photo_id, msg_id, idx),
        )


def test_rebuild_omits_excluded_exact_duplicates(tmp_path: Path) -> None:
    """업로드 제외 SHA 복사본은 similar 그룹에 넣지 않는다."""
    db = tmp_path / "exact-hide.db"
    init_schema(db)
    with connect(db) as conn:
        ensure_similar_schema(conn)
        p1 = _insert_photo(conn, rel="photos/a.jpg", sha="same")
        p2 = _insert_photo(conn, rel="photos/a_copy.jpg", sha="same")
        p3 = _insert_photo(conn, rel="photos/b.jpg", sha="other")
        _insert_sig(conn, p1, "0000000000000000", "0000000000000000", "same")
        _insert_sig(conn, p2, "0000000000000000", "0000000000000000", "same")
        _insert_sig(conn, p3, "0000000000000001", "0000000000000000", "other")
        eg = conn.execute(
            "INSERT INTO exact_sha_group (sha256, representative_photo_id, member_count) "
            "VALUES ('same', ?, 2)",
            (p1,),
        ).lastrowid
        conn.execute(
            "INSERT INTO exact_sha_member (group_id, photo_id, is_representative, excluded_from_upload) "
            "VALUES (?, ?, 1, 0)",
            (eg, p1),
        )
        conn.execute(
            "INSERT INTO exact_sha_member (group_id, photo_id, is_representative, excluded_from_upload) "
            "VALUES (?, ?, 0, 1)",
            (eg, p2),
        )
        _insert_exact_group(conn, p3, "other")
        stats = rebuild_similar_groups(conn, max_distance=10)
        assert stats["skipped_exact"] == 1
        assert stats["groups"] == 1
        groups = list_similar_groups(conn)
        member_ids = {m["photo_id"] for m in groups[0]["members"]}
        assert member_ids == {p1, p3}
        assert p2 not in member_ids
        conn.commit()


def test_rebuild_hides_exact_only_pair(tmp_path: Path) -> None:
    """SHA가 같은 두 장만 있으면 similar 그룹을 만들지 않는다."""
    db = tmp_path / "exact-only.db"
    init_schema(db)
    with connect(db) as conn:
        ensure_similar_schema(conn)
        p1 = _insert_photo(conn, rel="photos/a.jpg", sha="same")
        p2 = _insert_photo(conn, rel="photos/a_copy.jpg", sha="same")
        _insert_sig(conn, p1, "0000000000000000", "0000000000000000", "same")
        _insert_sig(conn, p2, "0000000000000000", "0000000000000000", "same")
        eg = conn.execute(
            "INSERT INTO exact_sha_group (sha256, representative_photo_id, member_count) "
            "VALUES ('same', ?, 2)",
            (p1,),
        ).lastrowid
        conn.execute(
            "INSERT INTO exact_sha_member (group_id, photo_id, is_representative, excluded_from_upload) "
            "VALUES (?, ?, 1, 0)",
            (eg, p1),
        )
        conn.execute(
            "INSERT INTO exact_sha_member (group_id, photo_id, is_representative, excluded_from_upload) "
            "VALUES (?, ?, 0, 1)",
            (eg, p2),
        )
        stats = rebuild_similar_groups(conn, max_distance=10)
        assert stats["skipped_exact"] == 1
        assert stats["groups"] == 0
        assert list_similar_groups(conn) == []
        conn.commit()


def test_rebuild_and_decide_does_not_touch_exact_exclude(tmp_path: Path) -> None:
    """decision 저장은 exact_sha_member.excluded_from_upload 을 바꾸지 않음."""
    db = tmp_path / "t.db"
    init_schema(db)
    with connect(db) as conn:
        ensure_similar_schema(conn)
        p1 = _insert_photo(conn, rel="photos/a.jpg", sha="sha1")
        p2 = _insert_photo(conn, rel="photos/b.jpg", sha="sha2")
        _insert_sig(conn, p1, "0000000000000000", "0000000000000000", "sha1")
        _insert_sig(conn, p2, "0000000000000001", "0000000000000000", "sha2")
        # exact member stub — excluded=0 유지 검증용
        eg = conn.execute(
            "INSERT INTO exact_sha_group (sha256, representative_photo_id, member_count) "
            "VALUES ('sha1', ?, 1)",
            (p1,),
        ).lastrowid
        conn.execute(
            "INSERT INTO exact_sha_member (group_id, photo_id, is_representative, excluded_from_upload) "
            "VALUES (?, ?, 1, 0)",
            (eg, p1),
        )
        stats = rebuild_similar_groups(conn, max_distance=10)
        assert stats["groups"] == 1
        groups = list_similar_groups(conn)
        assert len(groups) == 1
        assert groups[0]["decision"] == "deferred"
        assert groups[0]["upload_policy"] == "upload_none"
        gid = groups[0]["group_id"]
        out = set_similar_group_decision(
            conn, group_id=gid, decision="same_content", representative_photo_id=p2
        )
        assert out["ok"] is True
        assert out["decision"] == "same_content"
        assert out["upload_policy"] == "upload_representative"
        assert "no auto-merge/delete" in out["note"]
        # upload 실행 필드 미변경
        ex = conn.execute(
            "SELECT excluded_from_upload FROM exact_sha_member WHERE photo_id = ?",
            (p1,),
        ).fetchone()
        assert int(ex["excluded_from_upload"]) == 0
        # 대표만 갱신
        g2 = list_similar_groups(conn)[0]
        assert g2["decision"] == "same_content"
        assert g2["representative_photo_id"] == p2
        reps = [m for m in g2["members"] if m["is_representative"]]
        assert len(reps) == 1 and reps[0]["photo_id"] == p2
        conn.commit()


def test_partial_subgroups_same_and_different(tmp_path: Path) -> None:
    """partial: 묶음+단독 저장, upload exclude 미변경."""
    db = tmp_path / "p.db"
    init_schema(db)
    with connect(db) as conn:
        ensure_similar_schema(conn)
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(similar_image_member)").fetchall()
        }
        assert "subgroup_key" in cols
        p1 = _insert_photo(conn, rel="photos/a.jpg", sha="a")
        p2 = _insert_photo(conn, rel="photos/b.jpg", sha="b")
        p3 = _insert_photo(conn, rel="photos/c.jpg", sha="c")
        for pid, sha in ((p1, "a"), (p2, "b"), (p3, "c")):
            _insert_sig(conn, pid, "0000000000000000", "0000000000000000", sha)
        rebuild_similar_groups(conn, max_distance=10)
        gid = list_similar_groups(conn)[0]["group_id"]
        out = set_similar_group_decision(
            conn,
            group_id=gid,
            decision="partial",
            subgroups=[
                {"photo_ids": [p1, p2], "representative_photo_id": p1},
                {"photo_ids": [p3]},
            ],
        )
        assert out["ok"] is True
        assert out["decision"] == "partial"
        assert out["upload_policy"] == "upload_partial"
        assert len(out["subgroups"]) == 2
        g = list_similar_groups(conn)[0]
        assert g["decision"] == "partial"
        assert len(g["subgroups"]) == 2
        multi = next(s for s in g["subgroups"] if not s["is_singleton"])
        solo = next(s for s in g["subgroups"] if s["is_singleton"])
        assert set(multi["photo_ids"]) == {p1, p2}
        assert multi["representative_photo_id"] == p1
        assert solo["photo_ids"] == [p3]
        # partial without multi-member fails
        try:
            set_similar_group_decision(
                conn,
                group_id=gid,
                decision="partial",
                subgroups=[{"photo_ids": [p1]}, {"photo_ids": [p2]}, {"photo_ids": [p3]}],
            )
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "multi-member" in str(exc)
        conn.commit()


def test_build_batch_manifest_same_content_keeps_representative_and_bundle_shape(
    tmp_path: Path,
) -> None:
    db = tmp_path / "same.db"
    init_schema(db)
    settings = _settings(tmp_path, db)
    photos = settings.export_root / "photos"
    photos.mkdir(parents=True)
    (photos / "a.jpg").write_bytes(b"a")
    (photos / "b.jpg").write_bytes(b"b")
    with connect(db) as conn:
        ensure_similar_schema(conn)
        batch_id = conn.execute(
            "INSERT INTO import_batch (root_rel, started_at, status) VALUES ('raw', datetime('now'), 'done')"
        ).lastrowid
        p1 = _insert_photo(conn, rel="photos/a.jpg", sha="a" * 64)
        p2 = _insert_photo(conn, rel="photos/b.jpg", sha="b" * 64)
        _insert_exact_group(conn, p1, "a" * 64)
        _insert_exact_group(conn, p2, "b" * 64)
        _insert_sig(conn, p1, "0000000000000000", "0000000000000000", "a" * 64)
        _insert_sig(conn, p2, "0000000000000001", "0000000000000000", "b" * 64)
        _insert_bundle_match_context(conn, batch_id=batch_id, photo_ids=[p1, p2])
        rebuild_similar_groups(conn, max_distance=10)
        gid = list_similar_groups(conn)[0]["group_id"]
        set_similar_group_decision(
            conn, group_id=gid, decision="same_content", representative_photo_id=p1
        )
        conn.commit()

    manifest = build_batch_manifest(settings)
    assert manifest["item_count"] == 1
    req = manifest["requests"][0]
    assert req["item"]["local_item_id"].startswith(f"photo:{p1}:")
    assert req["item"]["matched_messages"][0]["text"] == "[CASE-B] 화요 바차타 특강 안내"
    assert len(manifest["similar_policy"]["skipped"]) == 1
    assert manifest["similar_policy"]["skipped"][0]["reason"] == "similar_non_representative"
    assert len(manifest["grouped_photo_candidates"]) == 1
    assert manifest["grouped_photo_candidates"][0]["slot_count"] == 2
    assert manifest["grouped_photo_candidates"][0]["bundle_candidate"] is True


def test_build_batch_manifest_partial_keeps_subgroup_rep_and_singleton(
    tmp_path: Path,
) -> None:
    db = tmp_path / "partial.db"
    init_schema(db)
    settings = _settings(tmp_path, db)
    photos = settings.export_root / "photos"
    photos.mkdir(parents=True)
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (photos / name).write_bytes(name.encode())
    with connect(db) as conn:
        ensure_similar_schema(conn)
        p1 = _insert_photo(conn, rel="photos/a.jpg", sha="a" * 64)
        p2 = _insert_photo(conn, rel="photos/b.jpg", sha="b" * 64)
        p3 = _insert_photo(conn, rel="photos/c.jpg", sha="c" * 64)
        for pid, sha, dh in (
            (p1, "a" * 64, "0000000000000000"),
            (p2, "b" * 64, "0000000000000000"),
            (p3, "c" * 64, "0000000000000001"),
        ):
            _insert_exact_group(conn, pid, sha)
            _insert_sig(conn, pid, dh, "0000000000000000", sha)
        rebuild_similar_groups(conn, max_distance=10)
        gid = list_similar_groups(conn)[0]["group_id"]
        set_similar_group_decision(
            conn,
            group_id=gid,
            decision="partial",
            subgroups=[
                {"photo_ids": [p1, p2], "representative_photo_id": p1},
                {"photo_ids": [p3]},
            ],
        )
        conn.commit()

    manifest = build_batch_manifest(settings)
    kept_ids = [req["item"]["local_item_id"].split(":")[1] for req in manifest["requests"]]
    assert kept_ids == [str(p1), str(p3)]
    assert len(manifest["similar_policy"]["skipped"]) == 1
    assert manifest["similar_policy"]["skipped"][0]["reason"] == (
        "similar_partial_non_representative"
    )
