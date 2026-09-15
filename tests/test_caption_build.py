# [변경사유]: 캡션 SQL 인덱스·배치 조회가 본문/앞뒤를 바꾸지 않는지 고정
"""caption_build 조회 최적화."""

from __future__ import annotations

from pathlib import Path

from kakao_import.caption_build import (
    build_caption_for_photos,
    build_captions_by_photo_ids,
    expand_exact_sha_photo_ids,
)
from kakao_import.caption_sep import CHAT_SPLIT_SEPARATOR
from kakao_import.db import connect, init_schema


def _insert_photo(conn, *, rel: str, sha: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO photo_file (rel_path, file_name, name_parse_ok, sha256)
        VALUES (?, ?, 1, ?)
        """,
        (rel, Path(rel).name, sha),
    )
    return int(cur.lastrowid)


def _setup_chat(conn, *, rel: str, title: str, batch_id: int) -> int:
    cur = conn.execute(
        """
        INSERT INTO chat_source (
          rel_path, room_title, file_size, mtime_ns, content_sha256, encoding, last_batch_id
        ) VALUES (?, ?, 1, 1, 'csha', 'utf-8', ?)
        """,
        (rel, title, batch_id),
    )
    return int(cur.lastrowid)


def _insert_msg(conn, chat_id: int, seq: int, body: str, *, kind: str = "text") -> int:
    cur = conn.execute(
        """
        INSERT INTO parsed_message (
          chat_id, seq, msg_kind, sender, abs_time, body_raw, body_norm, photo_count, line_no
        ) VALUES (?, ?, ?, '달콩', ?, ?, ?, 0, ?)
        """,
        (chat_id, seq, kind, f"2026-09-14T21:00:{seq:02d}", body, body, seq),
    )
    return int(cur.lastrowid)


def test_caption_before_after_and_indexes(tmp_path: Path) -> None:
    db = tmp_path / "cap.db"
    init_schema(db)
    with connect(db) as conn:
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_assign_photo'"
        ).fetchone()
        assert idx is not None
        batch_id = conn.execute(
            "INSERT INTO import_batch (root_rel, started_at, status) VALUES ('raw', datetime('now'), 'done')"
        ).lastrowid
        chat_id = _setup_chat(conn, rel="chats/room.txt", title="홍대", batch_id=batch_id)
        before_id = _insert_msg(conn, chat_id, 5, "앞글 안내")
        photo_msg = _insert_msg(conn, chat_id, 10, "사진", kind="photo")
        after_id = _insert_msg(conn, chat_id, 20, "뒷글 신청")
        photo_id = _insert_photo(conn, rel="photos/a.jpg", sha="a" * 64)
        gid = conn.execute(
            """
            INSERT INTO image_group (batch_id, chat_id, group_key, confidence, review_required, match_reason)
            VALUES (?, ?, 'g1', 'high', 0, 'test')
            """,
            (batch_id, chat_id),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO photo_message_assignment (
              batch_id, group_id, photo_id, message_id, slot_index, confidence, match_reason, review_required
            ) VALUES (?, ?, ?, ?, 0, 'high', 'test', 0)
            """,
            (batch_id, gid, photo_id, photo_msg),
        )
        conn.execute(
            "INSERT INTO group_text (group_id, message_id, seq_in_group) VALUES (?, ?, 1)",
            (gid, before_id),
        )
        conn.execute(
            "INSERT INTO group_text (group_id, message_id, seq_in_group) VALUES (?, ?, 2)",
            (gid, after_id),
        )
        text = build_caption_for_photos(conn, [photo_id])
        assert "앞글 안내" in text
        assert "뒷글 신청" in text
        assert CHAT_SPLIT_SEPARATOR in text
        assert "[단톡방: 홍대]" in text
        assert "[대화명: 달콩]" in text
        conn.commit()


def test_caption_empty_adjacent_keeps_room_and_sender(tmp_path: Path) -> None:
    # [변경사유]: 인접 본문이 없어도 단톡방+대화명으로 확인 가능
    db = tmp_path / "empty-cap.db"
    init_schema(db)
    with connect(db) as conn:
        batch_id = conn.execute(
            "INSERT INTO import_batch (root_rel, started_at, status) VALUES ('raw', datetime('now'), 'done')"
        ).lastrowid
        chat_id = _setup_chat(
            conn, rel="chats/room.txt", title="정보방!!!!!!!!!(전국라틴댄스)", batch_id=batch_id
        )
        photo_msg = _insert_msg(conn, chat_id, 10, "사진", kind="photo")
        photo_id = _insert_photo(conn, rel="photos/a.jpg", sha="a" * 64)
        gid = conn.execute(
            """
            INSERT INTO image_group (batch_id, chat_id, group_key, confidence, review_required, match_reason)
            VALUES (?, ?, 'g1', 'high', 0, 'test')
            """,
            (batch_id, chat_id),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO photo_message_assignment (
              batch_id, group_id, photo_id, message_id, slot_index, confidence, match_reason, review_required
            ) VALUES (?, ?, ?, ?, 0, 'high', 'test', 0)
            """,
            (batch_id, gid, photo_id, photo_msg),
        )
        text = build_caption_for_photos(conn, [photo_id])
        assert text == (
            "[단톡방: 정보방!!!!!!!!!(전국라틴댄스)]\n[대화명: 달콩]"
        )
        conn.commit()


def test_batch_captions_do_not_mix_groups(tmp_path: Path) -> None:
    db = tmp_path / "mix.db"
    init_schema(db)
    with connect(db) as conn:
        batch_id = conn.execute(
            "INSERT INTO import_batch (root_rel, started_at, status) VALUES ('raw', datetime('now'), 'done')"
        ).lastrowid
        chat_a = _setup_chat(conn, rel="chats/a.txt", title="A방", batch_id=batch_id)
        chat_b = _setup_chat(conn, rel="chats/b.txt", title="B방", batch_id=batch_id)
        msg_a = _insert_msg(conn, chat_a, 1, "에이만")
        msg_b = _insert_msg(conn, chat_b, 1, "비만")
        pa = _insert_photo(conn, rel="photos/a.jpg", sha="a" * 64)
        pb = _insert_photo(conn, rel="photos/b.jpg", sha="b" * 64)
        ga = conn.execute(
            """
            INSERT INTO image_group (batch_id, chat_id, group_key, confidence, review_required, match_reason)
            VALUES (?, ?, 'ga', 'high', 0, 't')
            """,
            (batch_id, chat_a),
        ).lastrowid
        gb = conn.execute(
            """
            INSERT INTO image_group (batch_id, chat_id, group_key, confidence, review_required, match_reason)
            VALUES (?, ?, 'gb', 'high', 0, 't')
            """,
            (batch_id, chat_b),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO photo_message_assignment (
              batch_id, group_id, photo_id, message_id, slot_index, confidence, match_reason, review_required
            ) VALUES (?, ?, ?, ?, 0, 'high', 't', 0)
            """,
            (batch_id, ga, pa, msg_a),
        )
        conn.execute(
            """
            INSERT INTO photo_message_assignment (
              batch_id, group_id, photo_id, message_id, slot_index, confidence, match_reason, review_required
            ) VALUES (?, ?, ?, ?, 0, 'high', 't', 0)
            """,
            (batch_id, gb, pb, msg_b),
        )
        conn.execute(
            "INSERT INTO group_text (group_id, message_id, seq_in_group) VALUES (?, ?, 1)",
            (ga, msg_a),
        )
        conn.execute(
            "INSERT INTO group_text (group_id, message_id, seq_in_group) VALUES (?, ?, 1)",
            (gb, msg_b),
        )
        batched = build_captions_by_photo_ids(conn, [pa, pb])
        one_a = build_caption_for_photos(conn, [pa])
        one_b = build_caption_for_photos(conn, [pb])
        assert batched[pa] == one_a
        assert batched[pb] == one_b
        assert "에이만" in batched[pa]
        assert "비만" not in batched[pa]
        assert "비만" in batched[pb]
        assert "에이만" not in batched[pb]
        conn.commit()


def test_exact_sha_sibling_caption_included(tmp_path: Path) -> None:
    db = tmp_path / "sha.db"
    init_schema(db)
    with connect(db) as conn:
        batch_id = conn.execute(
            "INSERT INTO import_batch (root_rel, started_at, status) VALUES ('raw', datetime('now'), 'done')"
        ).lastrowid
        chat1 = _setup_chat(conn, rel="chats/r1.txt", title="1방", batch_id=batch_id)
        chat2 = _setup_chat(conn, rel="chats/r2.txt", title="2방", batch_id=batch_id)
        m1 = _insert_msg(conn, chat1, 1, "첫째방글")
        m2 = _insert_msg(conn, chat2, 1, "둘째방글")
        p1 = _insert_photo(conn, rel="photos/c1.jpg", sha="c" * 64)
        p2 = _insert_photo(conn, rel="photos/c2.jpg", sha="c" * 64)
        g1 = conn.execute(
            """
            INSERT INTO image_group (batch_id, chat_id, group_key, confidence, review_required, match_reason)
            VALUES (?, ?, 'g1', 'high', 0, 't')
            """,
            (batch_id, chat1),
        ).lastrowid
        g2 = conn.execute(
            """
            INSERT INTO image_group (batch_id, chat_id, group_key, confidence, review_required, match_reason)
            VALUES (?, ?, 'g2', 'high', 0, 't')
            """,
            (batch_id, chat2),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO photo_message_assignment (
              batch_id, group_id, photo_id, message_id, slot_index, confidence, match_reason, review_required
            ) VALUES (?, ?, ?, ?, 0, 'high', 't', 0)
            """,
            (batch_id, g1, p1, m1),
        )
        conn.execute(
            """
            INSERT INTO photo_message_assignment (
              batch_id, group_id, photo_id, message_id, slot_index, confidence, match_reason, review_required
            ) VALUES (?, ?, ?, ?, 0, 'high', 't', 0)
            """,
            (batch_id, g2, p2, m2),
        )
        conn.execute(
            "INSERT INTO group_text (group_id, message_id, seq_in_group) VALUES (?, ?, 1)",
            (g1, m1),
        )
        conn.execute(
            "INSERT INTO group_text (group_id, message_id, seq_in_group) VALUES (?, ?, 1)",
            (g2, m2),
        )
        eg = conn.execute(
            "INSERT INTO exact_sha_group (sha256, representative_photo_id, member_count) VALUES (?, ?, 2)",
            ("c" * 64, p1),
        ).lastrowid
        conn.execute(
            "INSERT INTO exact_sha_member (group_id, photo_id, is_representative, excluded_from_upload) VALUES (?, ?, 1, 0)",
            (eg, p1),
        )
        conn.execute(
            "INSERT INTO exact_sha_member (group_id, photo_id, is_representative, excluded_from_upload) VALUES (?, ?, 0, 0)",
            (eg, p2),
        )
        expanded = expand_exact_sha_photo_ids(conn, [p1])
        assert set(expanded) == {p1, p2}
        text = build_caption_for_photos(conn, [p1])
        assert "첫째방글" in text
        assert "둘째방글" in text
        conn.commit()
