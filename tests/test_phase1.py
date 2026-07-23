# [변경사유]: Phase1 단위·golden 테스트
"""parser / time / photo_name / matcher / pipeline / golden."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from kakao_import.config import Settings
from kakao_import.db import connect, init_schema, status_counts
from kakao_import.encoding_util import read_text_with_encoding
from kakao_import.matcher import PhotoSlot, match_photos_to_messages
from kakao_import.parser import parse_chat_text
from kakao_import.photo_name import parse_kakaotalk_filename
from kakao_import.pipeline import cmd_hash, cmd_run, cmd_scan
from kakao_import.timeutil import combine_abs, korean_ampm_to_time

ROOT = Path(__file__).resolve().parents[1]
ESENCIA = ROOT / "fixtures" / "golden" / "esencia"


def _settings(tmp_path: Path, root: Path) -> Settings:
    return Settings(
        export_root=root,
        db_path=tmp_path / "t.db",
        log_level="WARNING",
        match_tolerance_seconds=120,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
        merge_mode="balanced",
    )


def test_ampm_noon_midnight() -> None:
    assert korean_ampm_to_time("오전", 12, 50).hour == 0
    assert korean_ampm_to_time("오후", 12, 0).hour == 12
    assert korean_ampm_to_time("오전", 1, 0).hour == 1
    assert korean_ampm_to_time("오후", 1, 0).hour == 13
    dt = combine_abs(datetime(2026, 7, 24).date(), "오전", 12, 50)
    assert dt == datetime(2026, 7, 24, 0, 50, 0)


def test_photo_name_ms() -> None:
    p = parse_kakaotalk_filename("KakaoTalk_20260724_005030533.png")
    assert p.ok
    assert p.name_time == datetime(2026, 7, 24, 0, 50, 30, 533000)
    p2 = parse_kakaotalk_filename("KakaoTalk_20260724_005034512.PNG")
    assert p2.ok and p2.ext == "png"
    bad = parse_kakaotalk_filename("random.jpg")
    assert not bad.ok and bad.error == "unparsed_photo"


def test_encoding_utf8_bom(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_bytes("\ufeff안녕하세요".encode("utf-8-sig"))
    text, enc = read_text_with_encoding(p)
    assert "안녕" in text
    assert enc in ("utf-8-sig", "utf-8")


def test_encoding_cp949(tmp_path: Path) -> None:
    p = tmp_path / "b.txt"
    p.write_bytes("카카오톡".encode("cp949"))
    text, enc = read_text_with_encoding(p)
    assert "카카오" in text
    assert enc == "cp949"


def test_parse_date_change_and_photo() -> None:
    text = """Room 님과 카카오톡 대화
--------------- 2026년 7월 23일 목요일 ---------------
[A] [오후 11:59] hi
--------------- 2026년 7월 24일 금요일 ---------------
[A] [오전 12:01] 사진
[A] [오전 12:02] next day text
"""
    r = parse_chat_text(text)
    assert r.messages[0].abs_time == datetime(2026, 7, 23, 23, 59, 0)
    assert r.messages[1].msg_kind == "photo"
    assert r.messages[1].abs_time == datetime(2026, 7, 24, 0, 1, 0)
    assert "next day" in r.messages[2].body_raw


def test_unparsed_line_kept() -> None:
    text = """Room 님과 카카오톡 대화
--------------- 2026년 1월 1일 목요일 ---------------
??? weird ???
[A] [오전 10:00] ok
"""
    r = parse_chat_text(text)
    assert any(e.error_code == "unparsed_line" for e in r.errors)
    assert any(m.body_raw == "ok" for m in r.messages)


def test_match_same_minute_order() -> None:
    messages = [
        {
            "id": 1,
            "chat_id": 1,
            "seq": 1,
            "msg_kind": "photo",
            "sender": "S",
            "abs_time": "2026-07-24T00:50:00",
            "body_raw": "사진",
            "body_norm": "사진",
            "photo_count": 1,
        },
        {
            "id": 2,
            "chat_id": 1,
            "seq": 2,
            "msg_kind": "photo",
            "sender": "S",
            "abs_time": "2026-07-24T00:50:00",
            "body_raw": "사진",
            "body_norm": "사진",
            "photo_count": 1,
        },
        {
            "id": 3,
            "chat_id": 1,
            "seq": 3,
            "msg_kind": "text",
            "sender": "S",
            "abs_time": "2026-07-24T00:53:00",
            "body_raw": "desc1",
            "body_norm": "desc1",
            "photo_count": None,
        },
    ]
    photos = [
        PhotoSlot(1, "photos/a.png", datetime(2026, 7, 24, 0, 50, 30, 533000)),
        PhotoSlot(2, "photos/b.png", datetime(2026, 7, 24, 0, 50, 34, 512000)),
    ]
    out = match_photos_to_messages(
        photos=photos,
        messages=messages,
        tolerance_seconds=120,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
    )
    highs = [a for a in out.assignments if a.confidence == "high"]
    assert len(highs) == 2
    assert highs[0].photo_id == 1 and highs[0].slot_index == 0
    assert highs[1].photo_id == 2 and highs[1].slot_index == 1
    assert not highs[0].review_required
    assert len(out.group_texts) >= 1


def test_match_count_mismatch_review() -> None:
    messages = [
        {
            "id": 1,
            "chat_id": 1,
            "seq": 1,
            "msg_kind": "photo",
            "sender": "S",
            "abs_time": "2026-07-24T01:00:00",
            "body_raw": "사진",
            "body_norm": "사진",
            "photo_count": 1,
        },
    ]
    photos = [
        PhotoSlot(1, "p1.png", datetime(2026, 7, 24, 1, 0, 1)),
        PhotoSlot(2, "p2.png", datetime(2026, 7, 24, 1, 0, 2)),
    ]
    out = match_photos_to_messages(
        photos=photos,
        messages=messages,
        tolerance_seconds=120,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
    )
    assert any(r.get("kind") == "match_ambiguous" for r in out.reviews)


def test_match_multi_room_conflict() -> None:
    messages = [
        {
            "id": 1,
            "chat_id": 1,
            "seq": 1,
            "msg_kind": "photo",
            "sender": "A",
            "abs_time": "2026-07-24T02:00:00",
            "body_raw": "사진",
            "body_norm": "사진",
            "photo_count": 1,
        },
        {
            "id": 2,
            "chat_id": 2,
            "seq": 1,
            "msg_kind": "photo",
            "sender": "B",
            "abs_time": "2026-07-24T02:00:00",
            "body_raw": "사진",
            "body_norm": "사진",
            "photo_count": 1,
        },
    ]
    photos = [PhotoSlot(1, "p.png", datetime(2026, 7, 24, 2, 0, 5))]
    out = match_photos_to_messages(
        photos=photos,
        messages=messages,
        tolerance_seconds=120,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
    )
    assert any("multi_room" in (r.get("reason") or "") for r in out.reviews)


def test_group_text_stops_on_other_sender() -> None:
    messages = [
        {
            "id": 1,
            "chat_id": 1,
            "seq": 1,
            "msg_kind": "photo",
            "sender": "S",
            "abs_time": "2026-07-24T03:00:00",
            "body_raw": "사진",
            "body_norm": "사진",
            "photo_count": 1,
        },
        {
            "id": 2,
            "chat_id": 1,
            "seq": 2,
            "msg_kind": "text",
            "sender": "S",
            "abs_time": "2026-07-24T03:01:00",
            "body_raw": "mine",
            "body_norm": "mine",
            "photo_count": None,
        },
        {
            "id": 3,
            "chat_id": 1,
            "seq": 3,
            "msg_kind": "text",
            "sender": "Other",
            "abs_time": "2026-07-24T03:01:30",
            "body_raw": "other short",
            "body_norm": "other short",
            "photo_count": None,
        },
        {
            "id": 4,
            "chat_id": 1,
            "seq": 4,
            "msg_kind": "text",
            "sender": "S",
            "abs_time": "2026-07-24T03:02:00",
            "body_raw": "after other",
            "body_norm": "after other",
            "photo_count": None,
        },
    ]
    photos = [PhotoSlot(1, "p.png", datetime(2026, 7, 24, 3, 0, 1))]
    out = match_photos_to_messages(
        photos=photos,
        messages=messages,
        tolerance_seconds=120,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
    )
    ids = [g.message_id for g in out.group_texts]
    assert 2 in ids
    assert 3 not in ids
    assert 4 not in ids


def test_idempotent_rerun(tmp_path: Path) -> None:
    assert ESENCIA.is_dir()
    s = _settings(tmp_path, ESENCIA)
    init_schema(s.db_path, reset=True)
    cmd_run(s, ESENCIA)
    c1 = status_counts(s.db_path)
    cmd_run(s, ESENCIA)
    c2 = status_counts(s.db_path)
    assert c1["parsed_message"] == c2["parsed_message"]
    assert c1["photo_file"] == c2["photo_file"]


def test_sha_exact_and_original_untouched(tmp_path: Path) -> None:
    s = _settings(tmp_path, ESENCIA)
    init_schema(s.db_path, reset=True)
    p = ESENCIA / "photos" / "KakaoTalk_20260724_005030533.png"
    before = p.read_bytes()
    cmd_scan(s, ESENCIA)
    cmd_hash(s, ESENCIA)
    assert p.read_bytes() == before
    with connect(s.db_path) as conn:
        n = conn.execute("SELECT COUNT(*) c FROM exact_sha_group").fetchone()["c"]
        assert n >= 1


def test_esencia_golden(tmp_path: Path) -> None:
    """Phase1 필수 golden."""
    s = _settings(tmp_path, ESENCIA)
    init_schema(s.db_path, reset=True)
    out = cmd_run(s, ESENCIA)
    eg = out["report"]["esencia_golden"]
    assert eg["passed"] is True, eg
    assert eg["group_text_count"] >= 2
