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
        match_tolerance_seconds=60,
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
    assert p.sequence == 0
    p2 = parse_kakaotalk_filename("KakaoTalk_20260724_005034512.PNG")
    assert p2.ok and p2.ext == "png" and p2.sequence == 0
    # [변경사유]: PC 앨범 `_01`/`_02` — 동일 name_time + sequence
    album = parse_kakaotalk_filename("KakaoTalk_20260804_161921080_01.png")
    assert album.ok
    assert album.name_time == datetime(2026, 8, 4, 16, 19, 21, 80000)
    assert album.sequence == 1
    album2 = parse_kakaotalk_filename("KakaoTalk_20260804_161921080_02.JPG")
    assert album2.ok and album2.sequence == 2 and album2.ext == "jpg"
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


def test_match_album_suffix_same_ms_orders_by_seq() -> None:
    """동일 name_time + `_01` sequence → slot 0=본파일, slot 1=_01."""
    t = datetime(2026, 8, 4, 16, 19, 21, 80000)
    messages = [
        {
            "id": 1,
            "chat_id": 1,
            "seq": 1,
            "msg_kind": "photo_multi",
            "sender": "S",
            "abs_time": "2026-08-04T16:19:00",
            "body_raw": "사진 2장",
            "body_norm": "사진 2장",
            "photo_count": 2,
        },
    ]
    # photo_id 역순으로 넣어도 name_seq로 정렬되어야 함
    photos = [
        PhotoSlot(861, "photos/a_01.png", t, name_seq=1),
        PhotoSlot(860, "photos/a.png", t, name_seq=0),
    ]
    out = match_photos_to_messages(
        photos=photos,
        messages=messages,
        tolerance_seconds=120,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
    )
    highs = sorted(
        [a for a in out.assignments if a.confidence == "high"],
        key=lambda a: a.slot_index,
    )
    assert len(highs) == 2
    assert highs[0].photo_id == 860 and highs[0].slot_index == 0
    assert highs[1].photo_id == 861 and highs[1].slot_index == 1
    assert len(out.groups) == 1 and int(out.groups[0]["slot_count"]) == 2


def test_match_count_mismatch_partial_assigns_available() -> None:
    """슬롯 1·파일 2 → 앞 파일 1장 부분 배정 + 남는 파일 unmatched + review."""
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
        {
            "id": 2,
            "chat_id": 1,
            "seq": 2,
            "msg_kind": "text",
            "sender": "S",
            "abs_time": "2026-07-24T01:01:00",
            "body_raw": "same caption for all",
            "body_norm": "same caption for all",
            "photo_count": None,
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
        group_text_before_max_seconds=120,
    )
    assigned = [a for a in out.assignments if a.confidence != "unmatched"]
    unmatched = [a for a in out.assignments if a.confidence == "unmatched"]
    assert len(assigned) == 1
    assert assigned[0].photo_id == 1
    assert assigned[0].match_reason == "partial_count_order"
    assert assigned[0].review_required is True
    assert len(unmatched) == 1 and unmatched[0].photo_id == 2
    assert any(r.get("kind") == "match_partial" for r in out.reviews)
    assert any(g.message_id == 2 for g in out.group_texts)


def test_match_partial_fewer_files_same_group_text() -> None:
    """슬롯 3·파일 2 → 2장 배정, 동일 group_text, 부족 슬롯 review."""
    messages = [
        {
            "id": 1,
            "chat_id": 1,
            "seq": 1,
            "msg_kind": "photo",
            "sender": "S",
            "abs_time": "2026-07-24T01:10:00",
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
            "abs_time": "2026-07-24T01:10:00",
            "body_raw": "사진",
            "body_norm": "사진",
            "photo_count": 1,
        },
        {
            "id": 3,
            "chat_id": 1,
            "seq": 3,
            "msg_kind": "photo",
            "sender": "S",
            "abs_time": "2026-07-24T01:10:00",
            "body_raw": "사진",
            "body_norm": "사진",
            "photo_count": 1,
        },
        {
            "id": 4,
            "chat_id": 1,
            "seq": 4,
            "msg_kind": "text",
            "sender": "S",
            "abs_time": "2026-07-24T01:10:30",
            "body_raw": "shared desc",
            "body_norm": "shared desc",
            "photo_count": None,
        },
    ]
    photos = [
        PhotoSlot(10, "a.png", datetime(2026, 7, 24, 1, 10, 1)),
        PhotoSlot(11, "b.png", datetime(2026, 7, 24, 1, 10, 2)),
    ]
    out = match_photos_to_messages(
        photos=photos,
        messages=messages,
        tolerance_seconds=120,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
        group_text_before_max_seconds=120,
    )
    assigned = [a for a in out.assignments if a.confidence != "unmatched"]
    assert len(assigned) == 2
    assert {a.group_key for a in assigned} == {assigned[0].group_key}
    assert all(a.match_reason == "partial_count_order" for a in assigned)
    assert [g.message_id for g in out.group_texts] == [4]
    assert any(r.get("kind") == "match_partial" and r.get("assigned") == 2 for r in out.reviews)


def test_match_preceding_text_within_2_minutes() -> None:
    """설명 먼저 → 사진(1분 뒤): 선행 텍스트 귀속."""
    messages = [
        {
            "id": 1,
            "chat_id": 1,
            "seq": 1,
            "msg_kind": "text",
            "sender": "indy",
            "abs_time": "2026-08-04T13:53:00",
            "body_raw": "이번주 금요일 수업 개강합니다",
            "body_norm": "이번주 금요일 수업 개강합니다",
            "photo_count": None,
        },
        {
            "id": 2,
            "chat_id": 1,
            "seq": 2,
            "msg_kind": "photo",
            "sender": "indy",
            "abs_time": "2026-08-04T13:54:00",
            "body_raw": "사진",
            "body_norm": "사진",
            "photo_count": 1,
        },
    ]
    photos = [PhotoSlot(1, "poster.png", datetime(2026, 8, 4, 13, 54, 5))]
    out = match_photos_to_messages(
        photos=photos,
        messages=messages,
        tolerance_seconds=120,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
        group_text_before_max_seconds=120,
    )
    highs = [a for a in out.assignments if a.confidence == "high"]
    assert len(highs) == 1
    assert [g.message_id for g in out.group_texts] == [1]


def test_match_preceding_text_over_2_minutes_skipped() -> None:
    """3분 전 텍스트는 선행 귀속하지 않음."""
    messages = [
        {
            "id": 1,
            "chat_id": 1,
            "seq": 1,
            "msg_kind": "text",
            "sender": "S",
            "abs_time": "2026-08-04T13:50:00",
            "body_raw": "too old",
            "body_norm": "too old",
            "photo_count": None,
        },
        {
            "id": 2,
            "chat_id": 1,
            "seq": 2,
            "msg_kind": "photo",
            "sender": "S",
            "abs_time": "2026-08-04T13:54:00",
            "body_raw": "사진",
            "body_norm": "사진",
            "photo_count": 1,
        },
    ]
    photos = [PhotoSlot(1, "p.png", datetime(2026, 8, 4, 13, 54, 1))]
    out = match_photos_to_messages(
        photos=photos,
        messages=messages,
        tolerance_seconds=120,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
        group_text_before_max_seconds=120,
    )
    assert out.group_texts == []


def test_match_multi_room_conflict() -> None:
    """같은 분·두 방 + 로컬 1장 → medium 배정 + multi_room review (포기 안 함)."""
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
    assigned = [a for a in out.assignments if a.confidence != "unmatched"]
    assert len(assigned) == 1
    assert assigned[0].photo_id == 1
    assert assigned[0].confidence == "medium"
    assert assigned[0].match_reason == "multi_room_caption_union"
    assert any("multi_room" in (r.get("reason") or "") for r in out.reviews)


def test_match_multi_room_unions_captions_from_all_rooms() -> None:
    """같은 분·두 방 서로 다른 캡션 → 로컬 1장에 양쪽 텍스트 message_id 모두 귀속."""
    messages = [
        {
            "id": 10,
            "chat_id": 77,
            "seq": 1,
            "msg_kind": "photo",
            "sender": "Grey",
            "abs_time": "2026-08-04T15:00:00",
            "body_raw": "사진",
            "body_norm": "사진",
            "photo_count": 1,
        },
        {
            "id": 11,
            "chat_id": 77,
            "seq": 2,
            "msg_kind": "text",
            "sender": "Grey",
            "abs_time": "2026-08-04T15:00:00",
            "body_raw": "BACHATA INFLUENCE room77",
            "body_norm": "bachata influence room77",
            "photo_count": None,
        },
        {
            "id": 20,
            "chat_id": 80,
            "seq": 1,
            "msg_kind": "photo",
            "sender": "황성민",
            "abs_time": "2026-08-04T15:00:00",
            "body_raw": "사진",
            "body_norm": "사진",
            "photo_count": 1,
        },
        {
            "id": 21,
            "chat_id": 80,
            "seq": 2,
            "msg_kind": "text",
            "sender": "황성민",
            "abs_time": "2026-08-04T15:00:00",
            "body_raw": "BACHATA INFLUENCE room80",
            "body_norm": "bachata influence room80",
            "photo_count": None,
        },
    ]
    photos = [PhotoSlot(854, "p.png", datetime(2026, 8, 4, 15, 0, 17))]
    out = match_photos_to_messages(
        photos=photos,
        messages=messages,
        tolerance_seconds=120,
        group_text_max_gap_minutes=30,
        different_sender_grace_seconds=120,
        different_sender_max_chars=80,
    )
    assigned = [a for a in out.assignments if a.confidence != "unmatched"]
    assert len(assigned) == 1
    gkey = assigned[0].group_key
    msg_ids = {gt.message_id for gt in out.group_texts if gt.group_key == gkey}
    assert msg_ids == {11, 21}


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
